# ADR-004 — 双时态版本链：UPDATE/DELETE 只封口，不覆盖

日期：2026-11-09 ｜ 状态：已实现

## 背景
提案 §3/§4.1：UPDATE 改写 target 的最新 content，旧版本 superseded；
DELETE 旧事实失效并记 superseded_by。禁止覆盖的原因有两层：
1. "何时说过什么"是记忆系统的第一手价值（面试点：为什么覆盖毁历史）；
2. LLM 消解会错判，版本链是唯一的自动回滚底座（误删除可从链恢复）。

## 决定
- 每条 memory 有 `valid_from / valid_to`（默认 now 封口）；DELETE/UPDATE
  将旧行置 `status='superseded' + valid_to=now`，UPDATE 再列表
  `superseded_by`；rationale 全量进 `meta.close_reason[]` 可审计。
- 物理删除只出现在用户显式 DELETE 记忆的 `status='deleted'`（软删）。
  遗忘=分层存储（archive，P1），不做物理抹除（提案 §4.5）。
- **写事务规则**（实现中发现的真实 bug，教训 worth noting）：来自
  `repo.by_ids` 的 neighbor 是另一个 session 的 detached 对象——直接改
  字段不会进 flush。封口动作必须先 `session.get(Memory, id)` 把行 attach
  进当前写事务。devlog 2026-11-09 有事故记录。

## 可选扩展（不做）
- `GET /v1/memories/{id}/history` 沿 superseded_by 链反向遍历——P2。
