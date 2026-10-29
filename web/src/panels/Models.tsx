/**
 * 模型面板：把「档案」和「角色」分成两块呈现。
 *
 * 为什么分开：两者回答的是两个不同的问题——
 *   档案 = 有哪些端点（全局、只读）；角色 = 谁干什么活（本项目可改）。
 * 混成一张表，用户会以为「改档案 = 改项目」，然后在全局文件里翻找项目级设置。
 *
 * 成本口径（docs/ux/web.md §5/§6）：价格写在档案上，未配置价格显示 `—`（0 是有意义的数字，
 * 用来表示「本地模型不花钱」）；密钥只显示来源状态，永不回显密钥本身。
 */
import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";

import { api } from "../api";
import type { Profile, RoleSpec } from "../types";
import "./Models.css";

export interface ModelsProps {
  projectId: string | null;
  profiles: Profile[];
  roles: Record<string, RoleSpec>;
  budget: { max_cost: number; max_tokens: number };
  onSave: (body: Record<string, unknown>) => Promise<void>;
  onSaveRoles: (roles: Record<string, string>) => Promise<void>;
  onSaveBudget: (budget: { max_cost: number; max_tokens: number }) => Promise<void>;
}

/** 角色顺序固定；配置里多出来的角色也会补在后面（用户才知道「谁还没派活」）。 */
const ROLE_ORDER = ["translate", "proofread", "typeset", "vlm", "ocr", "analyze"] as const;

interface RoleMeta {
  /** 中文名：避免把 translate/ocr 这类暗码直接甩给用户（评审 P2-7）。 */
  label: string;
  desc: string;
  /** 该角色是否通常要调用模型（决定要不要提醒花钱）。 */
  billable: boolean;
}

const ROLE_META: Record<string, RoleMeta> = {
  translate: { label: "翻译", desc: "把原文翻成目标语言", billable: true },
  proofread: { label: "校对", desc: "模型审校：润色并挑错", billable: true },
  typeset: { label: "排版设计", desc: "重新设计版式与排版", billable: true },
  vlm: { label: "识别（图文）", desc: "看图理解版式与插图", billable: true },
  ocr: { label: "识别（文字）", desc: "扫描件转文字", billable: false },
  analyze: { label: "术语分析", desc: "全书术语抽取", billable: true },
};

const CAP_LABELS: Record<string, string> = {
  text: "文本",
  vision: "视觉",
  ocr: "OCR",
  embed: "向量",
};

const KIND_LABELS: Record<string, string> = {
  chat: "对话",
  vision: "视觉",
  ocr: "文字识别",
  local: "本地",
};

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 未配置价格 = 未知 = `—`；0 是真值（本地模型确实不花钱），要显示出来。 */
function fmtPrice(value: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(2)}`;
}

function priceText(priceIn: number | null, priceOut: number | null): string {
  if (priceIn === null && priceOut === null) return "—";
  return `${fmtPrice(priceIn)} / ${fmtPrice(priceOut)}`;
}

function capsText(caps: string[]): string {
  if (!caps || caps.length === 0) return "—";
  return caps.map((c) => CAP_LABELS[c] ?? c).join("·");
}

/** 密钥状态沿用后端原话（env:NAME / keys.toml:x / 档案内（不推荐）/ 未找到）。 */
function keyTagClass(state: string): string {
  if (!state || state.includes("未找到")) return "err";
  if (state.includes("档案内")) return "warn";
  return "ok";
}

export function Models(props: ModelsProps): JSX.Element {
  const { projectId, profiles, roles, budget, onSave, onSaveRoles, onSaveBudget } = props;

  /** 待保存的角色选择；保存成功后清掉，失败也清掉（界面回到后端的真实状态，不装成功）。 */
  const [pending, setPending] = useState<Record<string, string>>({});
  const [roleBusy, setRoleBusy] = useState<string | null>(null);
  const [roleMsg, setRoleMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const [costInput, setCostInput] = useState(() => String(budget.max_cost ?? 0));
  const [tokenInput, setTokenInput] = useState(() => String(budget.max_tokens ?? 0));
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetMsg, setBudgetMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const [adv, setAdv] = useState<{
    loading: boolean;
    value: string;
    error: string | null;
  }>({ loading: false, value: "", error: null });
  const [advBusy, setAdvBusy] = useState(false);
  const [advMsg, setAdvMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const rolePlan = useMemo(() => {
    const names: string[] = [...ROLE_ORDER];
    for (const key of Object.keys(roles)) {
      if (!names.includes(key)) names.push(key);
    }
    return names.map((role) => {
      const meta: RoleMeta | undefined = ROLE_META[role];
      const spec: RoleSpec | undefined = roles[role];
      return {
        role,
        label: meta?.label ?? role,
        desc: meta?.desc ?? "自定义角色",
        billable: meta?.billable ?? true,
        current: spec?.profile || spec?.default || "",
        fallback: spec?.fallback ?? "",
        rules: spec?.rules?.length ?? 0,
      };
    });
  }, [roles]);

  /** 预算回填：只依赖两个数字，避免父组件每次渲染都把输入框重置。 */
  useEffect(() => {
    setCostInput(String(budget.max_cost ?? 0));
    setTokenInput(String(budget.max_tokens ?? 0));
  }, [budget.max_cost, budget.max_tokens]);

  /** 高级参数（块预算 tokens）来自后端设置；读不到就说读不到，不显示猜的值。 */
  useEffect(() => {
    if (!projectId) {
      setAdv({ loading: false, value: "", error: null });
      return;
    }
    let alive = true;
    setAdv({ loading: true, value: "", error: null });
    api
      .settings(projectId)
      .then((s) => {
        if (!alive) return;
        setAdv({
          loading: false,
          value: String(s.translate.chunk_budget_tokens ?? ""),
          error: null,
        });
      })
      .catch((e: unknown) => {
        if (!alive) return;
        setAdv({ loading: false, value: "", error: errText(e) });
      });
    return () => {
      alive = false;
    };
  }, [projectId]);

  const selectionOf = (role: string, current: string): string =>
    pending[role] !== undefined ? pending[role] : current;

  const roleMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const r of rolePlan) map[r.role] = selectionOf(r.role, r.current);
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rolePlan, pending]);

  async function handleRoleChange(role: string, value: string): Promise<void> {
    setPending((p) => ({ ...p, [role]: value }));
    setRoleMsg(null);
    const next: Record<string, string> = { ...roleMap, [role]: value };
    setRoleBusy(role);
    try {
      await onSaveRoles(next);
      setPending((p) => {
        const q = { ...p };
        delete q[role];
        return q;
      });
      setRoleMsg({ tone: "ok", text: "角色路由已保存。" });
    } catch (e) {
      setPending((p) => {
        const q = { ...p };
        delete q[role];
        return q;
      });
      setRoleMsg({ tone: "err", text: `保存失败：${errText(e)}` });
    } finally {
      setRoleBusy(null);
    }
  }

  async function handleSaveBudget(): Promise<void> {
    const maxCost = Number(costInput);
    const maxTokens = Number(tokenInput);
    if (!Number.isFinite(maxCost) || maxCost < 0) {
      setBudgetMsg({ tone: "err", text: "金额上限要填 0 或正数（0 = 不限）。" });
      return;
    }
    if (!Number.isInteger(maxTokens) || maxTokens < 0) {
      setBudgetMsg({ tone: "err", text: "token 上限要填 0 或正整数（0 = 不限）。" });
      return;
    }
    setBudgetBusy(true);
    setBudgetMsg(null);
    try {
      await onSaveBudget({ max_cost: maxCost, max_tokens: maxTokens });
      setBudgetMsg({ tone: "ok", text: "预算闸已保存，下一次作业开始生效。" });
    } catch (e) {
      setBudgetMsg({ tone: "err", text: `保存失败：${errText(e)}` });
    } finally {
      setBudgetBusy(false);
    }
  }

  async function handleSaveAdvanced(): Promise<void> {
    const tokens = Number(adv.value);
    if (!Number.isInteger(tokens) || tokens <= 0) {
      setAdvMsg({ tone: "err", text: "块预算 tokens 要填正整数。" });
      return;
    }
    setAdvBusy(true);
    setAdvMsg(null);
    try {
      await onSave({ chunk_budget_tokens: tokens });
      setAdvMsg({ tone: "ok", text: "已保存，下次翻译起生效。" });
    } catch (e) {
      setAdvMsg({ tone: "err", text: `保存失败：${errText(e)}` });
    } finally {
      setAdvBusy(false);
    }
  }

  return (
    <section className="panel models" aria-labelledby="models-title">
      <header className="models-head">
        <h2 id="models-title">模型</h2>
        <p className="hint models-lead">
          档案是<b>全局</b>的、只读；角色路由决定「谁干什么活」，在本项目里改。
        </p>
      </header>

      {/* ---------- 档案 ---------- */}
      <section className="models-block" aria-labelledby="models-profiles-title">
        <div className="models-block__head">
          <h3 id="models-profiles-title">模型档案</h3>
          <span className="muted models-count">{profiles.length} 个档案</span>
        </div>
        <p className="hint">
          档案定义在 <code className="mono">~/.config/booktran/profiles.toml</code>，
          改全局档案所有项目立刻生效，这里只读。
        </p>
        {profiles.length === 0 ? (
          <p className="models-empty">
            还没有档案。在 <code className="mono">~/.config/booktran/profiles.toml</code> 里加一段
            <code className="mono">[profile.名字]</code>，再回到这里刷新即可。
          </p>
        ) : (
          <div className="models-table-wrap">
            <table>
              <caption className="models-caption">全局模型档案（只读）</caption>
              <thead>
                <tr>
                  <th scope="col">名称</th>
                  <th scope="col">能力</th>
                  <th scope="col">模型</th>
                  <th scope="col">价格 in·out（$/Mtok）</th>
                  <th scope="col">密钥状态</th>
                </tr>
              </thead>
              <tbody>
                {profiles.map((p: Profile) => (
                  <tr key={p.id}>
                    <td>
                      <div className="models-name">{p.id || "—"}</div>
                      <div className="models-sub muted">
                        {p.provider} · {KIND_LABELS[p.kind] ?? p.kind}
                      </div>
                    </td>
                    <td>{capsText(p.caps)}</td>
                    <td className="mono">{p.model || "—"}</td>
                    <td className="num">{priceText(p.price_in, p.price_out)}</td>
                    <td>
                      <span className={`tag ${keyTagClass(p.key_state)}`}>
                        {p.key_state || "未找到"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ---------- 角色路由 ---------- */}
      <section className="models-block" aria-labelledby="models-roles-title">
        <div className="models-block__head">
          <h3 id="models-roles-title">角色路由</h3>
          <span className="muted models-count">改动即时保存</span>
        </div>
        <p className="hint">
          角色决定「谁干什么活」。调到带价格的档案就会计费；本地档案（本地模型、OCR）通常不计费。
          模型审校、排版设计、术语分析都是全书级调用，长书请先看预算闸。
        </p>
        <div className="models-table-wrap">
          <table>
            <caption className="models-caption">角色 → 档案</caption>
            <thead>
              <tr>
                <th scope="col">角色</th>
                <th scope="col">档案</th>
                <th scope="col">做什么</th>
                <th scope="col">回落与级联</th>
              </tr>
            </thead>
            <tbody>
              {rolePlan.map((r) => {
                const selectId = `models-role-${r.role}`;
                const value = selectionOf(r.role, r.current);
                const known = profiles.some((p) => p.id === value);
                return (
                  <tr key={r.role}>
                    <th scope="row" className="models-rowhead">
                      <label htmlFor={selectId}>{r.label}</label>
                      <div className="models-sub muted mono">{r.role}</div>
                    </th>
                    <td>
                      <select
                        id={selectId}
                        value={value}
                        disabled={roleBusy === r.role || profiles.length === 0}
                        onChange={(e) => {
                          void handleRoleChange(r.role, e.currentTarget.value);
                        }}
                      >
                        <option value="">未指定（跟随全局默认）</option>
                        {value && !known ? (
                          <option value={value}>{value}（当前设置，不在档案列表）</option>
                        ) : null}
                        {profiles.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.id} · {p.model || p.provider}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <div>{r.desc}</div>
                      {r.billable ? (
                        <span className="tag warn">调用模型 · 按档案价格计费</span>
                      ) : (
                        <span className="tag">一般接本地档案 · 通常不计费</span>
                      )}
                    </td>
                    <td>
                      {r.fallback ? (
                        <div>
                          回落 <span className="mono">{r.fallback}</span>
                        </div>
                      ) : null}
                      {r.rules > 0 ? <div>级联规则 {r.rules} 条</div> : null}
                      {!r.fallback && r.rules === 0 ? <span className="muted">—</span> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {roleMsg ? (
          <p
            className={`models-msg ${roleMsg.tone === "err" ? "is-err" : "is-ok"}`}
            role="status"
          >
            {roleMsg.text}
          </p>
        ) : null}
      </section>

      {/* ---------- 预算闸 ---------- */}
      <section className="models-block" aria-labelledby="models-budget-title">
        <div className="models-block__head">
          <h3 id="models-budget-title">预算闸</h3>
          <span className="muted models-count">只对当前项目生效</span>
        </div>
        <p className="hint">超限即暂停，不静默烧钱。填 0 表示不设上限。</p>
        <div className="models-budget">
          <div className="field">
            <label htmlFor="models-budget-cost">金额上限（美元）</label>
            <input
              id="models-budget-cost"
              type="number"
              min={0}
              step="0.01"
              value={costInput}
              onChange={(e) => setCostInput(e.currentTarget.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="models-budget-tokens">token 上限</label>
            <input
              id="models-budget-tokens"
              type="number"
              min={0}
              step="1"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.currentTarget.value)}
            />
          </div>
          <button
            type="button"
            className="primary"
            onClick={() => {
              void handleSaveBudget();
            }}
            disabled={budgetBusy || !projectId}
          >
            {budgetBusy ? "保存中…" : "保存预算"}
          </button>
        </div>
        {budgetMsg ? (
          <p
            className={`models-msg ${budgetMsg.tone === "err" ? "is-err" : "is-ok"}`}
            role="status"
          >
            {budgetMsg.text}
          </p>
        ) : null}
      </section>

      {/* ---------- 高级参数（默认收起） ---------- */}
      <section className="models-block" aria-labelledby="models-adv-title">
        <details className="models-adv">
          <summary id="models-adv-title">高级参数（默认收起）</summary>
          <div className="models-adv__body">
            <p className="hint">
              并发数、RPM、超时等更多调参在{" "}
              <code className="mono">~/.config/booktran/config.toml</code> 里改。
            </p>
            {adv.loading ? (
              <p className="muted" role="status">
                正在读取当前设置…
              </p>
            ) : adv.error ? (
              <p className="models-msg is-err" role="status">
                读取设置失败：{adv.error}
              </p>
            ) : (
              <div className="models-budget">
                <div className="field">
                  <label htmlFor="models-chunk-tokens">
                    块预算 tokens（每次请求送进模型的文本上限）
                  </label>
                  <input
                    id="models-chunk-tokens"
                    type="number"
                    min={1}
                    step={100}
                    value={adv.value}
                    onChange={(e) => setAdv({ loading: false, value: e.currentTarget.value, error: null })}
                    disabled={advBusy || !projectId}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => {
                    void handleSaveAdvanced();
                  }}
                  disabled={advBusy || !projectId}
                >
                  {advBusy ? "保存中…" : "保存"}
                </button>
              </div>
            )}
            {advMsg ? (
              <p
                className={`models-msg ${advMsg.tone === "err" ? "is-err" : "is-ok"}`}
                role="status"
              >
                {advMsg.text}
              </p>
            ) : null}
          </div>
        </details>
      </section>
    </section>
  );
}
