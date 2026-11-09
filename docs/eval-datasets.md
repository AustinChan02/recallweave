# 评测数据集：来源 / 许可 / 预处理 / 已知偏差

> 与 `configs/datasets.toml`（唯一 pin 出处）一致；数据集**不入仓**（§7.1），
> 转换子集可由 pin 源 + 参数**字节级复现**（已验证）。
> mini_eval.jsonl 仍是仓内合成冒烟集（非基准），不产生质量结论。

## 1) LongMemEval-S（cleaned，主数据集）

| 字段 | 值 |
|---|---|
| 来源 | HF `xiaowu0162/longmemeval-cleaned`，split `longmemeval_s_cleaned.json` |
| 版本 pin | sha256 `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442`（= HF LFS oid + 本地下载双侧验证，277,383,467 bytes） |
| 许可 | **MIT**（HF 卡片 + 官方 repo LICENSE 双证） |
| 引用 | Wu et al., ICLR 2025, arXiv:2410.10813 |
| 2026 口径 | 明确标注使用 **cleaned 版**（2025-09 去除干扰会话；非原始版） |

- 体量：500 题，题均历史 ~115k tokens（40-50 会话）。
- 类型分布：multi-session 133 / temporal-reasoning 133 / knowledge-update 78 /
  single-session-user 70 / single-session-assistant 56 / single-session-preference 30。

## 2) LoCoMo（locomo10.json）

| 字段 | 值 |
|---|---|
| 来源 | github `snap-research/locomo` @ commit `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`（commit 级不可变 URL） |
| 版本 pin | sha256 `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`（2,805,274 bytes） |
| 许可 | **CC BY-NC 4.0**（repo LICENSE.txt「Attribution-NonCommercial 4.0」+ 论文附录 B.2）→ 非商业评测使用，README 已披露 |
| 引用 | Maharana, Lee et al., ACL 2024, arXiv:2402.17753 |

- 体量：10 段超长对话；QA 1,986 条（cat1 单跳 282 / cat2 多跳 321 / cat3 时序 96 /
  cat4 开放域 841 / cat5 对抗 446）。

## 预处理（`scripts/download_data.py`，代码即文档）

1. **抽取配额（面向能力覆盖，非自然分布）**：
   - LME 30 题分层：multi-session 6 / temporal 6 / knowledge-update 5 /
     single-user 5 / single-assistant 4 / single-preference 4；
   - LoCoMo 30 题分层：single-hop 8 / multi-hop 8 / temporal 6 /
     open-domain 4 / adversarial 4（稀有类型有意提升）。
   - 选样 `random.Random(seed=2026)`；**重跑字节级一致**（已实测 diff=∅）。
2. **会话日期注入**：LME 每会话首条前缀 `[Session date: <date>]`；LoCoMo 每会话
   注入 `[Session N date_time: <dt>]` 行。原因：两个基准的日期都在元数据而非
   正文，不注入则时序类问题对人/模型都不可答（诚实偏差处理而非假装可答）。
3. **LoCoMo 说话人映射**：全部轮次 `role=user`，内容前缀真实说话人名
   （`Caroline: …`）。原因：评测管线在 user 轮做记忆抽取，只保留一方会静默
   偏置结果（问答双方话题都有）；名字进内容保证下游提示词可读。
4. LME 保留基准原生 `user/assistant` 角色（忠实于其协议）。
5. 输出：`data/datasets/converted/<id>-t30-seed2026.jsonl`（gitignored，
   3.1MB / 15.4MB）；出处/参数/sha256 记录在
   `evals/fixtures/converted-manifest.json`（committed）。

## 已知偏差（诚实清单）

- 记忆抽取覆盖 user-role 轮；assistant 轮内容只通过 C0 截窗/C3 recent 层部分
  进入上下文 → `single-session-assistant` 类问题对记忆系统偏难（这是测得的
  真实弱点，不算 bug；mock 抽取为占位，真抽取同理只提取用户事实）。
- C3 compile 预算 600 tokens：对 115k-token 历史是极端检索压力场景
  （这正是评测目标）；LME 115k 全文读入的 full-context baseline 不在对比范围。
- 评测 harness 的抽取/冲突解决为 mock（sticky NOOP）：本评测测的是
  **检索/编译上下文质量 + 回答 + judge** 这一链路；抽取质量属另一评测面，
  未测，不混报。
- LoCoMo cat4（开放域知识）部分答案可能在会话外（数据集注"adversarial/open"），
  judge 只对问答一致性打分，无法区分"记忆失败"与"参考本身开放"→ 该类结果
  解读时单独声明。

## 不可行边界

许可若升级/变更：重 pin（sha256 变化）→ 结果文件与旧 pin 的可比性断裂，
须在 devlog 单开一段声明换版重跑，不允许新旧混报。
