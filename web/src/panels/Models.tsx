import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";

import { api } from "../api";
import type { Profile, ProviderTemplate, RoleSpec } from "../types";
import "./Models.css";

export interface ModelsProps {
  projectId: string | null;
  profiles: Profile[];
  roles: Record<string, RoleSpec>;
  budget: { max_cost: number; max_tokens: number };
  onSave: (body: Record<string, unknown>) => Promise<void>;
  onSaveRoles: (roles: Record<string, string>) => Promise<void>;
  onSaveBudget: (budget: { max_cost: number; max_tokens: number }) => Promise<void>;
  onChanged?: () => void;
}

const ROLE_ORDER = ["translate", "proofread", "typeset", "vlm", "ocr", "analyze"] as const;

interface RoleMeta { label: string; desc: string; billable: boolean }

const ROLE_META: Record<string, RoleMeta> = {
  translate: { label: "翻译", desc: "把原文翻成目标语言", billable: true },
  proofread: { label: "校对", desc: "模型审校（qa 模式不调模型）", billable: true },
  typeset: { label: "排版", desc: "设计排版样式表", billable: true },
  vlm: { label: "视觉识别", desc: "扫描页 / 图片识别", billable: true },
  ocr: { label: "OCR", desc: "本地文字识别", billable: false },
  analyze: { label: "术语分析", desc: "全文提取专有名词", billable: true },
};

const CAP_LABELS: Record<string, string> = { text: "文本", vision: "视觉", ocr: "OCR", embed: "向量" };
const KIND_LABELS: Record<string, string> = { chat: "对话", vision: "视觉", ocr: "文字识别", local: "本地" };

function errText(e: unknown): string { return e instanceof Error ? e.message : String(e); }
function fmtPrice(v: number | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `$${v.toFixed(2)}`;
}
function capsText(caps: string[]): string {
  return caps?.length ? caps.map((c) => CAP_LABELS[c] ?? c).join("·") : "—";
}
function keyTagClass(state: string): string {
  if (!state || state.includes("未找到")) return "err";
  if (state.includes("档案内")) return "warn";
  return "ok";
}

export function Models(props: ModelsProps): JSX.Element {
  const { projectId, profiles, roles, budget, onSave, onSaveRoles, onSaveBudget, onChanged } = props;
  const [pending, setPending] = useState<Record<string, string>>({});
  const [roleBusy, setRoleBusy] = useState<string | null>(null);
  const [roleMsg, setRoleMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [costInput, setCostInput] = useState(() => String(budget.max_cost ?? 0));
  const [tokenInput, setTokenInput] = useState(() => String(budget.max_tokens ?? 0));
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetMsg, setBudgetMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [adv, setAdv] = useState<{ loading: boolean; value: string; error: string | null }>({ loading: false, value: "", error: null });
  const [advBusy, setAdvBusy] = useState(false);
  const [advMsg, setAdvMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  // 档案 CRUD
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Profile | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  const rolePlan = useMemo(() => {
    const names: string[] = [...ROLE_ORDER];
    for (const key of Object.keys(roles)) { if (!names.includes(key)) names.push(key); }
    return names.map((role) => {
      const meta: RoleMeta | undefined = ROLE_META[role];
      const spec: RoleSpec | undefined = roles[role];
      return { role, label: meta?.label ?? role, desc: meta?.desc ?? "自定义角色", billable: meta?.billable ?? true, current: spec?.profile || spec?.default || "", fallback: spec?.fallback ?? "", rules: spec?.rules?.length ?? 0 };
    });
  }, [roles]);

  useEffect(() => { setCostInput(String(budget.max_cost ?? 0)); setTokenInput(String(budget.max_tokens ?? 0)); }, [budget.max_cost, budget.max_tokens]);

  useEffect(() => {
    if (!projectId) { setAdv({ loading: false, value: "", error: null }); return; }
    let alive = true;
    setAdv({ loading: true, value: "", error: null });
    api.settings(projectId).then((s) => { if (alive) setAdv({ loading: false, value: String(s.translate.chunk_budget_tokens ?? ""), error: null }); }).catch((e) => { if (alive) setAdv({ loading: false, value: "", error: errText(e) }); });
    return () => { alive = false; };
  }, [projectId]);

  const selectionOf = (role: string, current: string): string => pending[role] !== undefined ? pending[role] : current;
  const roleMap = useMemo(() => { const m: Record<string, string> = {}; for (const r of rolePlan) m[r.role] = selectionOf(r.role, r.current); return m; }, [rolePlan, pending]);

  async function handleRoleChange(role: string, value: string) {
    setPending((p) => ({ ...p, [role]: value })); setRoleMsg(null);
    const next: Record<string, string> = { ...roleMap, [role]: value };
    setRoleBusy(role);
    try { await onSaveRoles(next); setRoleMsg({ tone: "ok", text: "角色路由已保存。" }); }
    catch (e) { setRoleMsg({ tone: "err", text: `保存失败：${errText(e)}` }); }
    finally { setPending((p) => { const q = { ...p }; delete q[role]; return q; }); setRoleBusy(null); }
  }

  async function handleSaveBudget() {
    const maxCost = Number(costInput); const maxTokens = Number(tokenInput);
    if (!Number.isFinite(maxCost) || maxCost < 0) { setBudgetMsg({ tone: "err", text: "金额上限要填 0 或正数（0 = 不限）。" }); return; }
    if (!Number.isInteger(maxTokens) || maxTokens < 0) { setBudgetMsg({ tone: "err", text: "token 上限要填 0 或正整数（0 = 不限）。" }); return; }
    setBudgetBusy(true); setBudgetMsg(null);
    try { await onSaveBudget({ max_cost: maxCost, max_tokens: maxTokens }); setBudgetMsg({ tone: "ok", text: "预算闸已保存。" }); }
    catch (e) { setBudgetMsg({ tone: "err", text: `保存失败：${errText(e)}` }); }
    finally { setBudgetBusy(false); }
  }

  async function handleSaveAdvanced() {
    const tokens = Number(adv.value);
    if (!Number.isInteger(tokens) || tokens <= 0) { setAdvMsg({ tone: "err", text: "块预算 tokens 要填正整数。" }); return; }
    setAdvBusy(true); setAdvMsg(null);
    try { await onSave({ chunk_budget_tokens: tokens }); setAdvMsg({ tone: "ok", text: "已保存。" }); }
    catch (e) { setAdvMsg({ tone: "err", text: `保存失败：${errText(e)}` }); }
    finally { setAdvBusy(false); }
  }

  async function handleProfileSave(profile: Profile, isNew: boolean) {
    try {
      if (isNew) await api.createProfile(profile as unknown as Record<string, unknown>);
      else await api.updateProfile(profile.id, profile as unknown as Record<string, unknown>);
      setShowForm(false); setEditing(null); onChanged?.();
    } catch (e) { alert(`保存失败：${errText(e)}`); }
  }

  async function handleProfileDelete(pid: string) {
    try { await api.deleteProfile(pid); setConfirmDel(null); onChanged?.(); }
    catch (e) { alert(`删除失败：${errText(e)}`); }
  }

  return (
    <section className="panel models" aria-labelledby="models-title">
      <header className="models-head">
        <h2 id="models-title">模型</h2>
        <p className="hint models-lead">档案是全局的；角色路由决定「谁干什么活」，在本项目里改。</p>
      </header>

      {/* 档案 */}
      <section className="models-block" aria-labelledby="models-profiles-title">
        <div className="models-block__head">
          <h3 id="models-profiles-title">模型档案</h3>
          <div className="models-actions">
            <span className="muted models-count">{profiles.length} 个档案</span>
            <button className="primary" onClick={() => { setEditing(null); setShowForm(true); }}>+ 添加档案</button>
          </div>
        </div>
        {profiles.length === 0 ? (
          <p className="models-empty">还没有档案。点「添加档案」配置你的第一个模型。</p>
        ) : (
          <div className="models-table-wrap">
            <table>
              <caption className="models-caption">全局模型档案</caption>
              <thead>
                <tr><th scope="col">名称</th><th scope="col">能力</th><th scope="col">模型</th><th scope="col">价格 in·out（$/Mtok）</th><th scope="col">密钥状态</th><th scope="col">操作</th></tr>
              </thead>
              <tbody>
                {profiles.map((p: Profile) => (
                  <tr key={p.id}>
                    <td><div className="models-name">{p.id || "—"}</div><div className="models-sub muted">{p.provider} · {KIND_LABELS[p.kind] ?? p.kind}</div></td>
                    <td>{capsText(p.caps)}</td>
                    <td className="mono">{p.model || "—"}</td>
                    <td className="num">{p.price_in === null && p.price_out === null ? "—" : `${fmtPrice(p.price_in)} / ${fmtPrice(p.price_out)}`}</td>
                    <td><span className={`tag ${keyTagClass(p.key_state)}`}>{p.key_state || "未找到"}</span></td>
                    <td>
                      <div className="models-row-actions">
                        <button className="ghost" onClick={() => { setEditing(p); setShowForm(true); }}>编辑</button>
                        {confirmDel === p.id ? (
                          <span className="models-confirm">
                            <button className="danger" onClick={() => void handleProfileDelete(p.id)}>确认</button>
                            <button className="ghost" onClick={() => setConfirmDel(null)}>取消</button>
                          </span>
                        ) : (
                          <button className="ghost danger" onClick={() => setConfirmDel(p.id)}>删除</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 角色路由 */}
      <section className="models-block" aria-labelledby="models-roles-title">
        <div className="models-block__head">
          <h3 id="models-roles-title">角色路由</h3>
          <span className="muted models-count">改动即时保存</span>
        </div>
        <p className="hint">调到带价格的档案就会计费；本地档案（本地模型、OCR）通常不计费。</p>
        <div className="models-table-wrap">
          <table>
            <caption className="models-caption">角色 → 档案</caption>
            <thead><tr><th scope="col">角色</th><th scope="col">档案</th><th scope="col">做什么</th><th scope="col">回落与级联</th></tr></thead>
            <tbody>
              {rolePlan.map((r) => {
                const selectId = `models-role-${r.role}`;
                const value = selectionOf(r.role, r.current);
                const known = profiles.some((p) => p.id === value);
                return (
                  <tr key={r.role}>
                    <th scope="row" className="models-rowhead"><label htmlFor={selectId}>{r.label}</label><div className="models-sub muted mono">{r.role}</div></th>
                    <td>
                      <select id={selectId} value={value} disabled={roleBusy === r.role || profiles.length === 0} onChange={(e) => { void handleRoleChange(r.role, e.currentTarget.value); }}>
                        <option value="">未指定（跟随全局默认）</option>
                        {value && !known ? <option value={value}>{value}（当前设置，不在档案列表）</option> : null}
                        {profiles.map((p) => <option key={p.id} value={p.id}>{p.id} · {p.model || p.provider}</option>)}
                      </select>
                    </td>
                    <td>
                      <div>{r.desc}</div>
                      {r.billable ? <span className="tag warn">调用模型 · 按档案价格计费</span> : <span className="tag">一般接本地档案 · 通常不计费</span>}
                    </td>
                    <td>
                      {r.fallback ? <div>回落 <span className="mono">{r.fallback}</span></div> : null}
                      {r.rules > 0 ? <div>级联规则 {r.rules} 条</div> : null}
                      {!r.fallback && r.rules === 0 ? <span className="muted">—</span> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {roleMsg ? <p className={`models-msg ${roleMsg.tone === "err" ? "is-err" : "is-ok"}`} role="status">{roleMsg.text}</p> : null}
      </section>

      {/* 预算 */}
      <section className="models-block" aria-labelledby="models-budget-title">
        <div className="models-block__head"><h3 id="models-budget-title">预算闸</h3><span className="muted models-count">只对当前项目生效</span></div>
        <p className="hint">超限即暂停，不静默烧钱。填 0 表示不设上限。</p>
        <div className="models-budget">
          <div className="field"><label htmlFor="models-budget-cost">金额上限（美元）</label><input id="models-budget-cost" type="number" min={0} step="0.01" value={costInput} onChange={(e) => setCostInput(e.currentTarget.value)} /></div>
          <div className="field"><label htmlFor="models-budget-tokens">token 上限</label><input id="models-budget-tokens" type="number" min={0} step="1" value={tokenInput} onChange={(e) => setTokenInput(e.currentTarget.value)} /></div>
          <button type="button" className="primary" onClick={() => { void handleSaveBudget(); }} disabled={budgetBusy || !projectId}>{budgetBusy ? "保存中…" : "保存预算"}</button>
        </div>
        {budgetMsg ? <p className={`models-msg ${budgetMsg.tone === "err" ? "is-err" : "is-ok"}`} role="status">{budgetMsg.text}</p> : null}
      </section>

      {/* 高级参数 */}
      <section className="models-block" aria-labelledby="models-adv-title">
        <details className="models-adv">
          <summary id="models-adv-title">高级参数（默认收起）</summary>
          <div className="models-adv__body">
            {adv.loading ? <p className="muted" role="status">正在读取当前设置…</p>
              : adv.error ? <p className="models-msg is-err" role="status">读取设置失败：{adv.error}</p>
              : (
                <div className="models-budget">
                  <div className="field"><label htmlFor="models-chunk-tokens">块预算 tokens（每次请求送进模型的文本上限）</label><input id="models-chunk-tokens" type="number" min={1} step={100} value={adv.value} onChange={(e) => setAdv({ loading: false, value: e.currentTarget.value, error: null })} disabled={advBusy || !projectId} /></div>
                  <button type="button" onClick={() => { void handleSaveAdvanced(); }} disabled={advBusy || !projectId}>{advBusy ? "保存中…" : "保存"}</button>
                </div>
              )}
            {advMsg ? <p className={`models-msg ${advMsg.tone === "err" ? "is-err" : "is-ok"}`} role="status">{advMsg.text}</p> : null}
          </div>
        </details>
      </section>

      {/* 档案编辑表单 */}
      {showForm && <ProfileForm profile={editing} onCancel={() => { setShowForm(false); setEditing(null); }} onSubmit={(p, isNew) => void handleProfileSave(p, isNew)} />}
    </section>
  );
}

/* ------------------------------------------------------------------ 档案编辑表单 */

function ProfileForm(props: { profile: Profile | null; onCancel: () => void; onSubmit: (p: Profile, isNew: boolean) => void }): JSX.Element {
  const { profile, onCancel, onSubmit } = props;
  const isNew = !profile;
  const [providers, setProviders] = useState<ProviderTemplate[]>([]);
  const [tplProvider, setTplProvider] = useState("");
  const [tplModel, setTplModel] = useState("");
  const [tplLoading, setTplLoading] = useState(true);
  const [id, setId] = useState(profile?.id ?? "");
  const [kind, setKind] = useState(profile?.kind ?? "chat");
  const [provider, setProvider] = useState(profile?.provider ?? "anthropic");
  const [model, setModel] = useState(profile?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(profile?.base_url ?? "");
  const [keyEnv, setKeyEnv] = useState(profile?.key_env ?? "");
  const [priceIn, setPriceIn] = useState(profile?.price_in ?? null);
  const [priceOut, setPriceOut] = useState(profile?.price_out ?? null);
  const [concurrency, setConcurrency] = useState(4);

  // 加载 models.dev 供应商模板
  useEffect(() => {
    let alive = true;
    api.providers().then((r) => { if (alive) { setProviders(r.providers); setTplLoading(false); } }).catch(() => { if (alive) setTplLoading(false); });
    return () => { alive = false; };
  }, []);

  function applyTemplate(pid: string, mid: string) {
    setTplProvider(pid); setTplModel(mid);
    if (!pid || !mid) return;
    const tp = providers.find((x) => x.id === pid);
    if (!tp) return;
    const tm = tp.models.find((x) => x.id === mid);
    if (!tm) return;
    // 自动填充字段
    setProvider(tp.id === "anthropic" ? "anthropic" : tp.id === "openai" ? "openai" : "openai");
    setBaseUrl(tp.api);
    setKeyEnv(tp.env[0] ?? "");
    setModel(tm.id);
    setPriceIn(tm.price_in);
    setPriceOut(tm.price_out);
    if (tm.vision) setKind("vision");
    if (!isNew && id === "") setId(tp.id);
    if (!isNew && id !== "" && id !== profile?.id) return;
    if (isNew) setId(tp.id);
  }

  function submit() {
    if (!id.trim()) return;
    onSubmit({ id: id.trim(), kind, provider, model: model.trim(), base_url: baseUrl.trim(), key_env: keyEnv.trim(), price_in: priceIn, price_out: priceOut, concurrency, caps: [], api_key: "", api_keys: [], rpm: 0, max_output_tokens: 8192, temperature: null, timeout: 300, max_retries: 4, prompt_preset: "default", price_cache_read: null, explicit: [], key_state: keyEnv.trim() ? `env:${keyEnv.trim()}` : "未找到" }, isNew);
  }

  return (
    <div className="pf-backdrop" role="presentation" onClick={onCancel}>
      <div className="pf-modal" role="dialog" aria-label={isNew ? "添加档案" : `编辑档案：${profile?.id}`} onClick={(e) => e.stopPropagation()}>
        <h3>{isNew ? "添加模型档案" : `编辑档案：${profile?.id}`}</h3>

        {/* models.dev 模板选择 */}
        <div className="pf-tpl">
          <span className="pf-tpl__label">从模板选择</span>
          <select value={tplProvider} onChange={(e) => { setTplProvider(e.target.value); setTplModel(""); }} aria-label="选择供应商">
            <option value="">{tplLoading ? "加载中…" : "手动填写"}</option>
            {providers.map((tp) => <option key={tp.id} value={tp.id}>{tp.name}</option>)}
          </select>
          {tplProvider && (
            <select value={tplModel} onChange={(e) => applyTemplate(tplProvider, e.target.value)} aria-label="选择模型">
              <option value="">选择模型…</option>
              {providers.find((x) => x.id === tplProvider)?.models.map((m) => (
                <option key={m.id} value={m.id}>{m.name}{m.price_in != null ? ` · $${m.price_in}/$${m.price_out}` : ""}</option>
              ))}
            </select>
          )}
        </div>

        <div className="pf-grid">
          <div className="field"><label htmlFor="pf-id">档案名称（唯一 ID）</label><input id="pf-id" type="text" value={id} disabled={!isNew} placeholder="如 claude / deepseek / qwen-local" onChange={(e) => setId(e.target.value)} /></div>
          <div className="field"><label htmlFor="pf-kind">类型</label><select id="pf-kind" value={kind} onChange={(e) => setKind(e.target.value)}><option value="chat">对话（文本翻译）</option><option value="vision">视觉（图片 / 扫描页）</option><option value="ocr">OCR（本地文字识别）</option><option value="local">本地模型</option></select></div>
          <div className="field"><label htmlFor="pf-provider">Provider</label><select id="pf-provider" value={provider} onChange={(e) => setProvider(e.target.value)}><option value="anthropic">Anthropic</option><option value="openai">OpenAI 兼容</option><option value="deepseek">DeepSeek</option><option value="ollama">Ollama（本地）</option><option value="vllm">vLLM（本地）</option><option value="fake">Fake（测试）</option></select></div>
          <div className="field"><label htmlFor="pf-model">模型名称</label><input id="pf-model" type="text" value={model} placeholder="如 claude-sonnet-4-5 / qwen2.5:7b" onChange={(e) => setModel(e.target.value)} /></div>
          <div className="field"><label htmlFor="pf-url">Base URL（留空用默认）</label><input id="pf-url" type="text" value={baseUrl} placeholder="https://api.example.com/v1" onChange={(e) => setBaseUrl(e.target.value)} /></div>
          <div className="field"><label htmlFor="pf-key">密钥来源（环境变量名）</label><input id="pf-key" type="text" value={keyEnv} placeholder="如 ANTHROPIC_API_KEY" onChange={(e) => setKeyEnv(e.target.value)} /></div>
          <div className="field"><label htmlFor="pf-price-in">输入价格（$/Mtok，留空=未知）</label><input id="pf-price-in" type="number" min="0" step="0.01" value={priceIn ?? ""} placeholder="3.0" onChange={(e) => setPriceIn(e.target.value ? Number(e.target.value) : null)} /></div>
          <div className="field"><label htmlFor="pf-price-out">输出价格（$/Mtok，留空=未知）</label><input id="pf-price-out" type="number" min="0" step="0.01" value={priceOut ?? ""} placeholder="15.0" onChange={(e) => setPriceOut(e.target.value ? Number(e.target.value) : null)} /></div>
          <div className="field"><label htmlFor="pf-conc">并发数</label><input id="pf-conc" type="number" min="1" max="64" value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value) || 4)} /></div>
        </div>
        <div className="pf-actions"><button onClick={onCancel}>取消</button><button className="primary" onClick={submit} disabled={!id.trim()}>{isNew ? "添加" : "保存"}</button></div>
      </div>
    </div>
  );
}
