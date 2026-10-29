/**
 * 校对面板：质检问题清单 + 就地处理。
 *
 * 为什么在这张表里就能「采用建议」：评审 §4.3 的结论是问题必须能就地处理，
 * 处理完立刻回写译文，工作台的书页随之更新；跳去另一个页面等于把活儿拆散。
 *
 * 严重度同时用文字和颜色表达（评审 R10 / §10：状态从不只用颜色）。
 */
import { useMemo, useState } from "react";
import type { JSX } from "react";
import { useI18n } from "../i18n";

import type { Issue, QualityReport } from "../types";
import "./Proof.css";

export interface ProofProps {
  projectId: string | null;
  report: QualityReport | null;
  onRun: (mode: "qa" | "llm") => Promise<void>;
  onApply: (issue: Issue, target: string) => Promise<void>;
  /** 可选：回退到上一版修订 */
  onRevert?: (issue: Issue) => Promise<void>;
  busy?: boolean;
}

const SEVERITY_LABELS: Record<string, string> = {
  error: "error",
  warn: "warn",
  info: "info",
};

/** 严重度 → 徽标样式（.tag.err / .tag.warn / 中性 .tag）。 */
const SEVERITY_TAG: Record<string, string> = {
  error: "err",
  warn: "warn",
  info: "",
};

/** 类型 → 中文标签；没见过的类型退回原文，不吞掉（暗码外泄的反面：不能瞎翻译）。 */
const KIND_LABELS: Record<string, string> = {
  glossary: "术语不一致",
  untranslated: "疑似未翻译",
  placeholder: "占位符丢失",
  length: "长度异常",
  numbering: "编号残留",
  non_translate: "禁翻表残留",
  empty: "空译文",
  llm: "模型建议",
};

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function severityRank(severity: string): number {
  if (severity === "error") return 0;
  if (severity === "warn") return 1;
  if (severity === "info") return 2;
  return 3;
}

export function Proof(props: ProofProps): JSX.Element {
  const { t } = useI18n();
  const { projectId, report, onRun, onApply, onRevert, busy } = props;

  const [mode, setMode] = useState<"qa" | "llm">("qa");
  const [runMsg, setRunMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [applyMsg, setApplyMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const issues = useMemo(() => {
    const list = report?.issues ?? [];
    return [...list].sort(
      (a, b) =>
        severityRank(a.severity) - severityRank(b.severity) ||
        a.kind.localeCompare(b.kind) ||
        a.sid.localeCompare(b.sid),
    );
  }, [report]);

  const bySeverity = report?.summary.by_severity ?? {};
  const nError = bySeverity.error ?? 0;
  const nWarn = bySeverity.warn ?? 0;
  const nInfo = bySeverity.info ?? 0;
  const total = report?.summary.total ?? 0;

  const kindBreakdown = useMemo(() => {
    const byKind = report?.summary.by_kind ?? {};
    return Object.keys(byKind)
      .sort((a, b) => (byKind[b] ?? 0) - (byKind[a] ?? 0))
      .map((k) => `${KIND_LABELS[k] ?? k} ${byKind[k] ?? 0}`)
      .join(" · ");
  }, [report]);

  async function handleRun(): Promise<void> {
    setRunMsg(null);
    try {
      await onRun(mode);
      setRunMsg({
        tone: "ok",
        text: mode === "llm" ? "模型审校已跑完。" : "本地规则质检已跑完。",
      });
    } catch (e) {
      setRunMsg({ tone: "err", text: `运行失败：${errText(e)}` });
    }
  }

  async function handleApply(issue: Issue): Promise<void> {
    const key = `${issue.lang}/${issue.sid}`;
    setApplyMsg(null);
    setApplying(key);
    try {
      await onApply(issue, issue.suggestion);
      setApplyMsg({ tone: "ok", text: `已采用建议并写回译文：${issue.sid}` });
    } catch (e) {
      setApplyMsg({ tone: "err", text: `写回失败：${errText(e)}` });
    } finally {
      setApplying(null);
    }
  }

  const runDisabled = busy === true || !projectId;

  return (
    <section className="panel proof" aria-labelledby="proof-title">
      <header className="proof-head">
        <h2 id="proof-title">校对</h2>
        {report ? (
          <div className="proof-stats" aria-live="polite">
            <span className="proof-stat">
              共 <b className="num">{total}</b> 个问题
            </span>
            <span className="tag err">错误 {nError}</span>
            <span className="tag warn">警告 {nWarn}</span>
            <span className="tag">提示 {nInfo}</span>
          </div>
        ) : (
          <p className="hint">还没有质检报告。</p>
        )}
      </header>

      {report && kindBreakdown ? (
        <p className="hint proof-breakdown">{kindBreakdown}</p>
      ) : null}

      {/* ---------- 运行质检 ---------- */}
      <div className="proof-controls">
        <div className="field proof-mode">
          <label htmlFor="proof-mode-select">质检方式</label>
          <select
            id="proof-mode-select"
            value={mode}
            onChange={(e) => setMode(e.currentTarget.value === "llm" ? "llm" : "qa")}
          >
            <option value="qa">本地规则（不调模型，不花钱）</option>
            <option value="llm">模型审校（调模型，按 token 计费）</option>
          </select>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => {
            void handleRun();
          }}
          disabled={runDisabled}
        >
          {busy === true ? "运行中…" : t("proof.run")}
        </button>
        {mode === "llm" ? (
          <p className="proof-cost proof-cost--money" role="status">
            模型审校会把正文交给模型判断，按 token 计费；长书请先确认预算上限。
          </p>
        ) : (
          <p className="hint proof-cost">本地规则只做确定性检查，不调模型、不产生费用。</p>
        )}
      </div>

      {runMsg ? (
        <p className={`proof-msg ${runMsg.tone === "err" ? "is-err" : "is-ok"}`} role="status">
          {runMsg.text}
        </p>
      ) : null}

      {/* ---------- 清单 / 空状态 ---------- */}
      {!report ? (
        <div className="proof-empty">
          <p className="proof-empty__lead">
            还没有质检记录。质检会逐段比对译文，找出术语不一致、疑似未翻译、占位符丢失、编号残留
            这类问题——先把书翻完，再点上面的「运行质检」。
          </p>
          <ul className="proof-empty__list">
            <li>
              <b>本地规则</b>：查术语表、占位符、残留原文，固定规则、稳定结果、不花钱。
            </li>
            <li>
              <b>模型审校</b>：让模型通读译文挑错，判得更细，但会按 token 计费。
            </li>
          </ul>
          <p className="hint">
            选好方式后点「运行质检」，结果会列在下面；每条建议都能就地采用并写回译文。
          </p>
        </div>
      ) : issues.length === 0 ? (
        <div className="proof-empty">
          <p className="proof-empty__lead">这次质检没有发现问题。</p>
          <p className="hint">
            译文里没找到术语不一致、未翻译、占位符丢失等问题。可以换一种质检方式再跑一遍，
            或继续推进翻译后再复核。
          </p>
        </div>
      ) : (
        <div className="proof-table-wrap">
          <table>
            <caption className="proof-caption">质检问题清单（按严重度排序）</caption>
            <thead>
              <tr>
                <th scope="col">严重度</th>
                <th scope="col">类型</th>
                <th scope="col">问题</th>
                <th scope="col">建议</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {issues.map((issue) => {
                const key = `${issue.lang}/${issue.sid}`;
                const sevLabel = SEVERITY_LABELS[issue.severity] ?? issue.severity;
                const sevTag = SEVERITY_TAG[issue.severity] ?? "";
                const kindLabel = KIND_LABELS[issue.kind] ?? issue.kind;
                const canApply = issue.suggestion.trim().length > 0;
                return (
                  <tr key={`${issue.lang}/${issue.sid}/${issue.kind}`}>
                    <td>
                      <span className={`tag ${sevTag}`.trim()}>{sevLabel}</span>
                    </td>
                    <td>{kindLabel}</td>
                    <td>
                      <div className="proof-msg-line">{issue.message}</div>
                      <div className="proof-meta">
                        语言 {issue.lang || "—"} · 段 {issue.sid || "—"}
                      </div>
                      {issue.source || issue.target ? (
                        <details className="proof-context">
                          <summary>看原文与译文</summary>
                          <div className="proof-context__body">
                            <div>
                              <span className="proof-context__label">原文</span>
                              <div className="proof-context__text">{issue.source || "—"}</div>
                            </div>
                            <div>
                              <span className="proof-context__label">译文</span>
                              <div className="proof-context__text">{issue.target || "—"}</div>
                            </div>
                          </div>
                        </details>
                      ) : null}
                    </td>
                    <td>
                      {canApply ? (
                        <span className="proof-suggestion">{issue.suggestion}</span>
                      ) : (
                        <span className="muted">（没有可直接采用的建议）</span>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        aria-label={`采用建议：${kindLabel} · ${issue.lang || "—"} · ${issue.sid || "—"}`}
                        onClick={() => {
                          void handleApply(issue);
                        }}
                        disabled={!canApply || applying === key}
                      >
                        {applying === key ? t("proof.writing") : t("proof.adopt")}
                      </button>
                      {onRevert ? (
                        <button
                          type="button"
                          className="ghost"
                          aria-label={`回退：${issue.lang || "—"} · ${issue.sid || "—"}`}
                          title="回退到上一版（撤销上次修改）"
                          onClick={() => void onRevert(issue)}
                          disabled={applying === key}
                        >
                          回退
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {applyMsg ? (
        <p className={`proof-msg ${applyMsg.tone === "err" ? "is-err" : "is-ok"}`} role="status">
          {applyMsg.text}
        </p>
      ) : null}
    </section>
  );
}
