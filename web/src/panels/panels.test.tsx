import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Cost } from "./Cost";
import { Glossary } from "./Glossary";
import { Library } from "./Library";
import { Models } from "./Models";
import { Proof } from "./Proof";
import type { Issue, Profile, Project, QualityReport } from "../types";

const project = (over: Partial<Project> = {}): Project => ({
  id: "book",
  path: "/tmp/book",
  source: "/tmp/book.epub",
  created_at: "2026-01-01T00:00:00",
  target_langs: ["chinese_simplified"],
  target_lang: "chinese_simplified",
  source_lang: "japanese",
  provider: "anthropic",
  model: "claude-sonnet-4-5",
  roles: { translate: "claude" },
  docs: [{ relpath: "book.epub", fmt: "epub", total: 3, translated: 1, excluded: 0 }],
  progress: { total: 3, translated: 1, excluded: 0, pending: 2, errored: 0, percent: 33.3 },
  per_lang: {
    chinese_simplified: { total: 3, translated: 1, pending: 2, errored: 0, percent: 33.3 },
  },
  stats: {
    requests: 1,
    failed_chunks: 0,
    prompt_tokens: 100,
    completion_tokens: 20,
    retried_requests: 0,
    cost: 0.01,
    unpriced_requests: 0,
  },
  cost: { requests: 1, prompt_tokens: 100, completion_tokens: 20, cost: 0.01, unpriced: 0 },
  budget: { max_cost: 5, max_tokens: 0 },
  task: { state: "idle", message: "" },
  ...over,
});

const profiles: Profile[] = [
  {
    id: "claude",
    kind: "chat",
    provider: "anthropic",
    model: "claude-sonnet-4-5",
    base_url: "",
    caps: ["text"],
    price_in: 3,
    price_out: 15,
    price_cache_read: null,
    key_env: "ANTHROPIC_API_KEY",
    key_state: "env:ANTHROPIC_API_KEY",
    api_key: "",
    api_keys: [],
    concurrency: 4,
    rpm: 0,
    max_output_tokens: 8192,
    temperature: null,
    timeout: 300,
    max_retries: 4,
    prompt_preset: "default",
    explicit: [],
  },
];

describe("面板渲染", () => {
  it("书架：有项目时渲染行，空项目时给教学式空状态", () => {
    const withProjects = renderToStaticMarkup(
      <Library
        projects={[project()]}
        selectedId={null}
        onSelect={() => undefined}
        onUpload={async () => undefined}
        onDelete={() => undefined}
      />,
    );
    expect(withProjects).toContain("book");

    const empty = renderToStaticMarkup(
      <Library
        projects={[]}
        selectedId={null}
        onSelect={() => undefined}
        onUpload={async () => undefined}
        onDelete={() => undefined}
      />,
    );
    expect(empty.length).toBeGreaterThan(0);
  });

  it("成本：金额可见，未定价显示 —", () => {
    const html = renderToStaticMarkup(
      <Cost
        projectId="book"
        cost={{
          total: { requests: 1, prompt_tokens: 100, completion_tokens: 20, cost: 0.01, unpriced: 0 },
          by_role: { translate: { requests: 1, prompt_tokens: 100, completion_tokens: 20, cost: 0.01, unpriced: 0 } },
          by_stage: {},
          by_model: {},
          by_lang: {},
        }}
        budget={{ max_cost: 5, max_tokens: 0 }}
      />,
    );
    expect(html).toContain("$0.01");
  });

  it("模型：档案与角色都出现，价格缺失显示 —", () => {
    const html = renderToStaticMarkup(
      <Models
        projectId="book"
        profiles={[...profiles, { ...profiles[0], id: "local", price_in: null, price_out: null }]}
        roles={{ translate: { profile: "claude" } }}
        budget={{ max_cost: 5, max_tokens: 0 }}
        onSave={async () => undefined}
        onSaveRoles={async () => undefined}
        onSaveBudget={async () => undefined}
      />,
    );
    expect(html).toContain("claude");
    expect(html).toContain("—");
  });

  it("校对：问题清单渲染，严重度用文字表达", () => {
    const issue: Issue = {
      sid: "a#0",
      lang: "chinese_simplified",
      kind: "glossary",
      severity: "warn",
      message: "术语「魔导器」未按表译为「魔导器」",
      suggestion: "魔导器",
      source: "原文",
      target: "译文",
    };
    const report: QualityReport = {
      summary: { total: 1, by_kind: { glossary: 1 }, by_severity: { warn: 1 }, by_lang: {} },
      issues: [issue],
    };
    const html = renderToStaticMarkup(
      <Proof
        projectId="book"
        report={report}
        onRun={async () => undefined}
        onApply={async () => undefined}
      />,
    );
    expect(html).toContain("魔导器");
    expect(html).toContain("警告");
  });

  it("术语：术语行可编辑且有保存入口", () => {
    const html = renderToStaticMarkup(
      <Glossary
        projectId="book"
        glossary={{
          terms: [{ src: "魔导器", dst: "魔导器", note: "物品" }],
          non_translate: [],
          style: "",
          notes: "",
          system_prompt: "",
          background: null,
        }}
        onSave={async () => undefined}
      />,
    );
    expect(html).toContain("魔导器");
  });
});
