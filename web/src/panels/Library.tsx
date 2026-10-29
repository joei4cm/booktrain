/**
 * 书架面板（docs/ux/web.md §3）。
 *
 * 为什么要这么写：
 * - 空状态是**四步教学**而不是空白（评审 P0-3：空状态不教人）。主行动只有一个：拖入 / 选择文件。
 * - 有书时用**行列表**而不是卡片墙（§1 明确拒绝"一排同尺寸卡片"）。密度是工具界面的许可。
 * - 进度用条不用环，金额右对齐 + 等宽数字（§3.2、§7.2）。
 * - 状态永远带文字，不靠颜色单独表达（§10「状态从不只用颜色」）。
 * - 语言用多选，不让用户手打逗号分隔的语言码（评审 R8 / P2-7）。
 */
import { useCallback, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, JSX } from "react";

import { api } from "../api";
import type { CostBucket, Project } from "../types";
import "./Library.css";

export interface LibraryProps {
  projects: Project[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onUpload: (file: File, targetLangs: string[], vlm: boolean) => Promise<void>;
  onDelete: (id: string) => void;
  busy?: boolean;
}

/* ------------------------------------------------------------------ 基础工具 */

/** 语言代码 → 人类可读的名字（不再让用户面对 chinese_simplified 这种暗码）。 */
const LANG_LABELS: Record<string, string> = {
  chinese_simplified: "简体中文",
  chinese_traditional: "繁體中文",
  english: "English",
  japanese: "日本語",
  korean: "한국어",
  french: "Français",
  german: "Deutsch",
  spanish: "Español",
  russian: "Русский",
  italian: "Italiano",
  portuguese: "Português",
  arabic: "العربية",
  vietnamese: "Tiếng Việt",
  thai: "ไทย",
  hindi: "हिन्दी",
  turkish: "Türkçe",
  dutch: "Nederlands",
  polish: "Polski",
  indonesian: "Bahasa Indonesia",
};

const LANG_OPTIONS = Object.entries(LANG_LABELS).map(([code, label]) => ({ code, label }));

function langLabel(code: string): string {
  if (!code || code === "—") return "未标注";
  return LANG_LABELS[code] ?? code;
}

/**
 * 金额显示规则（§6）：
 * 未知 → —；小于 1 分 → <$0.01；否则 $x.xx。
 * 0 是有意义的数字（本地模型真的免费），所以 0 显示 $0.00，只有"算不出来"才显示 —。
 */
function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (v > 0 && v < 0.01) return "<$0.01";
  return `$${v.toFixed(2)}`;
}

/** 后端 usage 聚合的字段名是 unpriced_requests，types.ts 里写的是 unpriced —— 两个都读。 */
function unpricedOf(bucket: CostBucket): number {
  const raw = bucket as CostBucket & { unpriced_requests?: number };
  return raw.unpriced_requests ?? bucket.unpriced ?? 0;
}

/**
 * 一个桶的金额。全部请求都没配价格时金额是"未知"，不是 0：
 * 猜一个 0 会撒谎（§6「未配置价格的档案显示 —，不显示 0」）。
 */
function bucketAmount(bucket: CostBucket | undefined): number | null {
  if (!bucket) return null;
  if (bucket.cost === 0 && unpricedOf(bucket) > 0) return null;
  return bucket.cost;
}

function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] ?? path;
}

/** 相对时间：ISO 字符串 → "3 分钟前"。没有时间戳就说不知道，不编。 */
function relativeTime(iso: string): string {
  if (!iso) return "时间未知";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "时间未知";
  const diff = Date.now() - t;
  if (diff < 60_000) return "刚刚";
  const min = Math.floor(diff / 60_000);
  if (min < 60) return `${min} 分钟前`;
  const hour = Math.floor(min / 60);
  if (hour < 24) return `${hour} 小时前`;
  const day = Math.floor(hour / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(t).toLocaleDateString("zh-CN");
}

function pctOf(p: Project): number {
  const v = p.progress.percent;
  if (typeof v !== "number" || Number.isNaN(v)) return 0;
  return Math.max(0, Math.min(100, v));
}

/** 行的状态标签：文字 + 语义色，缺一不可。 */
function statusOf(p: Project): { text: string; cls: string } {
  const pct = pctOf(p);
  if (p.task.state === "running") return { text: "翻译中", cls: "tag acc" };
  if (p.task.state === "error") return { text: "出错", cls: "tag err" };
  if (p.task.state === "done" || pct >= 100) return { text: "已完成", cls: "tag ok" };
  if (pct > 0) return { text: "翻译中", cls: "tag acc" };
  return { text: "待开始", cls: "" };
}

function fileFormat(p: Project): string {
  const name = baseName(p.source || "") || p.id;
  const dot = name.lastIndexOf(".");
  if (dot > 0 && dot < name.length - 1) return name.slice(dot + 1).toUpperCase();
  const fmt = p.docs[0]?.fmt;
  return fmt ? fmt.toUpperCase() : "—";
}

function projectName(p: Project): string {
  return baseName(p.source || "") || p.id;
}

/** 示例书：内置的一小段公共领域文字，零成本、不用 key，先把全流程跑通。 */
const SAMPLE_TEXT = [
  "从前有一座山，山上有一座庙，庙里有一位老和尚和一个小和尚。",
  "小和尚每天挑水、砍柴、扫院子。老和尚说：“你这样天天做的事，究竟有什么意思？”",
  "小和尚答不上来。老和尚说：“你用不着等明天，也不用等谁带路——挑水的时候挑水，砍柴的时候砍柴。”",
  "第二天，小和尚照旧挑水。回来的时候，他把水桶举得高高的，这一次，水洒出来的比往常少。于是他笑了。",
].join("\n\n");

/* ------------------------------------------------------------------ 局部控件 */

interface DropButtonProps {
  dragging: boolean;
  disabled: boolean;
  compact: boolean;
  onPick: () => void;
}

/** 主行动：整块可点，也接受拖放；键盘用户按 Enter 同样能触发。 */
function DropButton(props: DropButtonProps): JSX.Element {
  const { dragging, disabled, compact, onPick } = props;
  return (
    <button
      type="button"
      className={`lib-drop${dragging ? " is-over" : ""}${compact ? " is-compact" : ""}`}
      onClick={onPick}
      disabled={disabled}
      aria-label="选择要翻译的文件，或把文件拖到这里"
    >
      <span className="lib-drop-title">{compact ? "添加一本书" : "拖到这里，或点击选择文件"}</span>
      <span className="lib-drop-hint">
        {compact ? "EPUB · PDF · TXT · MOBI · 图片" : "支持 EPUB · PDF · TXT · MOBI · 图片，扫描件也能读"}
      </span>
    </button>
  );
}

interface LangFieldProps {
  langs: string[];
  disabled: boolean;
  compact?: boolean;
  onChange: (langs: string[]) => void;
}

/** 目标语言：多选下拉，杜绝手打逗号分隔的语言码。 */
function LangField(props: LangFieldProps): JSX.Element {
  const { langs, disabled, compact = false, onChange } = props;
  return (
    <div className="field lib-field">
      <label htmlFor="lib-langs">目标语言（可多选）</label>
      <select
        id="lib-langs"
        multiple
        size={compact ? 3 : 6}
        value={langs}
        disabled={disabled}
        onChange={(e: ChangeEvent<HTMLSelectElement>) =>
          onChange(Array.from(e.target.selectedOptions, (o) => o.value))
        }
      >
        {LANG_OPTIONS.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </div>
  );
}

interface VlmFieldProps {
  vlm: boolean;
  disabled: boolean;
  compact?: boolean;
  onChange: (vlm: boolean) => void;
}

/** VLM 只对扫描件 / 插图有意义，所以默认关着，并说清什么时候该开。 */
function VlmField(props: VlmFieldProps): JSX.Element {
  const { vlm, disabled, compact = false, onChange } = props;
  return (
    <label className="lib-check" htmlFor="lib-vlm">
      <input
        id="lib-vlm"
        type="checkbox"
        checked={vlm}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        用视觉模型读取页面
        {compact ? null : (
          <span className="lib-check-hint">（扫描件、插图版才需要；会多花一些钱）</span>
        )}
      </span>
    </label>
  );
}

/* ------------------------------------------------------------------ 行 */

interface RowProps {
  p: Project;
  selected: boolean;
  disabled: boolean;
  confirming: boolean;
  onSelect: (id: string) => void;
  onPreview: (p: Project) => void;
  onAskDelete: (id: string) => void;
  onConfirmDelete: (id: string) => void;
  onCancelDelete: () => void;
}

/**
 * 一行一本书（§3.2）。
 * 布局：名称/格式/语言 → 进度条 → 金额 → 行内操作；下面一行是补充信息。
 */
function Row(props: RowProps): JSX.Element {
  const { p, selected, disabled, confirming, onSelect, onPreview, onAskDelete, onConfirmDelete, onCancelDelete } =
    props;
  const pct = pctOf(p);
  const name = projectName(p);
  const amount = bucketAmount(p.cost);
  const status = statusOf(p);
  const srcLang = p.source_lang === "auto" ? "自动识别" : langLabel(p.source_lang);
  const targets = p.target_langs.length ? p.target_langs.map(langLabel).join(" / ") : "未设目标语言";
  const errCount = p.progress.errored ?? 0;

  return (
    <li className={`lib-row${selected ? " is-sel" : ""}`}>
      <div className="lib-row-top">
        <div className="lib-row-id">
          <button
            type="button"
            className="lib-name"
            onClick={() => onSelect(p.id)}
            aria-current={selected ? "true" : undefined}
            aria-label={`打开《${name}》`}
          >
            {name}
          </button>
          <span className="lib-meta">
            {fileFormat(p)} · {srcLang} → {targets}
            {selected ? " · 当前" : null}
          </span>
        </div>

        <div className="lib-row-progress">
          <div
            className="lib-bar"
            role="progressbar"
            aria-valuenow={Math.round(pct)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`《${name}》翻译进度`}
          >
            <div className="lib-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="lib-pct num">{Math.round(pct)}%</span>
        </div>

        <div className="lib-row-cost num" title="这本书到目前为止的花费">
          {fmtMoney(amount)}
        </div>

        {confirming ? (
          <div className="lib-actions is-confirm" role="group" aria-label={`确认删除《${name}》`}>
            <span className="lib-confirm-text">确认删除？</span>
            <button type="button" className="danger" onClick={() => onConfirmDelete(p.id)} disabled={disabled}>
              删除
            </button>
            <button type="button" className="ghost" onClick={onCancelDelete} disabled={disabled}>
              取消
            </button>
          </div>
        ) : (
          <div className="lib-actions">
            <button type="button" onClick={() => onSelect(p.id)} disabled={disabled} aria-label={`继续翻译《${name}》`}>
              继续
            </button>
            <button type="button" onClick={() => onPreview(p)} disabled={disabled} aria-label={`预览《${name}》的译文`}>
              预览
            </button>
            <button
              type="button"
              className="danger"
              onClick={() => onAskDelete(p.id)}
              disabled={disabled}
              aria-label={`删除《${name}》`}
            >
              删除
            </button>
          </div>
        )}
      </div>

      <div className="lib-row-sub">
        <span className={`tag ${status.cls}`.trim()}>{status.text}</span>
        <span className="lib-sub-item">
          上次活动 {p.task.state === "running" ? "刚刚 · 正在处理" : relativeTime(p.created_at)}
        </span>
        <span className="lib-sub-item">{p.model || p.provider || "未配置模型"}</span>
        <span className="lib-sub-item num">
          {p.progress.translated}/{p.progress.total} 段
        </span>
        {errCount > 0 ? <span className="lib-sub-item lib-sub-err">{errCount} 处失败</span> : null}
        {unpricedOf(p.cost) > 0 ? (
          <span className="lib-sub-item">{unpricedOf(p.cost)} 次请求未计价</span>
        ) : null}
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ 面板 */

export function Library(props: LibraryProps): JSX.Element {
  const { projects, selectedId, onSelect, onUpload, onDelete, busy = false } = props;

  const [langs, setLangs] = useState<string[]>(["chinese_simplified"]);
  const [vlm, setVlm] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const disabled = busy || uploading;

  const doUpload = useCallback(
    async (file: File) => {
      if (disabled) return;
      setError(null);
      setUploading(true);
      try {
        await onUpload(file, langs, vlm);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setUploading(false);
      }
    },
    [disabled, langs, onUpload, vlm],
  );

  const onPickFile = useCallback(() => {
    fileRef.current?.click();
  }, []);

  const onFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = ""; // 清空，好让同一个文件能再次选中
      if (file) void doUpload(file);
    },
    [doUpload],
  );

  const onDrop = useCallback(
    (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) void doUpload(file);
    },
    [doUpload],
  );

  const onDragOver = useCallback((e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setDragging(true);
  }, []);

  const onDragLeave = useCallback((e: DragEvent<HTMLElement>) => {
    // 只有真的离开容器才取消高亮，划过子元素时别闪
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragging(false);
  }, []);

  const onPreview = useCallback(
    (p: Project) => {
      const lang = p.target_lang || p.target_langs[0] || "";
      if (!lang) {
        // 没有目标语言就没有可预览的版本，退回工作台让用户自己选
        onSelect(p.id);
        return;
      }
      window.open(api.previewUrl(p.id, lang), "_blank", "noopener,noreferrer");
    },
    [onSelect],
  );

  const onSample = useCallback(() => {
    const file = new File([SAMPLE_TEXT], "sample.txt", { type: "text/plain" });
    void doUpload(file);
  }, [doUpload]);

  const hasProjects = projects.length > 0;

  return (
    <section
      className={`lib${dragging ? " is-dragging" : ""}`}
      aria-label="书架"
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <input
        ref={fileRef}
        type="file"
        className="lib-file"
        onChange={onFileChange}
        accept=".epub,.pdf,.txt,.mobi,.azw3,image/*"
        aria-label="选择要翻译的文件"
      />

      {error ? (
        <p className="lib-error" role="alert">
          上传失败：{error}
        </p>
      ) : null}

      {!hasProjects ? (
        /* 空状态 = 教学：四步，只有第一步是主行动 */
        <div className="lib-empty">
          <h2 className="lib-empty-title">把一本书变成你能读的语言</h2>
          <ol className="lib-steps">
            <li className="lib-step">
              <span className="lib-step-num" aria-hidden="true">
                1
              </span>
              <div className="lib-step-body">
                <h3>放进你的书</h3>
                <p>支持 EPUB、PDF、TXT、MOBI 和图片；扫描件也能读。</p>
                <DropButton dragging={dragging} disabled={disabled} compact={false} onPick={onPickFile} />
                <VlmField vlm={vlm} disabled={disabled} onChange={setVlm} />
              </div>
            </li>
            <li className="lib-step">
              <span className="lib-step-num" aria-hidden="true">
                2
              </span>
              <div className="lib-step-body">
                <h3>选你要的语言</h3>
                <p>可以一次选好几种，开始之后也能随时换。</p>
                <LangField langs={langs} disabled={disabled} onChange={setLangs} />
              </div>
            </li>
            <li className="lib-step">
              <span className="lib-step-num" aria-hidden="true">
                3
              </span>
              <div className="lib-step-body">
                <h3>选排版方式</h3>
                <p>参照原排版、模型设计或混合——在「模型」页里设定。</p>
              </div>
            </li>
            <li className="lib-step">
              <span className="lib-step-num" aria-hidden="true">
                4
              </span>
              <div className="lib-step-body">
                <h3>看清价格再开始</h3>
                <p>开始前会告诉你这次大概要花多少钱；翻译过程中费用一直摆在书架上。</p>
              </div>
            </li>
          </ol>

          <div className="lib-empty-foot">
            <span className="muted">没有书？</span>
            <button type="button" className="lib-sample" onClick={onSample} disabled={disabled}>
              用示例书试试
            </button>
            <span className="muted lib-sample-hint">内置一本小样，零成本、不用配 key。</span>
          </div>
        </div>
      ) : (
        <>
          <header className="lib-head">
            <h2 className="lib-title">书架</h2>
            <span className="lib-count num">{projects.length} 本</span>
            <div className="lib-head-spacer" />
            <DropButton dragging={dragging} disabled={disabled} compact onPick={onPickFile} />
            <LangField langs={langs} disabled={disabled} compact onChange={setLangs} />
            <VlmField vlm={vlm} disabled={disabled} compact onChange={setVlm} />
          </header>

          <ul className="lib-list">
            {projects.map((p) => (
              <Row
                key={p.id}
                p={p}
                selected={p.id === selectedId}
                disabled={disabled}
                confirming={confirmId === p.id}
                onSelect={onSelect}
                onPreview={onPreview}
                onAskDelete={(id) => setConfirmId(id)}
                onConfirmDelete={(id) => {
                  setConfirmId(null);
                  onDelete(id);
                }}
                onCancelDelete={() => setConfirmId(null)}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
