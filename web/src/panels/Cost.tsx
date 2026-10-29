/**
 * 成本面板（docs/ux/web.md §6）。
 *
 * 为什么要这么写：
 * - 评审 P0-1「钱完全不在视野里」：合计、预算、四个维度的金额都常驻，不藏在设置里。
 * - §6：预算用**条**不用环；未配置价格的档案显示 —，不显示 0（0 是有意义的数字，会撒谎）。
 * - 条形占比按**该维度内最大金额**归一化，不是按总额——这样一行一眼能看出谁最贵。
 * - 状态不只用颜色表达：每根条旁边都有百分比数字，错误一定带文字。
 */
import { useCallback, useEffect, useState } from "react";
import type { JSX } from "react";
import { useI18n } from "../i18n";

import { api } from "../api";
import type { CostBucket, CostSummary } from "../types";
import "./Cost.css";

export interface CostProps {
  projectId: string | null;
  cost: CostSummary | null;
  budget: { max_cost: number; max_tokens: number };
  /** 超预算时给一个"把上限提到 X"的真按钮（可选） */
  onRaiseBudget?: (next: { max_cost: number; max_tokens: number }) => void;
}

/* ------------------------------------------------------------------ 基础工具 */

/** 金额显示规则（§6）：未知 → —；小于 1 分 → <$0.01；否则 $x.xx。 */
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

/** 桶的金额：全部请求都没配价格时是"未知"，不是 0。 */
function bucketAmount(bucket: CostBucket | undefined): number | null {
  if (!bucket) return null;
  if (bucket.cost === 0 && unpricedOf(bucket) > 0) return null;
  return bucket.cost;
}

function fmtTokens(n: number): string {
  if (!n) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

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

const STAGE_LABELS: Record<string, string> = {
  translate: "翻译",
  recognize: "识别",
  proofread: "校对",
  layout: "排版",
  glossary: "术语分析",
  quality: "质检",
  ocr: "OCR",
  read: "视觉读取",
};

const ROLE_LABELS: Record<string, string> = {
  translate: "翻译",
  proofread: "校对",
  layout: "排版设计",
  recognize: "识别",
  vlm: "视觉识别",
  ocr: "OCR",
  glossary: "术语分析",
  quality: "质检",
  read: "视觉读取",
};

function langLabel(code: string): string {
  if (!code || code === "—") return "未标注语言";
  return LANG_LABELS[code] ?? code;
}

/* ------------------------------------------------------------------ 维度行 */

interface DimRow {
  key: string;
  label: string;
  bucket: CostBucket;
  /** null = 费用未知（未配置价格）。 */
  amount: number | null;
  /** 占本维度已知总额的比例（百分比）。 */
  share: number;
  /** 条形长度：按本维度内最大金额归一化（不是按总额）。 */
  bar: number;
}

interface DimData {
  rows: DimRow[];
  /** 本维度已知的金额合计。 */
  total: number;
  /** 完全没算出来的请求数。 */
  unknown: number;
}

function buildDim(entries: Record<string, CostBucket> | undefined, label: (k: string) => string): DimData {
  const raw = Object.entries(entries ?? {});
  const known = raw.filter(([, b]) => bucketAmount(b) !== null);
  const total = known.reduce((s, [, b]) => s + b.cost, 0);
  const max = known.reduce((m, [, b]) => Math.max(m, b.cost), 0);

  const rows: DimRow[] = raw.map(([key, bucket]) => {
    const amount = bucketAmount(bucket);
    return {
      key,
      label: label(key),
      bucket,
      amount,
      share: amount !== null && total > 0 ? (amount / total) * 100 : 0,
      bar: amount !== null && max > 0 ? (amount / max) * 100 : 0,
    };
  });

  // 贵的排前面；算不出价格的沉到最后（按请求数排）。
  rows.sort((a, b) => {
    const av = a.amount ?? -1;
    const bv = b.amount ?? -1;
    if (av !== bv) return bv - av;
    return b.bucket.requests - a.bucket.requests;
  });

  const unknown = raw
    .filter(([, b]) => bucketAmount(b) === null)
    .reduce((s, [, b]) => s + unpricedOf(b), 0);

  return { rows, total, unknown };
}

/* ------------------------------------------------------------------ 分块 */

interface BlockProps {
  title: string;
  nameHeader: string;
  dim: DimData;
}

function CostBlock(props: BlockProps): JSX.Element {
  const { title, nameHeader, dim } = props;
  return (
    /* 台账样式：一行一条横线，不做成卡片墙（§1 拒绝"一排同尺寸卡片"） */
    <section className="cost-block" aria-label={title}>
      <div className="cost-block-head">
        <h3 className="cost-block-title">{title}</h3>
        <span className="cost-block-total num">合计 {fmtMoney(dim.total)}</span>
      </div>

      {dim.rows.length === 0 ? (
        <p className="cost-block-empty">这个维度还没有记录。</p>
      ) : (
        <table className="cost-table">
          <caption className="cost-caption">{title}的花费明细</caption>
          <thead>
            <tr>
              <th scope="col">{nameHeader}</th>
              <th scope="col" className="cost-num">
                金额
              </th>
              <th scope="col">占比</th>
            </tr>
          </thead>
          <tbody>
            {dim.rows.map((r) => (
              <tr key={r.key}>
                <th scope="row" className="cost-name">
                  {r.label}
                  <span className="cost-name-sub num">
                    输入 {fmtTokens(r.bucket.prompt_tokens)} · 输出 {fmtTokens(r.bucket.completion_tokens)} · {r.bucket.requests} 次
                  </span>
                </th>
                <td className="cost-num num">
                  {fmtMoney(r.amount)}
                  {unpricedOf(r.bucket) > 0 ? (
                    <span className="cost-unpriced"> {unpricedOf(r.bucket)} 次未计价</span>
                  ) : null}
                </td>
                <td className="cost-share">
                  <div className="cost-share-inner">
                    {r.amount === null ? (
                      <span className="cost-unknown">费用未知</span>
                    ) : (
                      <>
                        <div
                          className="cost-bar"
                          role="progressbar"
                          aria-valuenow={Math.round(r.share)}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-label={`${r.label} 占本维度 ${r.share.toFixed(1)}%`}
                        >
                          <div className="cost-bar-fill" style={{ width: `${r.bar}%` }} />
                        </div>
                        <span className="cost-pct num">{r.share.toFixed(1)}%</span>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {dim.unknown > 0 ? (
        <p className="cost-note">另有 {dim.unknown} 次请求没配置价格，没算进金额。</p>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ 面板 */

export function Cost(props: CostProps): JSX.Element {
  const { t } = useI18n();
  const { projectId, cost, budget, onRaiseBudget } = props;

  // 父组件给的汇总 + 用户手动刷新的结果；换项目时丢掉旧的刷新结果。
  const [fresh, setFresh] = useState<{ id: string; data: CostSummary } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setFresh(null);
  }, [projectId]);

  const data: CostSummary | null = fresh && fresh.id === projectId ? fresh.data : cost;

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const next = await api.cost(projectId);
      setFresh({ id: projectId, data: next });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const total = data?.total;
  const totalAmount = bucketAmount(total);
  const knownCost = total?.cost ?? 0;
  const unpricedTotal = total ? unpricedOf(total) : 0;
  const promptTokens = total?.prompt_tokens ?? 0;
  const completionTokens = total?.completion_tokens ?? 0;

  const maxCost = budget?.max_cost ?? 0;
  const maxTokens = budget?.max_tokens ?? 0;
  const hasBudget = maxCost > 0;
  const budgetPct = hasBudget ? (knownCost / maxCost) * 100 : 0;
  const over = hasBudget && knownCost > maxCost;
  const tokenPct = maxTokens > 0 ? ((promptTokens + completionTokens) / maxTokens) * 100 : 0;

  const hasUsage = !!data && data.total.requests > 0;

  const stageDim = buildDim(data?.by_stage, (k) => STAGE_LABELS[k] ?? (k === "—" ? "未标注阶段" : k));
  const roleDim = buildDim(data?.by_role, (k) => ROLE_LABELS[k] ?? (k === "—" ? "未标注角色" : k));
  const modelDim = buildDim(data?.by_model, (k) => (k === "—" ? "未标注模型" : k));
  const langDim = buildDim(data?.by_lang, (k) => langLabel(k));

  return (
    <section className="cost" aria-label="成本" aria-busy={loading}>
      <header className="cost-head">
        <h2 className="cost-title">成本</h2>
        <div className="cost-head-spacer" />
        <button type="button" className="ghost" onClick={() => void refresh()} disabled={!projectId || loading}>
          {loading ? "刷新中…" : "刷新"}
        </button>
      </header>

      {error ? (
        <div className="cost-error" role="alert">
          <p className="cost-error-text">刷新失败，没能拿到最新的费用数据。</p>
          <button type="button" onClick={() => void refresh()} disabled={loading}>
            重试
          </button>
          <details className="cost-details">
            <summary>原始细节</summary>
            <pre className="mono">{error}</pre>
          </details>
        </div>
      ) : null}

      {loading ? (
        <div className="cost-skeleton" aria-hidden="true">
          <span className="cost-skel-line" />
          <span className="cost-skel-bar" />
        </div>
      ) : null}

      {/* 合计 + 预算：条不用环 */}
      <div className="cost-summary panel">
        {/* 台账一行：标签 + 数字，不做成"大数字指标卡" */}
        <div className="cost-kv-row">
          <span className="cost-kv is-total">
            <span className="cost-kv-label">本次合计</span>
            <span className="cost-kv-value num" aria-live="polite">
              {fmtMoney(totalAmount)}
            </span>
          </span>
          <span className="cost-kv">
            <span className="cost-kv-label">预算上限</span>
            <span className="cost-kv-value num">{hasBudget ? fmtMoney(maxCost) : "未设置"}</span>
          </span>
          <span className="cost-kv">
            <span className="cost-kv-label">请求</span>
            <span className="cost-kv-value num">{total ? total.requests : 0} 次</span>
          </span>
          <span className="cost-kv">
            <span className="cost-kv-label">Token</span>
            <span className="cost-kv-value num">
              输入 {fmtTokens(promptTokens)} · 输出 {fmtTokens(completionTokens)}
            </span>
          </span>
        </div>

        {hasBudget ? (
          <div className="cost-budget">
            <div className="cost-budget-head">
              <span className={over ? "cost-budget-over" : undefined}>预算已用 {budgetPct.toFixed(0)}%</span>
              <span className="num">
                {fmtMoney(knownCost)} / {fmtMoney(maxCost)}
              </span>
            </div>
            <div
              className="cost-bar is-budget"
              role="progressbar"
              aria-valuenow={Math.round(Math.min(100, budgetPct))}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`预算已用 ${budgetPct.toFixed(0)}%`}
            >
              <div className={`cost-bar-fill${over ? " is-over" : ""}`} style={{ width: `${Math.min(100, budgetPct)}%` }} />
            </div>
            {over ? (
              <p className="cost-alert" role="alert">
                已超出预算上限 {fmtMoney(maxCost)}（超出 {fmtMoney(knownCost - maxCost)}），作业会在这里暂停。
                下一步：把本项目的预算上限调高，或换一个更便宜的模型。
              </p>
            ) : null}
            {over && onRaiseBudget ? (
              <div className="row">
                <button
                  onClick={() =>
                    onRaiseBudget({
                      max_cost: Math.max(maxCost * 2, Math.ceil(knownCost * 1.2)),
                      max_tokens: maxTokens,
                    })
                  }
                >
                  把上限提到 {fmtMoney(Math.max(maxCost * 2, Math.ceil(knownCost * 1.2)))}
                </button>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="cost-note">还没有设置预算上限。设一个上限，作业超支时会自动暂停。</p>
        )}

        {maxTokens > 0 ? (
          <div className="cost-budget">
            <div className="cost-budget-head">
              <span>Token 预算已用 {tokenPct.toFixed(0)}%</span>
              <span className="num">
                {fmtTokens(promptTokens + completionTokens)} / {fmtTokens(maxTokens)}
              </span>
            </div>
            <div
              className="cost-bar is-budget"
              role="progressbar"
              aria-valuenow={Math.round(Math.min(100, tokenPct))}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`Token 预算已用 ${tokenPct.toFixed(0)}%`}
            >
              <div className="cost-bar-fill" style={{ width: `${Math.min(100, tokenPct)}%` }} />
            </div>
          </div>
        ) : null}

        {unpricedTotal > 0 ? (
          <p className="cost-note">另有 {unpricedTotal} 次请求没配置价格（模型档案里没填价格表），金额里没算它们。</p>
        ) : null}
      </div>

      {!projectId ? (
        <p className="cost-empty">先在书架里选一本书，这里会显示它的钱花到了哪里。</p>
      ) : !data ? (
        <p className="cost-empty">正在读取费用记录…</p>
      ) : !hasUsage ? (
        <p className="cost-empty">还没有任何用量记录。跑一次翻译后，这里会显示钱花到了哪里。</p>
      ) : (
        <div className="cost-blocks">
          <CostBlock title={t("cost.byStage")} nameHeader="阶段" dim={stageDim} />
          <CostBlock title={t("cost.byRole")} nameHeader="角色" dim={roleDim} />
          <CostBlock title={t("cost.byModel")} nameHeader="模型" dim={modelDim} />
          <CostBlock title="按目标语言" nameHeader="语言" dim={langDim} />
        </div>
      )}
    </section>
  );
}
