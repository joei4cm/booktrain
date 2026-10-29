"""网页端（booktran serve）：FastAPI + 单页前端。

设计原则：薄封装——所有逻辑复用 pipeline/store，web 层只做
项目发现/创建、状态查询、后台任务管理、段落/术语/配置编辑。
"""
# 注意：本模块不加 `from __future__ import annotations` ——
# 它会把注解变成字符串，FastAPI/pydantic 解析不了 UploadFile 等类型。
import asyncio
import json
import re
import shutil
from pathlib import Path

from pydantic import BaseModel

from ..config import (
    AppConfig,
    apply_platform,
    dump_toml,
    load_config,
    load_project_manifest,
    write_project_config,
)
from ..glossary import Glossary
from ..llm.usage import UsageLog
from ..model import Status
from ..pipeline import Progress, make_router, run_pipeline
from ..project import SUPPORTED_EXTS, init_project, iter_source_files, read_docs
from ..store import Store

# 请求模型必须在模块级（闭包内局部类 + `from __future__ import annotations`
# 会导致 FastAPI 无法解析字符串注解，body 被静默忽略）


class TranslateReq(BaseModel):
    source_lang: str | None = None
    target_lang: str | None = None
    target_langs: list[str] | str | None = None
    chunk_budget_tokens: int | None = None
    rounds: int | None = None


class SegmentReq(BaseModel):
    target: str
    lang: str | None = None


class ExportReq(BaseModel):
    out: str | None = None
    bilingual: bool = False
    format: str = "txt"
    langs: list[str] | str | None = None


class ConfigReq(BaseModel):
    """网页端设置表单：出现的字段才覆盖（与命令行一致）。"""
    # 翻译
    source_lang: str | None = None
    target_langs: list[str] | str | None = None
    chunk_budget_tokens: int | None = None
    rounds: int | None = None
    zh_convert: str | None = None
    layout: str | None = None
    domain: str | None = None
    # 角色绑定：项目覆盖全局（只写引用）
    role_profile: str | None = None
    roles: dict[str, str] | None = None      # 角色 → 档案（项目级覆盖）
    budget: dict | None = None                # {max_cost, max_tokens}
    # 密钥：写入**全局** keyring（项目目录永不存密钥）
    provider: str | None = None
    api_key: str | None = None


class AnalyzeReq(BaseModel):
    llm_merge: bool = True


class EventBus:
    """SSE 事件总线：把作业事件广播给所有订阅者。"""

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    async def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass          # 慢消费者：丢弃而不是拖住作业


class ProjectHandle:
    """一个已加载的项目及其运行状态。

    v0.5：配置是**引用**——项目只存 booktran.toml 的差异，模型档案在全局
    profiles.toml；密钥只在全局 keyring（项目目录永不存密钥）。
    """

    def __init__(self, project_dir: Path) -> None:
        self.dir = project_dir
        self.manifest = load_project_manifest(project_dir) if (project_dir / "project.json").exists() else {}
        self.cfg = load_config(project_dir)
        self.cfg.ensure_langs()
        self.store = Store(project_dir)
        self.usage = UsageLog(project_dir)
        self.docs, self.stats = self.store.load(default_lang=self.cfg.tr.target_lang)
        for d in self.docs:
            for s in d.segments:
                s.ensure_tracks(self.cfg.tr.target_langs)
        self.langs: list[str] = list(self.cfg.tr.target_langs)
        self.task: asyncio.Task | None = None
        self.task_state = "idle"          # idle | running | done | error
        self.task_message = ""
        self.task_kind = ""               # translate | analyze | read
        self.events: list[dict] = []      # 最近事件（兼容旧的轮询接口）
        self.bus = EventBus()             # SSE 广播
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()      # 取消信号（优雅暂停）

    # ---------- 数据 ----------
    def summary(self) -> dict:
        excluded = sum(1 for d in self.docs for s in d.segments if s.status == Status.EXCLUDED)
        total = sum(1 for d in self.docs for s in d.segments if s.status != Status.EXCLUDED)
        per_lang = {}
        for lang in self.langs:
            done = sum(1 for d in self.docs for s in d.segments if s.is_done(lang))
            errored = sum(1 for d in self.docs for s in d.segments
                          if s.status != Status.EXCLUDED and not s.is_done(lang)
                          and s.track(lang, create=False).error)
            per_lang[lang] = {
                "total": total, "translated": done,
                "pending": total - done, "errored": errored,
                "percent": round(done / max(1, total) * 100, 1),
            }
        done_any = sum(1 for d in self.docs for s in d.segments if s.is_done())
        usage = self.usage.aggregate()
        return {
            "id": self.dir.name,
            "path": str(self.dir),
            "source": self.manifest.get("source", ""),
            "created_at": self.manifest.get("created_at", ""),
            "target_langs": self.langs,
            "target_lang": self.cfg.tr.target_lang,
            "source_lang": self.cfg.tr.source_lang,
            "provider": self.cfg.llm.provider,
            "model": self.cfg.llm.model,
            "roles": {name: spec.target for name, spec in self.cfg.roles.items()},
            "docs": [
                {
                    "relpath": d.relpath, "fmt": d.fmt,
                    "total": len(d.segments),
                    "translated": sum(1 for s in d.segments if s.is_done()),
                    "excluded": sum(1 for s in d.segments if s.status == Status.EXCLUDED),
                } for d in self.docs
            ],
            "progress": {
                "total": total, "translated": done_any,
                "excluded": excluded, "pending": total - done_any,
                "errored": sum(v["errored"] for v in per_lang.values()),
                "percent": round(done_any / max(1, total) * 100, 1),
            },
            "per_lang": per_lang,
            "stats": self.stats.to_dict(),
            "cost": usage["total"],
            "budget": self.cfg.budget.to_dict(),
            "task": {"state": self.task_state, "message": self.task_message},
        }

    def glossary_path(self) -> Path:
        return self.dir / "glossary.json"

    def load_glossary(self) -> Glossary:
        return Glossary.load(self.glossary_path())

    def save_glossary(self, data: dict) -> None:
        # 经 Glossary 归一化后落盘（background 字段名规整；terms 内 dst_<lang> 原样保留）
        g = Glossary(
            terms=data.get("terms") or [],
            non_translate=data.get("non_translate") or [],
            style=data.get("style", ""),
            notes=data.get("notes", ""),
            system_prompt=data.get("system_prompt", ""),
            background=data.get("background"),
        )
        g.save(self.glossary_path())

    def reload(self) -> None:
        self.cfg.ensure_langs()
        self.langs = list(self.cfg.tr.target_langs)
        self.docs, self.stats = self.store.load(default_lang=self.cfg.tr.target_lang)
        for d in self.docs:
            for s in d.segments:
                s.ensure_tracks(self.langs)

    # ---------- 设置 ----------
    def settings(self) -> dict:
        """当前设置（供前端表单回填）。密钥只回传是否已设置。"""
        llm = self.cfg.llm
        tr = self.cfg.tr
        return {
            "llm": {
                "provider": llm.provider,
                "model": llm.model,
                "base_url": llm.base_url,
                "has_api_key": bool(llm.api_key),
                "api_key_count": len(llm.api_keys),
                "concurrency": llm.concurrency,
                "rpm": llm.rpm,
                "max_output_tokens": llm.max_output_tokens,
                "prompt_preset": llm.prompt_preset,
            },
            "translate": {
                "source_lang": tr.source_lang,
                "target_langs": list(tr.target_langs),
                "chunk_budget_tokens": tr.chunk_budget_tokens,
                "rounds": tr.rounds,
                "zh_convert": tr.zh_convert,
                "layout": tr.layout,
            },
            "platform": self.cfg.active_platform,
            "platforms": sorted(self.cfg.platforms.keys()),
            "profiles": self.profiles_view(),
            "roles": {name: spec.to_dict() for name, spec in self.cfg.roles.items()},
            "budget": self.cfg.budget.to_dict(),
        }

    def profiles_view(self) -> list[dict]:
        """模型档案视图（含价格与密钥状态，供"模型"面板使用）。"""
        from ..config.keyring import key_source
        out = []
        for pid, p in sorted(self.cfg.profiles.items()):
            out.append({
                "id": pid, "kind": p.kind, "provider": p.provider, "model": p.model,
                "base_url": p.base_url, "caps": sorted(p.caps),
                "price_in": p.price_in, "price_out": p.price_out,
                "key_env": p.key_env, "key_state": key_source(p),
            })
        return out

    def apply_settings(self, body: ConfigReq) -> dict:
        """把表单设置写进**项目差异文件** booktran.toml（模型配置仍属全局档案）。"""
        cfg = self.cfg
        if body.target_langs:
            tl = body.target_langs
            if isinstance(tl, str):
                tl = [x.strip() for x in tl.split(",") if x.strip()]
            if tl:
                cfg.tr.target_langs = list(tl)
        for key in ("source_lang", "chunk_budget_tokens", "rounds", "zh_convert", "layout", "domain"):
            val = getattr(body, key, None)
            if val is not None and val != "":
                setattr(cfg.tr, key, val)
        # 角色绑定：项目可以覆盖全局（只写引用）
        roles = {name: spec.target for name, spec in cfg.roles.items() if spec.target}
        if body.role_profile:
            roles["translate"] = body.role_profile
        if body.roles:
            roles.update({k: v for k, v in body.roles.items() if v})
        if body.budget:
            from ..config.models import Budget
            cfg.budget = Budget.from_dict(body.budget)
        # 项目差异落盘（引用式）
        write_project_config(self.dir, cfg, roles=roles, budget=cfg.budget)
        # 密钥：写全局 keyring（项目目录永不存密钥）
        if body.api_key:
            self._save_global_key(body.provider or cfg.llm.provider, body.api_key)
            cfg.llm.api_key = body.api_key
        cfg.ensure_langs()
        self.langs = list(cfg.tr.target_langs)
        for d in self.docs:
            for s in d.segments:
                s.ensure_tracks(self.langs)
        return self.settings()

    # ---------- 密钥（全局，不落项目） ----------
    def _keys_path(self) -> Path:
        from ..config import keys_path
        return keys_path()

    def _save_global_key(self, provider: str, api_key: str) -> None:
        """把密钥写进全局 keys.toml（0600）。项目目录里不写任何密钥。"""
        import tomllib
        path = self._keys_path()
        data: dict = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text("utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                data = {}
        key_section = data.setdefault("key", {})
        key_section[provider] = {"api_key": api_key}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_toml(data), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def segments_page(self, relpath: str | None, offset: int, limit: int) -> dict:
        for d in self.docs:
            if relpath and d.relpath != relpath:
                continue
            segs = d.segments
            page = segs[offset:offset + limit]
            return {
                "doc": d.relpath, "fmt": d.fmt, "total": len(segs),
                "offset": offset,
                "langs": self.langs,
                "items": [
                    {
                        "sid": s.sid, "source": s.source,
                        "status": s.overall_status.value,
                        "source_lang": s.meta.get("source_lang", ""),
                        "tracks": {lang: t.to_dict() for lang, t in s.tracks.items()},
                        "meta": {k: v for k, v in s.meta.items() if k != "original_html"},
                    } for s in page
                ],
            }
        return {"doc": relpath, "fmt": None, "total": 0, "offset": 0,
                "langs": self.langs, "items": []}

    def update_segment(self, sid: str, target: str, lang: str = "") -> bool:
        lang = lang or self.cfg.tr.target_lang
        for d in self.docs:
            for s in d.segments:
                if s.sid == sid:
                    t = s.track(lang)
                    t.target = target
                    t.status = Status.TRANSLATED if target.strip() else Status.PENDING
                    t.error = None
                    t.model = t.model or "manual"
                    self.store.append_event(s, lang)
                    self.store.save_snapshot(self.docs, self.stats)
                    return True
        return False

    # ---------- 任务 ----------
    async def start_translate(self, overrides: dict | None = None) -> dict:
        async with self._lock:
            if self.task and not self.task.done():
                return {"ok": False, "message": "任务已在运行"}
            self.task_state = "running"
            self.task_message = ""
            self.events.clear()
            self.task = asyncio.create_task(self._run_translate(overrides or {}))
            return {"ok": True}

    async def _run_translate(self, overrides: dict) -> None:
        cfg = self.cfg
        for key in ("source_lang", "chunk_budget_tokens", "rounds"):
            if overrides.get(key) not in (None, "", 0):
                setattr(cfg.tr, key, overrides[key])
        tl = overrides.get("target_langs")
        if isinstance(tl, str):
            tl = [x.strip() for x in tl.split(",") if x.strip()]
        if tl:
            cfg.tr.target_langs = list(tl)
        elif overrides.get("target_lang"):
            cfg.tr.target_langs = [overrides["target_lang"]]
        cfg.ensure_langs()
        self.langs = list(cfg.tr.target_langs)
        glossary = self.load_glossary()
        progress = Progress()
        progress.add_listener(self._record_event)
        self._stop = asyncio.Event()
        router = make_router(cfg, None, usage_log=self.usage)
        try:
            await run_pipeline(self.docs, None, cfg, self.store, self.stats, glossary,
                               progress, self._stop, router=router, usage_log=self.usage)
            if self._stop.is_set():
                self.task_state = "done"
                self.task_message = "已暂停（可继续）"
            else:
                self.task_state = "done"
                self.task_message = "翻译完成"
        except Exception as e:  # noqa: BLE001
            self.task_state = "error"
            self.task_message = str(e)
        finally:
            await router.aclose()
            self.reload()

    async def start_proofread(self, mode: str = "qa") -> dict:
        """只跑校对阶段（qa 不花钱；llm 会调用模型）。"""
        async with self._lock:
            if self.task and not self.task.done():
                return {"ok": False, "message": "任务已在运行"}
            self.task_state = "running"
            self.task_kind = "proofread"
            self.task_message = "质检中…"
            self.events.clear()
            self.task = asyncio.create_task(self._run_proofread(mode))
            return {"ok": True}

    async def _run_proofread(self, mode: str) -> None:
        from ..glossary import Glossary as _G
        cfg = self.cfg
        router = make_router(cfg, None, usage_log=self.usage)
        progress = Progress()
        progress.add_listener(self._record_event)
        try:
            await run_pipeline(self.docs, None, cfg, self.store, self.stats,
                               _G.load(self.dir / "glossary.json"), progress, None,
                               router=router, usage_log=self.usage,
                               stages=["proofread"], proofread_mode=mode,
                               project_dir=self.dir)
            self.task_state = "done"
            self.task_message = "质检完成"
        except Exception as e:  # noqa: BLE001
            self.task_state = "error"
            self.task_message = str(e)
        finally:
            await router.aclose()
            self.reload()

    def revert_segment(self, sid: str, lang: str) -> dict:
        """回退到上一版修订（revisions 里记的是 old→new，回退取 old）。"""
        for doc in self.docs:
            for seg in doc.segments:
                if seg.sid == sid:
                    tr = seg.track(lang, create=False)
                    if tr is None or not tr.revisions:
                        return {"ok": False, "message": "该段没有修订历史"}
                    last = tr.revisions[-1]
                    prev = last.get("old") or ""
                    tr.revisions.append({
                        "at": __import__("time").time(),
                        "old": tr.target, "new": prev,
                        "source": "revert"})
                    tr.target = prev
                    tr.status = Status.TRANSLATED if prev.strip() else Status.PENDING
                    tr.error = None
                    self.store.append_event(seg, lang)
                    self.store.save_snapshot(self.docs, self.stats)
                    return {"ok": True, "target": prev}
        return {"ok": False, "message": "段落不存在"}

    def cancel(self) -> dict:
        """优雅暂停：请求停止信号，作业在当前块结束后停下并落盘。"""
        if self.task_state != "running":
            return {"ok": False, "message": "当前没有正在运行的作业"}
        self._stop.set()
        self.task_message = "正在停止…"
        return {"ok": True, "message": "已请求停止，当前块结束后保存"}

    async def _record_event(self, event: dict) -> None:
        self.events.append(event)
        if len(self.events) > 200:
            self.events = self.events[-200:]
        await self.bus.publish(event)

    # ---------- 术语分析 ----------
    async def start_analyze(self, llm_merge: bool = True) -> dict:
        async with self._lock:
            if self.task and not self.task.done():
                return {"ok": False, "message": "任务已在运行"}
            self.task_state = "running"
            self.task_kind = "analyze"
            self.task_message = "分析中…"
            self.events.clear()
            self.task = asyncio.create_task(self._run_analyze(llm_merge))
            return {"ok": True}

    async def _run_analyze(self, llm_merge: bool) -> None:
        from ..config import resolve_task_platform
        from ..glossary import analyze_project
        cfg = self.cfg
        name = resolve_task_platform(cfg, "analyze")
        if name:
            try:
                apply_platform(cfg, name)
            except SystemExit as e:
                self.task_state = "error"
                self.task_message = str(e)
                return
        router = make_router(cfg, None, usage_log=self.usage)
        try:
            glossary = await analyze_project(self.docs, router, cfg.llm.concurrency,
                                             llm_merge=llm_merge)
            # 保留人工填过的译名与背景
            old = self.load_glossary()
            old_by_src = {t.get("src"): t for t in old.terms}
            for t in glossary.terms:
                prev = old_by_src.get(t.get("src"))
                if prev:
                    for k, v in prev.items():
                        if k.startswith("dst") and v:
                            t[k] = v
                    if prev.get("note"):
                        t["note"] = prev["note"]
            glossary.background = old.background
            glossary.system_prompt = old.system_prompt or glossary.system_prompt
            glossary.style = old.style or glossary.style
            glossary.notes = old.notes or glossary.notes
            if old.non_translate:
                glossary.non_translate = old.non_translate
            glossary.save(self.glossary_path())
            self.task_state = "done"
            self.task_message = f"术语分析完成：{len(glossary.terms)} 条候选"
        except Exception as e:  # noqa: BLE001
            self.task_state = "error"
            self.task_message = str(e)
        finally:
            await router.aclose()

    # ---------- VLM 重读（扫描页识别） ----------
    async def start_vlm_read(self, dpi: int = 150) -> dict:
        async with self._lock:
            if self.task and not self.task.done():
                return {"ok": False, "message": "任务已在运行"}
            self.task_state = "running"
            self.task_kind = "read"
            self.task_message = "多模态识别中…"
            self.events.clear()
            self.task = asyncio.create_task(self._run_vlm_read(dpi))
            return {"ok": True}

    async def _run_vlm_read(self, dpi: int) -> None:
        source = Path(self.manifest["source"])
        try:
            files = iter_source_files(source)
        except SystemExit as e:
            self.task_state = "error"
            self.task_message = str(e)
            return
        root = source if source.is_dir() else source.parent
        try:
            docs = await read_docs(files, root, vlm=True, vlm_cfg=self.cfg, vlm_dpi=dpi)
            # 识别会重新切段；sid 相同的段保留已有译文
            old = {s.sid: s for d in self.docs for s in d.segments}
            for doc in docs:
                for s in doc.segments:
                    s.ensure_tracks(self.langs)
                    prev = old.get(s.sid)
                    if prev:
                        for lang, t in prev.tracks.items():
                            if t.status == Status.TRANSLATED:
                                s.track(lang).target = t.target
                                s.track(lang).status = Status.TRANSLATED
                                s.track(lang).model = t.model
            self.docs = docs
            self.store.save_snapshot(docs, self.stats)
            total = sum(len(d.segments) for d in docs)
            self.task_state = "done"
            self.task_message = f"识别完成：{total} 段"
        except Exception as e:  # noqa: BLE001
            self.task_state = "error"
            self.task_message = str(e)
        finally:
            self.reload()

    def export(self, out: str | None, bilingual: bool, fmt: str,
               langs: list[str] | None = None) -> list[str]:
        from ..adapters import WRITERS
        from ..prompt.builder import lang_code

        source_root = Path(self.manifest["source"])
        out_root = Path(out).expanduser() if out else self.dir / "output"
        langs = langs or self.langs or [self.cfg.tr.target_lang]
        written = []
        for lang in langs:
            suffix = lang_code(lang)
            for doc in self.docs:
                writer = WRITERS[doc.fmt]()
                src = source_root / doc.relpath if source_root.is_dir() else source_root
                if doc.fmt == "pdf":
                    ext = {"epub": ".epub", "pdf": ".pdf"}.get(fmt, ".txt")
                else:
                    ext = src.suffix
                out_path = out_root / f"{src.stem}_{suffix}{ext}"
                if doc.fmt == "pdf" and fmt == "pdf":
                    from ..pdf_layout import PdfLayoutWriter
                    PdfLayoutWriter().write(doc, src, out_path, bilingual=False, lang=lang,
                                            target_lang_code=suffix)
                    written.append(str(out_path))
                    if bilingual:
                        bi = out_root / f"{src.stem}_{suffix}_bilingual{ext}"
                        PdfLayoutWriter().write(doc, src, bi, bilingual=True, lang=lang,
                                                target_lang_code=suffix)
                        written.append(str(bi))
                    continue
                if doc.fmt == "pdf" and fmt == "epub":
                    writer.write_epub(doc, src, out_path, lang=lang)
                else:
                    writer.write(doc, src, out_path, bilingual=False, lang=lang,
                                 target_lang_code=suffix)
                written.append(str(out_path))
                if bilingual and not (doc.fmt == "pdf" and fmt == "epub"):
                    bi = out_root / f"{src.stem}_{suffix}_bilingual{ext}"
                    writer.write(doc, src, bi, bilingual=True, lang=lang,
                                 target_lang_code=suffix)
                    written.append(str(bi))
        return written


class ProjectRegistry:
    """扫描 projects_root 下的项目目录。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._handles: dict[str, ProjectHandle] = {}

    def discover(self) -> list[Path]:
        dirs = []
        if not self.root.exists():
            return dirs
        for entry in sorted(self.root.iterdir()):
            if entry.is_dir() and (entry / "project.json").exists():
                dirs.append(entry)
        return dirs

    def get(self, project_id: str) -> ProjectHandle:
        # project_id = 目录名；也接受绝对路径
        p = Path(project_id)
        if p.is_absolute() or "/" in project_id:
            p = p if p.is_absolute() else (self.root / project_id)
        else:
            p = self.root / project_id
        if not (p / "project.json").exists():
            raise KeyError(project_id)
        key = str(p.resolve())
        if key not in self._handles:
            self._handles[key] = ProjectHandle(p)
        return self._handles[key]

    def reload(self, project_id: str) -> ProjectHandle:
        p = Path(project_id)
        if not p.is_absolute():
            p = self.root / project_id
        self._handles.pop(str(p.resolve()), None)
        return self.get(project_id)

    # ---------- 创建 / 删除 ----------
    def unique_dir(self, stem: str) -> Path:
        """生成不冲突的项目目录名（booktran-<stem> / -2 / -3 …）。"""
        safe = re.sub(r"[^\w一-鿿.-]+", "-", stem).strip("-.") or "book"
        d = self.root / f"booktran-{safe}"
        i = 2
        while d.exists():
            d = self.root / f"booktran-{safe}-{i}"
            i += 1
        return d

    async def create(self, uploaded_name: str, data: bytes, cfg: AppConfig,
                     vlm: bool = False, vlm_dpi: int = 150) -> ProjectHandle:
        """把上传的文件建成项目（源文件存进项目目录的 source/）。"""
        suffix = Path(uploaded_name).suffix.lower()
        if suffix not in SUPPORTED_EXTS:
            raise ValueError(f"不支持的文件类型 {suffix or '(无扩展名)'}（支持 {'/'.join(SUPPORTED_EXTS)}）")
        project_dir = self.unique_dir(Path(uploaded_name).stem)
        project_dir.mkdir(parents=True)
        src_dir = project_dir / "source"
        src_dir.mkdir()
        src = src_dir / Path(uploaded_name).name
        src.write_bytes(data)
        if vlm:
            docs = await read_docs([src], src_dir, vlm=True, vlm_cfg=cfg, vlm_dpi=vlm_dpi)
            init_project(src, project_dir, cfg, docs=docs)
        else:
            # 同步读取放到线程里，别阻塞事件循环（大 PDF 解析要几秒）
            await asyncio.to_thread(init_project, src, project_dir, cfg, None, [src])
        # 项目只写引用与差异：模型档案属全局，这里只记角色与语言
        write_project_config(project_dir, cfg,
                             roles={"translate": cfg.active_platform} if cfg.active_platform else {})
        handle = ProjectHandle(project_dir)
        self._handles[str(project_dir.resolve())] = handle
        return handle

    def delete(self, project_id: str) -> None:
        p = Path(project_id)
        if not p.is_absolute():
            p = self.root / project_id
        if not (p / "project.json").exists():
            raise KeyError(project_id)
        self._handles.pop(str(p.resolve()), None)
        shutil.rmtree(p)


def build_app(projects_root: Path, api_key_default: str = ""):
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="booktran")
    registry = ProjectRegistry(projects_root)
    web_dir = Path(__file__).parent  # index.html 就在 web 包目录下

    @app.get("/api/projects")
    async def list_projects() -> list[dict]:
        return [registry.get(str(p)).summary() for p in registry.discover()]

    # ---------- 上传建项目 ----------
    @app.post("/api/projects")
    async def create_project(
        file: UploadFile = File(...),
        target_langs: str = Form("chinese_simplified"),
        source_lang: str = Form("auto"),
        provider: str = Form(""),
        model: str = Form(""),
        platform: str = Form(""),
        vlm: bool = Form(False),
        vlm_dpi: int = Form(150),
        fake: bool = Form(False),
    ) -> dict:
        data = await file.read()
        if not data:
            raise HTTPException(400, "上传文件为空")
        cfg = load_config(None)  # 全局 config.toml + profiles.toml + 环境变量
        langs = [x.strip() for x in target_langs.split(",") if x.strip()] or ["chinese_simplified"]
        cfg.tr.target_langs = langs
        if source_lang:
            cfg.tr.source_lang = source_lang
        if fake:
            cfg.llm.provider = "fake"
        if platform:
            try:
                apply_platform(cfg, platform)
            except SystemExit as e:
                raise HTTPException(400, str(e)) from None
        elif provider or model:
            # 临时覆盖（不落盘）：档案仍在全局 profiles.toml 定义
            if provider:
                cfg.llm.provider = provider
            if model:
                cfg.llm.model = model
        try:
            handle = await registry.create(file.filename or "upload", data, cfg,
                                           vlm=vlm, vlm_dpi=vlm_dpi)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        return handle.summary()

    # ---------- 删除项目 ----------
    @app.delete("/api/project/{pid}")
    async def delete_project(pid: str) -> dict:
        try:
            registry.delete(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        except OSError as e:
            raise HTTPException(500, f"删除失败: {e}") from None
        return {"ok": True}

    # ---------- 设置 ----------
    @app.get("/api/project/{pid}/settings")
    async def get_settings(pid: str) -> dict:
        try:
            return registry.get(pid).settings()
        except KeyError:
            raise HTTPException(404, "项目不存在") from None

    @app.put("/api/project/{pid}/settings")
    async def put_settings(pid: str, body: ConfigReq) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        try:
            return h.apply_settings(body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    # ---------- 术语分析 ----------
    @app.post("/api/project/{pid}/analyze")
    async def analyze(pid: str, body: AnalyzeReq | None = None) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return await h.start_analyze(body.llm_merge if body else True)

    # ---------- VLM 重读（扫描页识别） ----------
    @app.post("/api/project/{pid}/read")
    async def read_source(pid: str, vlm_dpi: int = 150) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        if not any(d.fmt == "pdf" for d in h.docs):
            raise HTTPException(400, "仅 PDF 项目支持多模态识别")
        return await h.start_vlm_read(vlm_dpi)

    @app.get("/api/project/{pid}")
    async def project_summary(pid: str) -> dict:
        try:
            return registry.get(pid).summary()
        except KeyError:
            raise HTTPException(404, "项目不存在") from None

    @app.post("/api/project/{pid}/reload")
    async def project_reload(pid: str) -> dict:
        try:
            return registry.reload(pid).summary()
        except KeyError:
            raise HTTPException(404, "项目不存在") from None

    # ---------- models.dev 供应商模板 ----------
    @app.get("/api/providers")
    async def list_providers(refresh: bool = False) -> dict:
        from .providers import fetch_providers
        try:
            providers = await fetch_providers(force=refresh)
            return {"providers": providers}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"拉取供应商模板失败: {e}") from e

    # ---------- 模型档案 CRUD（全局 profiles.toml） ----------
    @app.post("/api/profiles")
    async def create_profile(body: dict):
        from ..config import keys_path, profiles_path
        from ..config.models import Profile
        from ..config.profile_store import ProfileStore
        store = ProfileStore(profiles_path(), keys_path())
        pid = str(body.get("id") or "").strip()
        if not pid:
            raise HTTPException(400, "档案 id 不能为空")
        profile = Profile.from_dict(pid, body)
        store.save_profile(profile)
        return {"ok": True, "id": pid}

    @app.put("/api/profiles/{pid}")
    async def update_profile(pid: str, body: dict):
        from ..config import keys_path, profiles_path
        from ..config.models import Profile
        from ..config.profile_store import ProfileStore
        store = ProfileStore(profiles_path(), keys_path())
        profile = Profile.from_dict(pid, body)
        store.save_profile(profile)
        return {"ok": True, "id": pid}

    @app.delete("/api/profiles/{pid}")
    async def delete_profile(pid: str):
        from ..config import keys_path, profiles_path
        from ..config.profile_store import ProfileStore
        store = ProfileStore(profiles_path(), keys_path())
        if not store.delete_profile(pid):
            raise HTTPException(404, f"档案不存在: {pid}")
        return {"ok": True}

    @app.put("/api/roles")
    async def save_roles(body: dict[str, str]):
        from ..config import keys_path, profiles_path
        from ..config.profile_store import ProfileStore
        store = ProfileStore(profiles_path(), keys_path())
        store.save_roles(body)
        return {"ok": True}

    @app.put("/api/budget")
    async def save_budget(body: dict):
        from ..config import keys_path, profiles_path
        from ..config.models import Budget
        from ..config.profile_store import ProfileStore
        store = ProfileStore(profiles_path(), keys_path())
        store.save_budget(Budget.from_dict(body))
        return {"ok": True}

    # ---------- 书页预览（随翻随排版） ----------
    @app.get("/api/project/{pid}/preview/{lang}")
    async def preview(pid: str, lang: str):
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        from ..ir import StyleSheet
        from ..layout import render_html
        sheets = {}
        style_file = h.dir / "stylesheet.json"
        if style_file.exists():
            try:
                sheets = json.loads(style_file.read_text("utf-8"))
            except (OSError, ValueError):
                sheets = {}
        out = []
        for doc in h.docs:
            raw = (sheets.get(doc.relpath) or {})
            sheet = StyleSheet.from_dict(raw) if raw else None
            out.append(render_html(doc, lang=lang, sheet=sheet,
                                   title=Path(doc.relpath).stem))
        html_all = "\n".join(out)
        return HTMLResponse(html_all)

    # ---------- 质检 ----------
    @app.post("/api/project/{pid}/proofread")
    async def proofread(pid: str, body: dict | None = None):
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        mode = (body or {}).get("mode") or "qa"
        return await h.start_proofread(mode)

    # ---------- 取消作业（优雅暂停） ----------
    @app.post("/api/project/{pid}/cancel")
    async def cancel_job(pid: str) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return h.cancel()

    # ---------- 模型档案与角色（全局引用，项目可覆盖） ----------
    @app.get("/api/profiles")
    async def list_profiles() -> dict:
        cfg = load_config(None)
        from ..config.keyring import key_source
        return {
            "profiles": [{
                "id": pid, "kind": p.kind, "provider": p.provider, "model": p.model,
                "base_url": p.base_url, "caps": sorted(p.caps),
                "price_in": p.price_in, "price_out": p.price_out,
                "key_env": p.key_env, "key_state": key_source(p),
            } for pid, p in sorted(cfg.profiles.items())],
            "roles": {name: spec.to_dict() for name, spec in cfg.roles.items()},
        }

    @app.get("/api/project/{pid}/cost")
    async def project_cost(pid: str) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return h.usage.aggregate()

    # ---------- SSE：作业事件流 ----------
    @app.get("/api/project/{pid}/stream")
    async def stream(pid: str, timeout: float = 0.0):
        """作业事件流（SSE）。

        timeout>0 时到点自动收尾（便于脚本/测试消费有限流）。
        """
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        queue = h.bus.subscribe()

        async def gen():
            import time as _t
            deadline = (_t.monotonic() + timeout) if timeout and timeout > 0 else None
            try:
                head = {"type": "state", "state": h.task_state,
                        "message": h.task_message, "progress": h.summary()["progress"]}
                yield f"event: state\ndata: {json.dumps(head, ensure_ascii=False)}\n\n"
                while True:
                    if deadline and _t.monotonic() >= deadline:
                        break
                    wait = 15.0
                    if deadline:
                        wait = max(0.1, min(15.0, deadline - _t.monotonic()))
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=wait)
                        kind = ev.get("type", "message")
                        yield f"event: {kind}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        if deadline and _t.monotonic() >= deadline:
                            break
                        yield ": ping\n\n"     # 心跳，避免代理断连
            finally:
                h.bus.unsubscribe(queue)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    # ---------- 质检报告 ----------
    @app.get("/api/project/{pid}/quality")
    async def quality(pid: str) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        from ..quality.report import QualityReport
        report = QualityReport.load(h.dir)
        return report.to_dict()

    @app.post("/api/project/{pid}/quality/apply")
    async def quality_apply(pid: str, body: dict):
        """接受一条建议：把译文改成建议值（记入修订历史，可回退）。"""
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        sid = str(body.get("sid") or "")
        lang = str(body.get("lang") or "")
        target = body.get("target")
        if not sid or not lang or not isinstance(target, str):
            raise HTTPException(400, "需要 sid / lang / target")
        found = False
        for doc in h.docs:
            for seg in doc.segments:
                if seg.sid == sid:
                    tr = seg.track(lang)
                    tr.revisions.append({"at": __import__("time").time(),
                                         "old": tr.target, "new": target,
                                         "source": body.get("source", "manual")})
                    tr.target = target
                    tr.status = Status.TRANSLATED
                    tr.error = None
                    h.store.append_event(seg, lang)
                    h.store.save_snapshot(h.docs, h.stats)
                    found = True
                    break
            if found:
                break
        if not found:
            raise HTTPException(404, "段落不存在")
        return {"ok": True}

    @app.post("/api/project/{pid}/segment/{sid:path}/revert")
    async def revert_segment(pid: str, sid: str, body: dict):
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        lang = str(body.get("lang") or "")
        if not lang:
            raise HTTPException(400, "需要 lang")
        return h.revert_segment(sid, lang)

    @app.get("/api/project/{pid}/segments")
    async def segments(pid: str, doc: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        try:
            return registry.get(pid).segments_page(doc, offset, limit)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None

    @app.put("/api/project/{pid}/segment/{sid:path}")
    async def update_segment(pid: str, sid: str, body: SegmentReq) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        if not h.update_segment(sid, body.target, body.lang or ""):
            raise HTTPException(404, "段落不存在")
        return {"ok": True}

    @app.get("/api/project/{pid}/glossary")
    async def get_glossary(pid: str) -> dict:
        try:
            g = registry.get(pid).load_glossary()
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return {"terms": g.terms, "non_translate": g.non_translate,
                "style": g.style, "notes": g.notes,
                "system_prompt": g.system_prompt, "background": g.background}

    @app.put("/api/project/{pid}/glossary")
    async def put_glossary(pid: str, body: dict) -> dict:
        try:
            registry.get(pid).save_glossary(body)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return {"ok": True}

    @app.post("/api/project/{pid}/translate")
    async def translate(pid: str, body: TranslateReq | None = None) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return await h.start_translate(body.model_dump() if body else {})

    @app.get("/api/project/{pid}/events")
    async def events(pid: str) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        return {"state": h.task_state, "message": h.task_message, "events": h.events[-50:]}

    @app.post("/api/project/{pid}/export")
    async def export(pid: str, body: ExportReq | None = None) -> dict:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        body = body or ExportReq()
        langs = body.langs
        if isinstance(langs, str):
            langs = [x.strip() for x in langs.split(",") if x.strip()]
        # web 导出固定落在项目 output/（不接受任意 out 路径，避免越权写盘）
        written = h.export(None, body.bilingual, body.format, langs=langs)
        return {"ok": True, "files": [Path(f).name for f in written]}

    @app.get("/api/project/{pid}/files")
    async def list_files(pid: str) -> list[dict]:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        out = h.dir / "output"
        if not out.exists():
            return []
        return [{"name": p.name, "size": p.stat().st_size}
                for p in sorted(out.iterdir()) if p.is_file()]

    @app.get("/api/project/{pid}/file/{name}")
    async def download_file(pid: str, name: str) -> FileResponse:
        try:
            h = registry.get(pid)
        except KeyError:
            raise HTTPException(404, "项目不存在") from None
        out = (h.dir / "output").resolve()
        # 路径穿越防护：解析后必须仍在 output/ 内
        target = (out / name).resolve()
        if not target.is_relative_to(out) or not target.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(target, filename=target.name)

    # 前端：Vite 构建产物（dist/）。没构建过就给出可操作的提示页，而不是白屏。
    dist = web_dir / "dist"
    built = (dist / "index.html").exists()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        if built:
            return FileResponse(dist / "index.html")
        return HTMLResponse(
            "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            "<title>booktran</title>"
            "<body style=\"font:14px/1.6 -apple-system,Segoe UI,sans-serif;padding:40px;max-width:640px\">"
            "<h1 style=\"font-size:17px\">前端还没构建</h1>"
            "<p>网页端是 Vite + React 工程，需要先构建一次：</p>"
            "<pre style=\"background:#f5f6f7;padding:12px;border-radius:6px\">"
            "cd web && npm install && npm run build</pre>"
            "<p>构建产物会自动放到 <code>src/booktran/web/dist/</code>，"
            "重启 <code>booktran serve</code> 即可。"
            "<br>命令行功能不受影响：<code>booktran run</code> / <code>booktran export</code> 照常可用。"
            "</body></html>")

    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    app.mount("/static", StaticFiles(directory=web_dir), name="static")
    return app
