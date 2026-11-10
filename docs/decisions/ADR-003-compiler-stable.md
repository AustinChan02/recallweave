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

## §7.4 数证补录（2026-11-10）：线上半已跑——稳定性证得，命中差值不可 Claim

`bench/cache_ab.py` 的两半现在都落在 write-once 文件里：

**离线半**（`bench/results/cache-ab-2026-11-09-1.json`）：同一记忆集 6 轮，
v2（内容寻址）恒 1 种前缀、v1（score 漂移）漂出 6 种——只证"稳定性前提"；
cache-hit/TTFT 在 mock 下不可测，恒 null（诚实半）。

**线上半**（`bench/results/cache-ab-online-2026-11-10-1.json`，schema
`rw-bench-cache-ab-online/1`）：同一记忆集、每轮把 v2 与 v1 两个 system
块**都发给真 provider（deepseek-flash）**，6 轮 × 2 臂 = 12 次调用，每次
prompt 142 tok。实测（provider 上报 usage，非本地估算）：

| 量 | v2 stable | v1 drift |
|---|---|---|
| unique_prefixes | 1（恒稳定） | 6（漂移） |
| `prompt_cache_hit_tokens` 合计 | **0** | **0** |
| mean wall（n=6/臂） | 2403.5 ms | 3362.3 ms |
| prompt tok 合计 | 852 | 852 |

诚实结论分三层：

1. **可 Claim（已证）**：铁律一"集合不变 → 前缀字节全同"在真 provider
   流量下复证（v2 恒 1 种前缀；v1 漂出 6 种，与离线半一致）。
2. **不可 Claim（数据不支持）**：**本次实验不存在 cache 命中差值**——
   12 次调用的 `prompt_cache_hit_tokens` **字段存在且全为 0**。这是
   "测得 0"，不是"没测"：字段名在 provider 的 usage payload 里真实出现，
   缺字段才会走"未测"分支。两臂同为 0 → v2/v1 命中差值无从比较，
   任何"v2 命中省钱"的数字都不成立。
3. **待定假设（明确标注为假设，不是结论）**：单调用前缀仅 142 tok，
   可能低于 provider cache 的计费/分块粒度；或 cache 需要
   流量/时间窗预热；或该字段语义并非"计费折扣命中"。落定途径 =
   DeepSeek 控制台 cache 日志对拍（用户侧动作，以环境真实状态为准）。

其余不可算项维持 null：`ttft_ms`（adapter 非流式，wall 是**整响应**
延迟；n=6/臂、单次试验，2403 vs 3362 不作任何显著性/首 token 论断）；
`cost_yuan` 待控制台核价。护栏：上界 ¥0.05904 / 上限 ¥5。

简历口径：§7.4 目前**只有"前缀稳定性"这一半可引用**；cache 命中差值
在控制台对拍给出非零证据前，一律不写进简历或 README 表格。
