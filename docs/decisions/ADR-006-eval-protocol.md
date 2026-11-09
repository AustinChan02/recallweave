# ADR-006 — 评测协议：未判定不入分母 + 结果文件 write-once

日期：2026-11-09 ｜ 状态：已实现（离线部分）/ 待真供应商运行

## 背景
提案 §7.3 的核心卖点：凡是评测可以被"合法低成本地作假"的位置，都用规则关死。

## 已落地的协议不变量（代码强制，不是约定）
1. **unjudged 出分母**：judge 失败/超格式 → 该题 judged=False，`accuracy`
   分母剔除，并在 metrics 里单独报 `judged/total/unjudged`。
   `evals/conditions.py::ConditionRun.metrics`。
2. **write-once 结果**：文件名 `eval-<date>-<condition>-run<N>.json`；
   目标文件已存在 → 进程 SystemExit 拒写（覆盖即作弊）。汇总表
   `summary-<date>-<run_id>.md` 同样 write-once、声明"由原始文件派生"。
3. **provider 全披露**：结果文件固定字段 `provider{llm, embedding, judge,
   note}` + 协议名义 `context-evidence-recall (offline proxy)`。
   mock 产物旁注"不得作为实测引用"。
4. **离线不可算的指标一律 null**：`cost_yuan / ttft_ms / cache_hit_tokens`
   恒为 null + 原因注释；绝不拿估算冒充——`tokens_total_estimate`
   是唯一填的字段（本地 tiktoken token 计数，标 est）。

## 与提案的偏差登记（§7.3/§7.4 修订）
- 离线"accuracy"是**上下文证据覆盖**代理（answer=echo 上下文文本），
  不是 QA 准确率。真率数字需要：真 provider 回答 + 真 judge + 30 题人工
  复核。这条已在结果文件与 devlog 声明；`--judge llm` 已留接口。
- LongMemEval/LoCoMo 未落地：先 pin 版本+许可（`scripts/download_data.py`
  明说不可运行并列出待办）；mini_eval.jsonl 是仓内合成冒烟集。

### 在线化修订（2026-11-09，代码已落地：schema `rw-eval-result/2`）
- 新增真答步：`--answer-mode provider` 把（memory 系统块 + 提案答案原则 +
  转写 + 问题）发给真实回答模型；**judge 判的是回答，不是上下文回显**。
  echo 模式不变（`run3` 与 `run2` 逐项 score/rationale/tokens/provider 对照
  一致，协议字节等价已验证）。
- 拒跑三连（fail-closed 在网络调用之前）：provider 模式必须 `openai_compat`
  且 LLM judge；真实回答永不给 mock keyword judge；见
  `evals/run_eval.py::main`。
- 成本护栏：`evals/pricing.CostGuard` + `configs/prices.toml`——**保守上界**
  单价（占位，待控制台核对），每次真调用（回答 + judge 都计）累计，
  超 `--max-cost-yuan` 即 SystemExit、不落盘（实测验证：0.0017¥ > 1e-05
  上限 → 目录都未创建）。
- 用量落账：answer/judge tokens 取 provider usage payload（事实）；
  `tokens_context_est` 替代旧 `tokens_total_estimate`（命名口径：本地估算），
  本地估算仅给上下文列。
- 成本字段语义：`cost_yuan_upper_est`（护栏上界估算）≠ `cost_yuan`
  （控制台实价，须 §7.4 对拍后才填，表达为 null + 原因）。
- mock 冒烟口径不变：`--judge mock` + `--answer-mode echo` 仍是离线代理协议。
  30 题人工复核模板见 `evals/human-review-template.md`。

## 招牌实验（§7.4 cache A/B）现状
`bench/cache_ab.py` 已能证明**稳定性前提**（同一记忆集 v2 恒字节、
v1 漂移）；命中率/TTFT 的另一半需要真供应商用量数据，离线不做、不编。
