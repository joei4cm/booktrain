import { useI18n } from "../i18n";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Issue, Project, QualityReport, Segment } from "../types";
import { Proof } from "./Proof";
import "./Workspace.css";

export interface WorkspaceProps {
  project: Project | null;
  report: QualityReport | null;
  previewLang: string;
  onPreviewLang: (lang: string) => void;
  onRunProofread: (mode: "qa" | "llm") => Promise<void>;
  onApplySuggestion: (issue: Issue, target: string) => Promise<void>;
  onRevertSuggestion?: (issue: Issue) => Promise<void>;
}

/** 工作台：左＝目录/阶段，中＝活页预览与段落，右＝校对。 */
export function Workspace(props: WorkspaceProps) {
  const { t } = useI18n();
  const { project, report, previewLang } = props;
  const [tab, setTab] = useState<"sheet" | "segments">("sheet");
  const [segments, setSegments] = useState<Segment[]>([]);
  const [offset, setOffset] = useState(0);
  const [nonce, setNonce] = useState(0);
  const limit = 50;

  // 进度变化 → 刷新预览（随翻随排版）
  useEffect(() => {
    if (!project) return;
    setNonce((n) => n + 1);
  }, [project?.progress.percent, project?.task.state]);

  useEffect(() => {
    if (!project || tab !== "segments") return;
    let cancelled = false;
    void api.segments(project.id, undefined, offset, limit).then((r) => {
      if (!cancelled) setSegments(r.items);
    });
    return () => {
      cancelled = true;
    };
  }, [project?.id, tab, offset, project?.progress.translated]);

  const previewUrl = useMemo(() => {
    if (!project || !previewLang) return "";
    return `${api.previewUrl(project.id, previewLang)}&n=${nonce}`;
  }, [project?.id, previewLang, nonce]);

  if (!project) {
    return (
      <div className="empty">
        还没有选中的书。先去「书架」上传或选一本。
      </div>
    );
  }

  return (
    <div className="ws">
      <aside className="ws-left panel">
        <h2>章节</h2>
        <ul className="ws-docs">
          {project.docs.map((d) => (
            <li key={d.relpath}>
              <span className="grow">{d.relpath}</span>
              <span className="num muted">
                {d.translated}/{d.total}
              </span>
            </li>
          ))}
        </ul>
        <h2>阶段</h2>
        <ul className="ws-stages">
          {(["translate", "proofread", "typeset"] as const).map((s) => {
            const label = s === "translate" ? t("ws.stageTranslate") : s === "proofread" ? t("ws.stageProofread") : t("ws.stageTypeset");
            const states = Object.values(project.per_lang).length;
            void states;
            const done = s === "translate" ? project.progress.translated : 0;
            const total = s === "translate" ? project.progress.total : 0;
            return (
              <li key={s}>
                <span className="grow">{label}</span>
                {s === "translate" ? (
                  <span className="num muted">
                    {done}/{total}
                  </span>
                ) : (
                  <span className="tag">{s === "proofread" ? report?.summary.total ?? 0 : "—"}</span>
                )}
              </li>
            );
          })}
        </ul>
        <h2>语言</h2>
        <ul className="ws-stages">
          {Object.entries(project.per_lang).map(([lang, p]) => (
            <li key={lang}>
              <button
                className="ghost grow"
                style={{ textAlign: "left" }}
                onClick={() => props.onPreviewLang(lang)}
                aria-current={lang === previewLang ? "true" : undefined}
              >
                {lang}
              </button>
              <span className="num muted">{p.percent.toFixed(0)}%</span>
            </li>
          ))}
        </ul>
      </aside>

      <section className="ws-center">
        <div className="row">
          <div className="tabs" role="tablist">
            <button
              role="tab"
              aria-selected={tab === "sheet"}
              className={tab === "sheet" ? "on" : ""}
              onClick={() => setTab("sheet")}
            >
              活页
            </button>
            <button
              role="tab"
              aria-selected={tab === "segments"}
              className={tab === "segments" ? "on" : ""}
              onClick={() => setTab("segments")}
            >
              段落
            </button>
          </div>
          <span className="grow" />
          <small className="muted">书页随翻译进度实时增长（未译段落显示原文占位）</small>
        </div>

        {tab === "sheet" ? (
          <iframe
            className="ws-sheet"
            title="书页预览"
            src={previewUrl}
          />
        ) : (
          <div className="ws-segments panel">
            <table>
              <thead>
                <tr>
                  <th scope="col" style={{ width: 40 }}>
                    #
                  </th>
                  <th scope="col">原文</th>
                  <th scope="col">译文</th>
                </tr>
              </thead>
              <tbody>
                {segments.map((s, i) => {
                  const lang = previewLang || Object.keys(s.tracks)[0] || "";
                  const tr = s.tracks[lang];
                  return (
                    <tr key={s.sid}>
                      <td className="num muted">{offset + i + 1}</td>
                      <td>{s.source}</td>
                      <td>
                        <textarea
                          defaultValue={tr?.target ?? ""}
                          aria-label={`${lang} 译文`}
                          onBlur={async (e) => {
                            const value = e.target.value;
                            if (value === (tr?.target ?? "")) return;
                            await api.saveSegment(project.id, s.sid, value, lang);
                          }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="row">
              <button onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</button>
              <button onClick={() => setOffset(offset + limit)}>下一页</button>
              <span className="grow" />
              <span className="muted num">
                {offset + 1}-{offset + segments.length} / {project.progress.total}
              </span>
            </div>
          </div>
        )}
      </section>

      <aside className="ws-right panel">
        <Proof
          projectId={project.id}
          report={report}
          onRun={props.onRunProofread}
          onApply={props.onApplySuggestion}
          onRevert={props.onRevertSuggestion}
        />
      </aside>
    </div>
  );
}
