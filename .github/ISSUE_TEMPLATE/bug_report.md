---
name: Bug 报告
about: 报告一个可复现的缺陷（请先填完下面的环境与复现信息）
title: "[Bug] "
labels: bug
assignees: ''
---

<!--
填写前请注意：
1. 先搜一遍已有 issue，避免重复。
2. 退出码是有语义的（见 docs/ux/cli.md §6）：
   0 成功 / 2 用法错误 / 3 配置错误 / 4 鉴权失败 / 5 限流耗尽 /
   6 预算超限 / 7 部分成功 / 8 用户中断。带上退出码能省一轮问答。
3. 永远不要在 issue 里贴 API key 或含密钥的日志。只需要回答"是否涉及 API key"。
-->

## 概述

<!-- 一两句话说明问题：哪里坏了、什么时候坏 -->

## 环境

| 项 | 值 |
|---|---|
| booktran 版本（`booktran --version`） | |
| Python 版本（`python3 -V`） | |
| 操作系统 | |
| 安装方式 | `pip install -e ".[...]"` / 其他： |
| 使用界面 | CLI / 网页端 / 两者 |

## 复现步骤

<!-- 编号列出，每步一行，尽量给出可以直接粘贴的命令 -->

```bash
# 例：
# booktran init 我的书.epub --dir /tmp/demo --target-langs chinese_simplified
# booktran run -p /tmp/demo --fake --yes
```

1.
2.
3.

## 实际结果

<!-- 终端输出 / 报错原文 / 页面表现。退出码是多少？ -->

```

```

## 期望结果

<!-- 你以为会发生什么 -->

## 项目状态（`booktran status --json`）

<!-- 直接贴 JSON 输出，不要截图：便于复现。
     贴之前检查里面有没有敏感信息（一般没有，但项目名可能是真实书名，可自行改名）。 -->

```json

```

## 是否涉及 API key

<!-- 只回答"是 / 否"。不要粘贴 key 本身。 -->

- [ ] 否（复现不需要 key，或可以用 --fake 复现）
- [ ] 是（**不要贴 key**；也不要贴含 key 的日志。如果必须提供日志，请先脱敏：把 sk-… 之类替换成 <REDACTED>）

## 相关配置

<!-- 贴配置片段时务必去掉密钥：
     [profile.xxx] 的 key_env 可以留，api_key / Authorization / token 必须去掉 -->

```toml

```

## 其他信息

<!-- 截图、终端录屏、日志片段（已脱敏）、或你觉得相关的一切 -->
