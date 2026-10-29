/** 后端 API 客户端：类型化的 fetch + SSE 订阅。 */
import type {
  CostSummary,
  Glossary,
  Issue,
  Project,
  QualityReport,
  Settings,
  StreamEvent,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* 保留 statusText */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });

const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const api = {
  projects: () => request<Project[]>("/api/projects"),
  project: (id: string) => request<Project>(`/api/project/${id}`),
  reload: (id: string) => post<Project>(`/api/project/${id}/reload`),
  remove: (id: string) => request<void>(`/api/project/${id}`, { method: "DELETE" }),

  settings: (id: string) => request<Settings>(`/api/project/${id}/settings`),
  saveSettings: (id: string, body: Record<string, unknown>) =>
    put<Settings>(`/api/project/${id}/settings`, body),

  profiles: () =>
    request<{ profiles: Settings["profiles"]; roles: Settings["roles"] }>(
      "/api/profiles",
    ),

  translate: (id: string, body: Record<string, unknown> = {}) =>
    post<{ ok: boolean; message?: string }>(`/api/project/${id}/translate`, body),
  analyze: (id: string) =>
    post<{ ok: boolean; message?: string }>(`/api/project/${id}/analyze`),
  proofread: (id: string, mode: "qa" | "llm") =>
    post<{ ok: boolean; message?: string }>(`/api/project/${id}/proofread`, { mode }),
  cancel: (id: string) => post<{ ok: boolean; message: string }>(`/api/project/${id}/cancel`),
  export: (id: string, body: Record<string, unknown>) =>
    post<{ ok: boolean; files: string[] }>(`/api/project/${id}/export`, body),
  files: (id: string) => request<{ name: string; size: number }[]>(`/api/project/${id}/files`),
  fileUrl: (id: string, name: string) =>
    `/api/project/${id}/file/${encodeURIComponent(name)}`,
  previewUrl: (id: string, lang: string) =>
    `/api/project/${id}/preview/${encodeURIComponent(lang)}?t=${Date.now()}`,

  glossary: (id: string) => request<Glossary>(`/api/project/${id}/glossary`),
  saveGlossary: (id: string, body: Glossary) => put<void>(`/api/project/${id}/glossary`, body),

  quality: (id: string) => request<QualityReport>(`/api/project/${id}/quality`),
  applySuggestion: (id: string, issue: Issue, target: string) =>
    post<{ ok: boolean }>(`/api/project/${id}/quality/apply`, {
      sid: issue.sid,
      lang: issue.lang,
      target,
      source: "proofread",
    }),

  cost: (id: string) => request<CostSummary>(`/api/project/${id}/cost`),

  segments: (id: string, doc?: string, offset = 0, limit = 50) =>
    request<{
      doc: string;
      total: number;
      offset: number;
      langs: string[];
      items: import("./types").Segment[];
    }>(
      `/api/project/${id}/segments?doc=${encodeURIComponent(doc ?? "")}&offset=${offset}&limit=${limit}`,
    ),

  revertSegment: (id: string, sid: string, lang: string) =>
    post<{ ok: boolean; target?: string; message?: string }>(
      `/api/project/${id}/segment/${encodeURIComponent(sid)}/revert`, { lang }),
  saveSegment: (id: string, sid: string, target: string, lang: string) =>
    put<void>(`/api/project/${id}/segment/${encodeURIComponent(sid)}`, { target, lang }),
};

/** 订阅作业事件流（SSE）。返回取消订阅函数。 */
export function subscribe(
  projectId: string,
  onEvent: (event: StreamEvent) => void,
  onError?: (err: Event) => void,
): () => void {
  const source = new EventSource(`/api/project/${projectId}/stream`);
  const kinds = ["state", "stage", "stage_end", "chunk", "issue", "done"];
  const handlers = kinds.map((kind) => {
    const handler = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data) as StreamEvent);
      } catch {
        /* 忽略坏事件 */
      }
    };
    source.addEventListener(kind, handler as EventListener);
    return [kind, handler] as const;
  });
  source.onerror = (err) => onError?.(err);
  return () => {
    for (const [kind, handler] of handlers) source.removeEventListener(kind, handler as EventListener);
    source.close();
  };
}
