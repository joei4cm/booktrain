/**
 * 术语与背景面板：术语表 / 禁翻表 / 翻译背景 / 文风与提示词。
 *
 * 为什么只有一个「保存」按钮（评审 B1 的教训）：
 * 旧界面把术语表和翻译背景拆成两个保存按钮，各自持一份快照，
 * 后保存的那次会用旧快照覆盖先保存的——用户没保存的编辑被**静默吃掉**。
 * 这里组件内部只维护一份 draft，一次 PUT 把整份对象写回去，
 * 于是根本不存在「哪份快照更旧」这回事，也就无从覆盖。
 *
 * 其它两条硬规矩：
 * - 保存失败**不清草稿**：错误提示说人话，改动还在原地，可以改好了再存。
 * - 外部数据变了而本地还有未保存改动时，不覆盖本地草稿，而是把冲突摆出来让人选。
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { JSX } from "react";

import type { Glossary as GlossaryModel } from "../types";
import "./Glossary.css";

export interface GlossaryProps {
  projectId: string | null;
  glossary: GlossaryModel | null;
  onSave: (g: GlossaryModel) => Promise<void>;
  onRunAnalyze?: () => Promise<void>;
  busy?: boolean;
}

type TermRow = GlossaryModel["terms"][number];

/** 语言列的默认值：只有一个覆盖列时，先给英文。 */
const DEFAULT_LANGS = "english";

/** 语言代码 → 中文名（与后端 prompt/builder.py 的 LANG_ZH 保持一致，未收录的原样透传）。 */
const LANG_ZH: Record<string, string> = {
  chinese_simplified: "简体中文",
  chinese_traditional: "繁體中文",
  english: "英语",
  japanese: "日语",
  korean: "韩语",
  french: "法语",
  german: "德语",
  spanish: "西班牙语",
  russian: "俄语",
  italian: "意大利语",
  portuguese: "葡萄牙语",
  arabic: "阿拉伯语",
  vietnamese: "越南语",
  thai: "泰语",
  hindi: "印地语",
  turkish: "土耳其语",
  dutch: "荷兰语",
  polish: "波兰语",
  indonesian: "印尼语",
};

interface BackgroundField {
  key: string;
  label: string;
  /** 长文本字段给 textarea，短字段给 input。 */
  long?: boolean;
}

/** 翻译背景的 10 个字段：中文名给界面看，英文 key 落盘（后端按 key 注入提示词）。 */
const BACKGROUND_FIELDS: readonly BackgroundField[] = [
  { key: "title", label: "书名/作品名" },
  { key: "genre", label: "题材类型" },
  { key: "era", label: "时代/世界观" },
  { key: "setting", label: "背景设定", long: true },
  { key: "tone", label: "基调/氛围" },
  { key: "characters", label: "主要人物与关系", long: true },
  { key: "naming", label: "命名/称呼规则", long: true },
  { key: "terminology", label: "专有名词背景", long: true },
  { key: "audience", label: "目标读者" },
  { key: "notes", label: "其他背景信息", long: true },
];

/** 语言代码的中文名；没收录的用代码本身，不瞎翻译（暗码外泄的反面）。 */
function langLabel(code: string): string {
  return LANG_ZH[code] ?? code.replace(/_/g, " ");
}

/** 逗号/顿号/空格分隔的语言串 → 去重后的代码列表（顺序按输入）。 */
function parseLangs(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(/[,，、;\s]+/)) {
    const code = part.trim().toLowerCase();
    if (!code || out.includes(code)) continue;
    out.push(code);
  }
  return out;
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 空术语表骨架。 */
function emptyGlossary(): GlossaryModel {
  return {
    terms: [],
    non_translate: [],
    style: "",
    notes: "",
    system_prompt: "",
    background: {},
  };
}

/** 深拷贝一份草稿：编辑永远不碰 props 里的对象。 */
function cloneGlossary(g: GlossaryModel | null): GlossaryModel {
  if (!g) return emptyGlossary();
  return {
    terms: (g.terms ?? []).map((t) => ({ ...t })),
    non_translate: [...(g.non_translate ?? [])],
    style: g.style ?? "",
    notes: g.notes ?? "",
    system_prompt: g.system_prompt ?? "",
    background: { ...(g.background ?? {}) },
  };
}

/**
 * 稳定序列化：对象键排序后再比。
 * 用途是判断「外部来的数据是不是和我们刚存上去的一样」，
 * 键序不同不该被当成外部改动（否则每次保存后都会误报冲突）。
 */
function stableJson(value: unknown): string {
  return JSON.stringify(value, (_key, v) => {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const obj = v as Record<string, unknown>;
      const sorted: Record<string, unknown> = {};
      for (const k of Object.keys(obj).sort()) sorted[k] = obj[k];
      return sorted;
    }
    return v;
  });
}

/** 术语行里某个语言的覆盖译名（后端按 dst_<语言> 取值，取不到就回落默认译名）。 */
function overrideValue(row: TermRow, lang: string): string {
  const v = row[`dst_${lang}`];
  return typeof v === "string" ? v : "";
}

/**
 * 组装要写回后端的整份对象。
 * 空值不落盘：没有原文的行匹配不了任何东西；空的覆盖值等于「回落默认」，
 * 留着只会让 glossary.json 越攒越脏。
 */
function buildPayload(d: GlossaryModel): GlossaryModel {
  const terms: TermRow[] = [];
  for (const raw of d.terms) {
    const src = (raw.src ?? "").trim();
    if (!src) continue;
    const row: TermRow = { ...raw, src };
    if (typeof row.dst === "string") {
      const v = row.dst.trim();
      if (v) row.dst = v;
      else delete row.dst;
    }
    if (typeof row.note === "string") {
      const v = row.note.trim();
      if (v) row.note = v;
      else delete row.note;
    }
    for (const key of Object.keys(row)) {
      if (!key.startsWith("dst_")) continue;
      const v = row[key];
      if (typeof v === "string") {
        const t = v.trim();
        if (t) row[key] = t;
        else delete row[key];
      } else if (v === undefined || v === null) {
        delete row[key];
      }
    }
    terms.push(row);
  }

  const background: Record<string, string> = {};
  for (const f of BACKGROUND_FIELDS) {
    const v = (d.background ?? {})[f.key];
    if (typeof v === "string" && v.trim()) background[f.key] = v.trim();
  }

  return {
    terms,
    non_translate: (d.non_translate ?? []).map((s) => s.trim()).filter((s) => s.length > 0),
    style: (d.style ?? "").trim(),
    notes: (d.notes ?? "").trim(),
    system_prompt: (d.system_prompt ?? "").trim(),
    background,
  };
}

export function Glossary(props: GlossaryProps): JSX.Element {
  const { projectId, glossary, onSave, onRunAnalyze, busy } = props;

  const [draft, setDraft] = useState<GlossaryModel>(() => cloneGlossary(glossary));
  const [dirty, setDirty] = useState(false);
  const [langsText, setLangsText] = useState(DEFAULT_LANGS);
  const [saving, setSaving] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  // dirty 的镜像：同步 effect 里要读它，但不能把 dirty 放进依赖（否则每次按键都会重同步草稿）。
  const dirtyRef = useRef(false);
  // 上一次同步的 props，用来判断「外部数据是不是真的变了」。
  const baseRef = useRef<GlossaryModel | null>(null);
  // 当前是第几本书：换书要把草稿整个换掉（不然会把上一本书的术语写进新书）。
  const projectRef = useRef(projectId);
  // 我们刚存上去的那份内容：外部把它回传回来时认账，别误报冲突。
  const savedJsonRef = useRef<string | null>(null);

  function markDirty(): void {
    dirtyRef.current = true;
    setDirty(true);
    setNotice(null);
  }

  // 外部数据同步：只有内容真的变了才动草稿，换书则整份重来。
  // 用 layout effect：换书后不要让上一本书的术语在屏幕上多留一帧。
  useLayoutEffect(() => {
    const incoming = glossary;
    const prev = baseRef.current;
    const projectChanged = projectRef.current !== projectId;
    projectRef.current = projectId;
    baseRef.current = incoming;
    if (!projectChanged && prev === incoming) return;

    const json = stableJson(incoming);
    if (savedJsonRef.current !== null && savedJsonRef.current === json) {
      // 我们自己刚保存的内容回传：认下来，清干净，不当作外部改动。
      savedJsonRef.current = null;
      setDraft(cloneGlossary(incoming));
      dirtyRef.current = false;
      setDirty(false);
      setConflict(false);
      return;
    }
    if (!projectChanged && prev !== null && stableJson(prev) === json) {
      // 父组件重建了同内容的对象：草稿不动。
      return;
    }
    if (dirtyRef.current && !projectChanged) {
      // 本地还有没保存的改动，而外面变了：保住草稿，把冲突摆出来让人决定，绝不静默覆盖。
      setConflict(true);
      return;
    }
    // 换书，或本地是干净的：直接同步外部版本。
    const wasDirty = dirtyRef.current;
    savedJsonRef.current = null;
    setDraft(cloneGlossary(incoming));
    dirtyRef.current = false;
    setDirty(false);
    setConflict(false);
    if (projectChanged) {
      // 不同书的目标语言不同，语言列回到默认；旧书的草稿不跟着走（避免写到新书里去）。
      setLangsText(DEFAULT_LANGS);
      setError(null);
      setNotice(
        wasDirty
          ? "已切换到另一本书，上一本书未保存的改动没有写入。"
          : "已切换到另一本书，术语与背景按新书重新载入。",
      );
    }
  }, [projectId, glossary]);

  const langs = useMemo(() => parseLangs(langsText), [langsText]);

  function updateTermField(index: number, key: string, value: string): void {
    setDraft((d) => {
      const terms = d.terms.slice();
      const cur = terms[index];
      if (!cur) return d;
      const next: TermRow = { ...cur };
      next[key] = value;
      terms[index] = next;
      return { ...d, terms };
    });
    markDirty();
  }

  function addTerm(): void {
    setDraft((d) => ({ ...d, terms: [...d.terms, { src: "", dst: "", note: "" }] }));
    markDirty();
  }

  function removeTerm(index: number): void {
    setDraft((d) => ({ ...d, terms: d.terms.filter((_, i) => i !== index) }));
    markDirty();
  }

  function updateNonTranslate(index: number, value: string): void {
    setDraft((d) => {
      const list = d.non_translate.slice();
      if (index >= list.length) return d;
      list[index] = value;
      return { ...d, non_translate: list };
    });
    markDirty();
  }

  function addNonTranslate(): void {
    setDraft((d) => ({ ...d, non_translate: [...d.non_translate, ""] }));
    markDirty();
  }

  function removeNonTranslate(index: number): void {
    setDraft((d) => ({ ...d, non_translate: d.non_translate.filter((_, i) => i !== index) }));
    markDirty();
  }

  function updateBackground(key: string, value: string): void {
    setDraft((d) => ({ ...d, background: { ...(d.background ?? {}), [key]: value } }));
    markDirty();
  }

  function updateText(field: "style" | "notes" | "system_prompt", value: string): void {
    setDraft((d) => {
      if (field === "style") return { ...d, style: value };
      if (field === "notes") return { ...d, notes: value };
      return { ...d, system_prompt: value };
    });
    markDirty();
  }

  async function handleSave(): Promise<void> {
    if (!projectId) return;
    setSaving(true);
    setError(null);
    const payload = buildPayload(draft);
    try {
      await onSave(payload);
      savedJsonRef.current = stableJson(payload);
      dirtyRef.current = false;
      setDirty(false);
      setConflict(false);
      setNotice("已保存：术语、禁翻表、翻译背景与提示词一次写入。");
    } catch (e) {
      // 失败不清草稿：改动还在原地，用户修好后可以再存一次。
      setError(`保存失败：${errText(e)}。你的改动仍在草稿里，可以修好后重试。`);
    } finally {
      setSaving(false);
    }
  }

  async function handleAnalyze(): Promise<void> {
    if (!onRunAnalyze) return;
    setAnalyzing(true);
    setError(null);
    try {
      await onRunAnalyze();
      setNotice("术语分析已跑完：候选术语在上表，补上译名再保存。");
    } catch (e) {
      setError(`术语分析失败：${errText(e)}`);
    } finally {
      setAnalyzing(false);
    }
  }

  function discardLocal(): void {
    setDraft(cloneGlossary(glossary));
    dirtyRef.current = false;
    setDirty(false);
    setConflict(false);
    savedJsonRef.current = null;
    setNotice("已放弃本地改动，改用外部版本。");
  }

  const busyNow = busy === true;
  const canSave = !busyNow && !saving && projectId !== null;

  return (
    <section className="panel gl" aria-labelledby="gl-title">
      <header className="gl-head">
        <div className="gl-head-row">
          <h2 id="gl-title" className="gl-title">
            术语与背景
          </h2>
          <span className="gl-head-spacer" />
          {onRunAnalyze ? (
            <button
              type="button"
              onClick={() => {
                void handleAnalyze();
              }}
              disabled={busyNow || analyzing}
            >
              {analyzing ? "分析中…" : "术语分析"}
            </button>
          ) : null}
          <button
            type="button"
            className="primary"
            onClick={() => {
              void handleSave();
            }}
            disabled={!canSave}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>

        <p className="gl-sub">
          术语表、禁翻表、翻译背景、文风与提示词一次保存、同时生效——这里只有一个保存按钮。
        </p>

        <p className="gl-status" role="status">
          {dirty ? (
            <span className="tag warn">有未保存的改动</span>
          ) : (
            <span className="tag ok">已是最新</span>
          )}
          {projectId === null ? <span className="muted">先在书架里选一本书再保存。</span> : null}
          {notice ? <span className="gl-notice">{notice}</span> : null}
        </p>

        {error ? (
          <p className="gl-error" role="status">
            {error}
          </p>
        ) : null}

        {conflict ? (
          <div className="gl-conflict" role="status">
            <span>
              外面这份术语表已经变了（可能刚跑完术语分析）。你未保存的改动没有被覆盖，还在草稿里。
            </span>
            <span className="gl-row">
              <button type="button" onClick={discardLocal}>
                放弃我的改动
              </button>
              <button type="button" onClick={() => setConflict(false)}>
                保留我的改动
              </button>
            </span>
          </div>
        ) : null}
      </header>

      {/* ---------------------------------------------------------- 术语表 */}
      <section className="gl-section" aria-labelledby="gl-terms-title">
        <h3 id="gl-terms-title" className="gl-section-title">
          术语表
        </h3>
        <p className="gl-hint">
          原文来自术语分析（只读）；补上默认译名。某个语言有不同译法时填该语言列，留空即回落默认译名。
        </p>

        <div className="field gl-langs">
          <label htmlFor="gl-langs-input">目标语言列（逗号分隔）</label>
          <input
            id="gl-langs-input"
            type="text"
            value={langsText}
            placeholder="english, chinese_simplified"
            aria-describedby="gl-langs-hint"
            onChange={(e) => {
              setLangsText(e.currentTarget.value);
              markDirty();
            }}
          />
          <p id="gl-langs-hint" className="gl-hint">
            表格里每个语言一列；覆盖译名按 <span className="mono">dst_&lt;语言&gt;</span> 存在术语行上。
            语言代码统一转小写，未收录的代码原样使用。
          </p>
        </div>

        {draft.terms.length === 0 ? (
          <div className="gl-empty">
            <p className="gl-empty-lead">还没有术语。三步就能用起来：</p>
            <ol>
              <li>点「术语分析」生成候选：模型通读全书，列出专有名词（会调用模型，按 token 计费）。</li>
              <li>给每个候选补上默认译名——没有译名的术语要么不译，要么前后不一致。</li>
              <li>点右上角「保存」，术语表、禁翻表和背景一次写入。</li>
            </ol>
            <div className="gl-row">
              {onRunAnalyze ? (
                <button
                  type="button"
                  className="primary"
                  onClick={() => {
                    void handleAnalyze();
                  }}
                  disabled={busyNow || analyzing}
                >
                  {analyzing ? "分析中…" : "术语分析"}
                </button>
              ) : null}
              <button type="button" onClick={addTerm}>
                手工添加一行
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="gl-table-wrap">
              <table className="gl-table">
                <caption className="gl-sr">
                  术语表：原文、默认译名、各目标语言覆盖译名与备注
                </caption>
                <thead>
                  <tr>
                    <th scope="col" className="gl-col-src">
                      原文
                    </th>
                    <th scope="col" className="gl-col-dst">
                      默认译名
                    </th>
                    {langs.map((l) => (
                      <th scope="col" key={l} className="gl-col-lang">
                        <span className="gl-lang-label">{langLabel(l)}</span>
                        <span className="mono gl-lang-code">{l}</span>
                      </th>
                    ))}
                    <th scope="col" className="gl-col-note">
                      备注
                    </th>
                    <th scope="col">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.terms.map((row, i) => {
                    const rowName = row.src.trim() || `第 ${i + 1} 行`;
                    return (
                      <tr key={i}>
                        <td className="gl-col-src gl-src">
                          {row.src.trim() ? (
                            row.src
                          ) : (
                            <input
                              type="text"
                              value={row.src}
                              placeholder="原文术语（必填）"
                              aria-label={`第 ${i + 1} 行原文`}
                              onChange={(e) => updateTermField(i, "src", e.currentTarget.value)}
                            />
                          )}
                        </td>
                        <td className="gl-col-dst">
                          <input
                            type="text"
                            value={row.dst ?? ""}
                            placeholder="译名"
                            aria-label={`默认译名：${rowName}`}
                            onChange={(e) => updateTermField(i, "dst", e.currentTarget.value)}
                          />
                        </td>
                        {langs.map((l) => (
                          <td key={l} className="gl-col-lang">
                            <input
                              type="text"
                              value={overrideValue(row, l)}
                              placeholder="回落默认"
                              aria-label={`${langLabel(l)}译名：${rowName}`}
                              onChange={(e) =>
                                updateTermField(i, `dst_${l}`, e.currentTarget.value)
                              }
                            />
                          </td>
                        ))}
                        <td className="gl-col-note">
                          <input
                            type="text"
                            value={row.note ?? ""}
                            placeholder="备注（可选）"
                            aria-label={`备注：${rowName}`}
                            onChange={(e) => updateTermField(i, "note", e.currentTarget.value)}
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="ghost"
                            aria-label={`删除术语：${rowName}`}
                            onClick={() => removeTerm(i)}
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="gl-row gl-toolbar">
              <button type="button" onClick={addTerm}>
                添加一行
              </button>
              <span className="gl-hint">
                没有原文的行不会保存；覆盖列留空即回落「默认译名」。
              </span>
            </div>
          </>
        )}
      </section>

      {/* ---------------------------------------------------------- 禁翻表 */}
      <section className="gl-section" aria-labelledby="gl-nt-title">
        <h3 id="gl-nt-title" className="gl-section-title">
          禁翻表
        </h3>
        <p className="gl-hint">
          这些记号原样保留、不翻译：变量名、代码片段、商标、专有符号等。留空行不会保存。
        </p>
        {draft.non_translate.length === 0 ? (
          <p className="gl-hint">禁翻表是空的。正文里的变量名和代码通常需要列进来，否则会被译掉。</p>
        ) : (
          <ol className="gl-nt-list">
            {draft.non_translate.map((item, i) => (
              <li key={i} className="gl-nt-row">
                <label className="gl-sr" htmlFor={`gl-nt-${i}`}>
                  禁翻条目 {i + 1}
                </label>
                <input
                  id={`gl-nt-${i}`}
                  type="text"
                  value={item}
                  placeholder="要原样保留的记号"
                  onChange={(e) => updateNonTranslate(i, e.currentTarget.value)}
                />
                <button
                  type="button"
                  className="ghost"
                  aria-label={`删除禁翻条目：${item.trim() || `第 ${i + 1} 条`}`}
                  onClick={() => removeNonTranslate(i)}
                >
                  删除
                </button>
              </li>
            ))}
          </ol>
        )}
        <div className="gl-row gl-toolbar">
          <button type="button" onClick={addNonTranslate}>
            添加一条
          </button>
        </div>
      </section>

      {/* ---------------------------------------------------------- 翻译背景 */}
      <section className="gl-section" aria-labelledby="gl-bg-title">
        <h3 id="gl-bg-title" className="gl-section-title">
          翻译背景
        </h3>
        <p className="gl-hint">
          随每次请求注入提示词：模型知道这是什么书、谁是谁，前后才能一致。只写你真的在意的。
        </p>
        <div className="gl-grid">
          {BACKGROUND_FIELDS.map((f) => (
            <div className="field" key={f.key}>
              <label htmlFor={`gl-bg-${f.key}`}>{f.label}</label>
              {f.long ? (
                <textarea
                  id={`gl-bg-${f.key}`}
                  className="gl-textarea"
                  value={draft.background?.[f.key] ?? ""}
                  onChange={(e) => updateBackground(f.key, e.currentTarget.value)}
                />
              ) : (
                <input
                  id={`gl-bg-${f.key}`}
                  type="text"
                  value={draft.background?.[f.key] ?? ""}
                  onChange={(e) => updateBackground(f.key, e.currentTarget.value)}
                />
              )}
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------- 文风与提示词 */}
      <section className="gl-section" aria-labelledby="gl-style-title">
        <h3 id="gl-style-title" className="gl-section-title">
          文风与提示词
        </h3>
        <div className="gl-grid">
          <div className="field">
            <label htmlFor="gl-style">文风</label>
            <textarea
              id="gl-style"
              className="gl-textarea"
              value={draft.style}
              placeholder="例如：冷淡克制，保留敬语，不用四字成语"
              onChange={(e) => updateText("style", e.currentTarget.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="gl-notes">其它要求</label>
            <textarea
              id="gl-notes"
              className="gl-textarea"
              value={draft.notes}
              placeholder="除术语表之外，还想让模型注意的事"
              onChange={(e) => updateText("notes", e.currentTarget.value)}
            />
          </div>
          <div className="field gl-span">
            <label htmlFor="gl-system-prompt">整体提示词</label>
            <textarea
              id="gl-system-prompt"
              className="gl-textarea gl-textarea-tall"
              value={draft.system_prompt}
              placeholder="本书专属的整体提示词，优先级高于内置模板"
              onChange={(e) => updateText("system_prompt", e.currentTarget.value)}
            />
          </div>
        </div>
      </section>
    </section>
  );
}
