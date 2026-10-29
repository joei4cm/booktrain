import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";
import { api, subscribe } from "./api";
import type { CostSummary, Glossary as GlossaryModel, Profile, Project, QualityReport, RoleSpec, StreamEvent } from "./types";
import { Library } from "./panels/Library";
import { Glossary } from "./panels/Glossary";
import { Cost } from "./panels/Cost";
import { Models } from "./panels/Models";
import { Workspace } from "./panels/Workspace";
import { CommandPalette } from "./panels/CommandPalette";

type Route = "library" | "workspace" | "glossary" | "models" | "cost";

const NAV: { id: Route; label: string; icon: JSX.Element }[] = [
  {
    id: "library",
    label: "书架",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 5h5v14H4zM10 5h4v14h-4zM15.5 5.6l3.4 1.1-3.9 12.4-3.4-1.1z" />
      </svg>
    ),
  },
  {
    id: "workspace",
    label: "工作台",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M3 4h8v16H3zM13 4h8v7h-8zM13 13h8v7h-8z" />
      </svg>
    ),
  },
  {
    id: "models",
    label: "模型",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
      </svg>
    ),
  },
  {
    id: "glossary",
    label: "术语",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 5h7v14H4zM13 5h7v14h-7zM6.5 9h2M6.5 12h2M6.5 15h2M15 9h3M15 12h3M15 15h3" />
      </svg>
    ),
  },
  {
    id: "cost",
    label: "成本",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3v18M16 7.5c0-1.4-1.6-2.5-4-2.5S8 6.1 8 7.5 9.6 10 12 10s4 1.1 4 2.5S14.4 15 12 15s-4-1.1-4-2.5" />
      </svg>
    ),
  },
];

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [route, setRoute] = useState<Route>("library");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [roles, setRoles] = useState<Record<string, RoleSpec>>({});
  const [report, setReport] = useState<QualityReport | null>(null);
  const [glossary, setGlossary] = useState<GlossaryModel | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [previewLang, setPreviewLang] = useState<string>("");
  const unsubRef = useRef<(() => void) | null>(null);

  const say = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((m) => (m === msg ? null : m)), 4000);
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await api.projects());
    } catch {
      /* 静默：下一轮轮询会重试 */
    }
  }, []);

  const refreshProject = useCallback(async () => {
    if (!project) return;
    try {
      setProject(await api.project(project.id));
    } catch {
      /* 忽略单次失败 */
    }
  }, [project]);

  // 首次加载
  useEffect(() => {
    void refreshProjects();
    void api
      .profiles()
      .then((d) => {
        setProfiles(d.profiles);
        setRoles(d.roles);
      })
      .catch(() => undefined);
  }, [refreshProjects]);

  // 选中项目后：订阅 SSE + 轮询兜底
  useEffect(() => {
    unsubRef.current?.();
    unsubRef.current = null;
    if (!project) return;
    const onEvent = (ev: StreamEvent) => {
      if (ev.type === "done") {
        void refreshProject();
        void refreshProjects();
        say(`任务结束${ev.cost != null ? ` · 花费 ${fmtCost(ev.cost)}` : ""}`);
      }
      if (ev.type === "issue") setReport((r) => r);
    };
    unsubRef.current = subscribe(project.id, onEvent);
    const timer = window.setInterval(() => {
      void refreshProject();
      void refreshProjects();
    }, 5000);
    return () => {
      window.clearInterval(timer);
      unsubRef.current?.();
      unsubRef.current = null;
    };
  }, [project?.id, refreshProject, refreshProjects, say]);

  // 项目切换时载入报告与费用
  useEffect(() => {
    if (!project) {
      setReport(null);
      setCost(null);
      return;
    }
    void api
      .quality(project.id)
      .then(setReport)
      .catch(() => undefined);
    void api
      .glossary(project.id)
      .then(setGlossary)
      .catch(() => undefined);
    void api
      .cost(project.id)
      .then(setCost)
      .catch(() => undefined);
  }, [project?.id, project?.task.state]);

  const select = useCallback(async (id: string) => {
    try {
      const p = await api.project(id);
      setProject(p);
      setPreviewLang(p.target_langs[0] ?? "");
      setRoute("workspace");
    } catch (e) {
      say(String(e));
    }
  }, [say]);

  const upload = useCallback(
    async (file: File, targetLangs: string[], vlm: boolean) => {
      setBusy(true);
      try {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("target_langs", targetLangs.join(","));
        fd.append("vlm", String(vlm));
        const res = await fetch("/api/projects", { method: "POST", body: fd });
        if (!res.ok) throw new Error(`上传失败（HTTP ${res.status}）`);
        const created = (await res.json()) as Project;
        await refreshProjects();
        await select(created.id);
        say(`已创建 ${created.id}`);
      } catch (e) {
        say(String(e));
      } finally {
        setBusy(false);
      }
    },
    [refreshProjects, select, say],
  );

  const startTranslate = useCallback(async () => {
    if (!project) return;
    const r = await api.translate(project.id, {});
    if (r.ok === false) {
      say(r.message ?? "无法启动翻译");
      return;
    }
    say("翻译已启动");
    void refreshProject();
  }, [project, refreshProject, say]);

  const cancel = useCallback(async () => {
    if (!project) return;
    const r = await api.cancel(project.id);
    say(r.message || (r.ok ? "已请求停止" : "无法停止"));
  }, [project, say]);

  const budget = project?.budget ?? { max_cost: 0, max_tokens: 0 };

  const actions = useMemo(
    () => [
      { id: "library", label: "回到书架", run: () => setRoute("library") },
      { id: "workspace", label: "打开工作台", run: () => setRoute("workspace") },
      { id: "glossary", label: "术语与背景", run: () => setRoute("glossary") },
      { id: "models", label: "模型与角色", run: () => setRoute("models") },
      { id: "cost", label: "成本明细", run: () => setRoute("cost") },
      {
        id: "translate",
        label: "开始 / 继续翻译",
        run: () => {
          void startTranslate();
        },
      },
    ],
    [startTranslate],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
      if (e.key === "Escape") setPaletteOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const running = project?.task.state === "running";

  return (
    <div className="app">
      <header className="topbar">
        <h1>booktran</h1>
        {project && (
          <span className="muted">
            {project.id} · {project.target_langs.join(" / ")}
          </span>
        )}
        <span className="grow" />
        {project && (
          <>
            {running && (
              <button onClick={() => void cancel()} title="优雅暂停：当前块结束后保存">
                停止
              </button>
            )}
            <button
              className="primary"
              disabled={running}
              onClick={() => void startTranslate()}
            >
              {project.progress.translated > 0 ? "继续翻译" : "开始翻译"}
            </button>
          </>
        )}
        <button className="ghost" onClick={() => setPaletteOpen(true)} aria-label="命令面板">
          ⌘K
        </button>
      </header>

      <div className="body">
        <nav className="nav" aria-label="主导航">
          {NAV.map((n) => (
            <button
              key={n.id}
              onClick={() => setRoute(n.id)}
              aria-current={route === n.id ? "page" : undefined}
            >
              {n.icon}
              {n.label}
            </button>
          ))}
        </nav>

        <main className="main">
          {route === "library" && (
            <Library
              projects={projects}
              selectedId={project?.id ?? null}
              onSelect={(id) => void select(id)}
              onUpload={upload}
              onDelete={(id) => {
                void api.remove(id).then(() => {
                  if (project?.id === id) setProject(null);
                  void refreshProjects();
                });
              }}
              busy={busy}
            />
          )}

          {route === "workspace" && (
            <Workspace
              project={project}
              report={report}
              previewLang={previewLang}
              onPreviewLang={setPreviewLang}
              onRunProofread={async (mode) => {
                if (!project) return;
                const r = await api.proofread(project.id, mode);
                if (r.ok === false) say(r.message ?? "无法启动质检");
                else say("质检已启动");
              }}
              onApplySuggestion={async (issue, target) => {
                if (!project) return;
                await api.applySuggestion(project.id, issue, target);
                setReport(await api.quality(project.id));
              }}
              onRevertSuggestion={async (issue) => {
                if (!project) return;
                const r = await api.revertSegment(project.id, issue.sid, issue.lang);
                if (r.ok === false) say(r.message ?? "回退失败");
                else say("已回退到上一版");
              }}
            />
          )}

          {route === "glossary" && (
            <Glossary
              projectId={project?.id ?? null}
              glossary={glossary}
              onSave={async (g) => {
                if (!project) return;
                await api.saveGlossary(project.id, g);
                setGlossary(g);
                say("术语与背景已保存");
              }}
              onRunAnalyze={async () => {
                if (!project) return;
                const r = await api.analyze(project.id);
                if (r.ok === false) say(r.message ?? "无法启动术语分析");
                else say("术语分析已启动（完成后回到本页补译名）");
              }}
            />
          )}

          {route === "models" && (
            <Models
              projectId={project?.id ?? null}
              profiles={profiles}
              roles={roles}
              budget={budget}
              onChanged={() => {
                void api.profiles().then((d) => { setProfiles(d.profiles); setRoles(d.roles); });
              }}
              onSave={async (body) => {
                if (!project) return;
                await api.saveSettings(project.id, body);
                say("设置已保存");
              }}
              onSaveRoles={async (next) => {
                if (!project) return;
                await api.saveSettings(project.id, { roles: next });
                setRoles(
                  Object.fromEntries(
                    Object.entries(next).map(([k, v]) => [k, { profile: v }]),
                  ),
                );
                say("角色路由已保存");
              }}
              onSaveBudget={async (next) => {
                if (!project) return;
                await api.saveSettings(project.id, {
                  budget: next,
                });
                say("预算已保存");
              }}
            />
          )}

          {route === "cost" && (
            <Cost
              projectId={project?.id ?? null}
              cost={cost}
              budget={budget}
              onRaiseBudget={async (next) => {
                if (!project) return;
                await api.saveSettings(project.id, { budget: next });
                say(`预算上限已提到 ${next.max_cost}`);
              }}
            />
          )}
        </main>
      </div>

      {project && (
        <footer className="jobbar" aria-live="polite">
          <span className="muted">阶段</span>
          {["translate", "proofread", "typeset"].map((s) => {
            const states = Object.values(project.per_lang);
            void states;
            return (
              <span key={s} className="tag">
                {s === "translate" ? "翻译" : s === "proofread" ? "校对" : "排版"}
              </span>
            );
          })}
          <div className="bar" role="progressbar" aria-valuenow={project.progress.percent}
               aria-valuemin={0} aria-valuemax={100}
               aria-label="翻译进度">
            <i style={{ width: `${project.progress.percent}%` }} />
          </div>
          <span className="num">{project.progress.percent.toFixed(0)}%</span>
          <span className="num muted">
            {project.progress.translated}/{project.progress.total} 段
          </span>
          <span className="grow" />
          <span className="num">{fmtCost(cost?.total.cost ?? null)}</span>
          {budget.max_cost > 0 && (
            <span className="muted num">上限 {fmtCost(budget.max_cost)}</span>
          )}
          <span className="muted">{project.task.message || project.task.state}</span>
        </footer>
      )}

      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        actions={actions}
      />
    </div>
  );
}

function fmtCost(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v > 0 && v < 0.01) return "<$0.01";
  return `$${v.toFixed(2)}`;
}
