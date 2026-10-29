/** 与后端契约一一对应的类型（后端见 src/booktran/model.py、web/app.py）。 */

export type SegmentStatus = "pending" | "translated" | "excluded";
export type StageState =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "skipped"
  | "stale";

export interface Track {
  target: string;
  status: SegmentStatus;
  error: string | null;
  model: string | null;
  stages: Record<string, StageState>;
  revisions: { at: number; old: string; new: string; source: string }[];
}

export interface Segment {
  sid: string;
  source: string;
  status: SegmentStatus;
  meta: Record<string, unknown>;
  tracks: Record<string, Track>;
  kind: string;
  level: number;
  src_lang: string;
}

export interface DocInfo {
  relpath: string;
  fmt: string;
  total: number;
  translated: number;
  excluded: number;
}

export interface Progress {
  total: number;
  translated: number;
  excluded: number;
  pending: number;
  errored: number;
  percent: number;
}

export interface LangProgress {
  total: number;
  translated: number;
  pending: number;
  errored: number;
  percent: number;
}

export interface CostBucket {
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost: number;
  /** 该桶里有几次请求的档案没配置价格（费用未知，不计入金额） */
  unpriced: number;
}

export interface CostSummary {
  total: CostBucket;
  by_role: Record<string, CostBucket>;
  by_stage: Record<string, CostBucket>;
  by_model: Record<string, CostBucket>;
  by_lang: Record<string, CostBucket>;
}

export interface Stats {
  requests: number;
  failed_chunks: number;
  prompt_tokens: number;
  completion_tokens: number;
  retried_requests: number;
  cost: number;
  unpriced_requests: number;
}

export interface TaskState {
  state: "idle" | "running" | "done" | "error";
  message: string;
}

export interface Project {
  id: string;
  path: string;
  source: string;
  created_at: string;
  target_langs: string[];
  target_lang: string;
  source_lang: string;
  provider: string;
  model: string;
  roles: Record<string, string>;
  docs: DocInfo[];
  progress: Progress;
  per_lang: Record<string, LangProgress>;
  stats: Stats;
  cost: CostBucket;
  budget: { max_cost: number; max_tokens: number };
  task: TaskState;
}

export interface Profile {
  id: string;
  kind: string;
  provider: string;
  model: string;
  base_url: string;
  caps: string[];
  price_in: number | null;
  price_out: number | null;
  key_env: string;
  key_state: string;
}

export interface RoleSpec {
  profile?: string;
  default?: string;
  fallback?: string;
  rules?: { match: Record<string, unknown>; use: string }[];
}

export interface Issue {
  sid: string;
  lang: string;
  kind: string;
  severity: "error" | "warn" | "info";
  message: string;
  suggestion: string;
  source: string;
  target: string;
}

export interface QualityReport {
  summary: {
    total: number;
    by_kind: Record<string, number>;
    by_severity: Record<string, number>;
    by_lang: Record<string, number>;
  };
  issues: Issue[];
}

export interface Glossary {
  terms: {
    src: string;
    dst?: string;
    note?: string;
    [k: string]: unknown;
  }[];
  non_translate: string[];
  style: string;
  notes: string;
  system_prompt: string;
  background: Record<string, string> | null;
}

export interface Settings {
  llm: {
    provider: string;
    model: string;
    base_url: string;
    has_api_key: boolean;
    api_key_count: number;
    concurrency: number;
    rpm: number;
    max_output_tokens: number;
    prompt_preset: string;
  };
  translate: {
    source_lang: string;
    target_langs: string[];
    chunk_budget_tokens: number;
    rounds: number;
    zh_convert: string;
    layout: string;
  };
  platform: string;
  platforms: string[];
  profiles: Profile[];
  roles: Record<string, RoleSpec>;
  budget: { max_cost: number; max_tokens: number };
}

/** SSE 事件（后端 Progress._emit 的载荷）。 */
export type StreamEvent =
  | { type: "state"; state: string; message: string; progress: Progress }
  | { type: "stage"; stage: string; round: number; rounds: number; total: number; lang: string }
  | { type: "stage_end"; stage: string; ok: boolean; detail: string; done: number; failed: number }
  | { type: "chunk"; label: string; ok: boolean; detail: string }
  | { type: "issue" } & Issue
  | { type: "done"; done: number; failed: number; cost: number | null };
