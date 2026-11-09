# ADR-003 — Context Compiler 纯算法、无 LLM，与 cache-stable 硬规则

日期：2026-11-09 ｜ 状态：已实现

## 背景
提案 §4.3 定调：编译器是"纯算法、零 LLM"，这样 p95 低、零调用成本、
且可以做 cache-stable 实验（§7.4 招牌实验）。

## 决定
1. **零 LLM**：`compile_context` 不触碰任何 adapter，只读 DB + 数数。
2. 输出形态固定：`[system(persona→PROFILE→RECENT EVENTS)] + [working tail]`。
3. **铁律一（内容寻址）**：profile 条目按 `md5(content)` 排序输出——
   记忆集合不变 → 前缀字节全同（`bench/cache_ab.py` 实验已实证：同一
   集合连跑 6 轮，v2 恒 1 种前缀 vs v1 score-order 漂移出 6 种）。
4. **铁律二（易变后置）**：注入条目不携带时间戳/相对时间；工作态原文
   严格排在 system 块之后。
5. 预算四分（占总预算）：system 10% / profile 30% / episodic 40% /
   working 20%。working 从最旧侧裁剪到份额内；**单条消息超限不拦腰截断**
   （soft cap，砍掉用户当前轮是"静默丢失"的一种）。
6. trace 字段 = 提案 §4.3 要求的 {id, kind, score, rank, budget_slot}
   + {tokens, included, reason, source_ref}，截断在 60 条（文档化上限）。

## 实现偏差登记（提案修订）
- 提案把 profile/episodic 配额写成"占 system block 的百分比"；修订为
  **占四层总预算的百分比**（system10/profile30/episodic40/working20 并列），
  语义更直白、且 validator 强制 sum==1。
- episodic 层暂由 event 类记忆承担（§4.4 摘要是 P1 未实现）。

## 为什么不调 LLM 补写
三句话：成本（每轮一次 LLM 调用 → 测评的 token 成本单位失去意义）、
延迟（p95 抖动）、确定性（同一集合必须产出同一字节，LLM 签名不可复现）。
