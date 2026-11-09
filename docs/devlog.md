# RecallWeave 开发日志（devlog）

> 「记住所有试错」是提案 §13 的要求：这些记录同时是面试素材。
> 惯例：只记事实与可复现证据；mock 相关数字永远带 provider 标签。

## 2026-11-09 · Day 1 完成 MVP（环境 → 全量实现 → 验证）

### 环境事实
- 系统 Python 3.9.6（过旧，不可用）；一切 Python 通过 `uv run`（uv 0.12.19 管理 cpython 3.12.14）。
- `uv venv --python 3.12` + `uv sync --extra dev` 可复现；依赖坑：`sqlalchemy[asyncio]` 才带 `greenlet`，声明成裸 `sqlalchemy` 会在 import `sqlalchemy.ext.asyncio` 时报 ImportError——已修 pyproject。
- Docker 29.8 在位，pg 路径验证入口：`make db-up` + `RECALLWEAVE_TEST_PG_URL=... uv run pytest -m integration`。

### 生成内容的质量防线（工具性事实）
本仓大量文件由 agent 生成，出现过多个类别的"生成噪声"，全部通过统一防线捕获：
1. 每次写/改文件后 `py_compile` 全仓扫描（曾抓出 `session.py` import 被挤成一行、`compiler.py` 残留半截字段等）；
2. grep 扫非预期字符集（韩文/西里尔/阿拉伯字符区间，0 命中为通过）；
3. 对文档逐篇 read-back：抓出「测试uish」「结果文件 chef」「Adam 参数」「DataOps 地hemisphere」之类的中英混杂碎片并修复。
防线不是流程装饰：三步在 Day 1 内各自至少抓到过一次真实缺陷。

### 跨后端双 bug（最有面试价值的两课）
1. **SQLite Uuid 无连字符陷阱**：SQLAlchemy `Uuid` 在 SQLite 以 32 位 hex（无 `-`）存储，而 `memory_embeddings` 行是我们自己以带连字符字符串写入的。裸 SQL `JOIN m.id = e.memory_id` 因此**永远匹配不上**（症状：向量检索静默返回空）。修复：读路径改为两步——ORM 类型化 select 取候选（绑定/结果处理保持一致）+ 用 dash 键 `IN` 查 embedding 行；「跨格式字符串 JOIN 在任何地方都不允许」成为本仓规则。`touch_last_accessed` 同理改为 ORM `update()`，避免裸绑 UUID 对象。
2. **游离对象 mutation 不落库**：写入管道里 UPDATE/DELETE 的 target 是经 `repo.by_ids`（另一个 session）取出的 detached 对象，直接改 `status/valid_to/superseded_by` 根本不会 flush（症状：e2e 断言 `old.status == "superseded"` 失败，库里仍是 active）。修复：写事务内 `await session.get(Memory, id)` 重新 attach 后再封口；`service.delete_memory` 同病同修。**教训：跨事务复用 ORM 实例 = 静默丢写。**

### Compiler 的两处真实缺陷
- `tzinfo=timezone`（应为 `timezone.utc`）——把类型名当实例传了，在 naive datetime 分支必崩。
- 账目不平：`system_tokens` 数的是整条 system 消息，而 layers 只数了三段内容，section header 行的黑户 token 无处入账。重构为 `_system_parts` 产出 `(budget_slot, line)` 明细：persona/headers/profile/episodic 逐行入桶，断言 `system_tokens == Σlayers` 精确成立（headers 独立成一个桶，诚实公开）。

### LLM adapter 重试语义（fail-closed 的精确化）
- 最终形态：单一 `_with_retries` 覆盖**传输类**（429/5xx/超时/传输错误，retry-after 头被尊重）与**schema 类**（strict JSON 解析失败也算可重试），共享受界重试预算；4xx 永久失败不重试。
- 踩过的类层次坑：`ProviderSchemaError` 继承自 `ProviderError`，except 顺序把 schema 错误先撞进「永久失败」分支 → schema 漂移不再重试。修复=catch 顺序必须子类在前。这类"继承树改变异常路由"的坑值得记一辈子。
- `MockLLMAdapter` 语义：FIFO 脚本队列，**队列耗尽默认 raise（fail-closed）**；仅评测 harness 用显式 `sticky=...` 参数声明确定性兜底应答（评测文件里披露，不是静默默认）。

### 测试确定性策略（哈希 embedding 的副作用）
hash 相似度内容相关 → "必须走 LLM 路由"的用例**不可依赖**默认阈值恰好落窗：
- 路由本身：表驱动纯函数测试（sim=None/0.5/0.79/0.85/0.929/0.93/0.99 → add/llm/near_dup）。
- 走 LLM 用例：force `conflict_lo=0.001, dedup_near=0.999` 钉死路由。
- near-dup skip：默认阈值下用**完全相同内容**（cosine 恰为 1.0 ≥ 0.93），与阈值联结自然成立。
- demo 想真实触发 UPDATE 链：实测挑了一对 cosine≈0.8853 的表述（"吃了花生会起疹子" vs "吃了花生酱也会起疹子"），天然落入 (0.80, 0.93) 窗。
最终基线：`uv run pytest` → **73 passed, 1 skipped**（skip=需 Docker 的 pg 集成，门控 `RECALLWEAVE_TEST_PG_URL`）。

### 诚实评测协议落地形态
- `evals/run_eval.py`：write-once 结果文件（目标存在即 SystemExit 拒写）；`unjudged` 计数与分母剔除都发生在 `ConditionRun.metrics()`；`cost_yuan/ttft_ms/cache_hit_tokens` 恒 null + 原因。
- 离线四条件（C0/C1/C2/C3）跑通 mini 6 题：C0 与 C3 的 context-evidence recall 均为 0.75（judged 6/6）——注意！这是离线**代理指标**（keyword judge + echo 上下文），唯一的用途是证明 harness 各环节是通的，不可当作质量结论引用。
- 数据集（LongMemEval/LoCoMo）未下载：许可与版本 pin 未完成之前，`scripts/download_data.py` 明确拒绝假装工作。

### 基准（全部带范围标签）
- `bench/latency.py`（sqlite + hash，单线程进程内）：1000 条记忆时 compile p95 ≈ 34ms / search p95 ≈ 30ms。**这不是生产数字**（生产是 pg+HNSW + 真 embedding），只说明纯算法路径的可延迟性。
- `bench/cache_ab.py`：同一记忆集合 6 轮，v2（内容寻址）前缀哈希恒 1 种 vs v1（得分序模拟漂移）6 种——证明的是 cache-stable 的**前提（字节稳定性）**；缓存命中/TTFT 的另一半需要真 provider 用量数据，离线不测不编。

### pg 路径首轮验证的三个真 bug（make db-up 后现场抓的）
1. **pgvector 强制声明宽度**：列是 `vector(512)`，集成测试想省事用 16 维 → `expected 512 dimensions, not 16`。实验代码必须用与生产一致的形状，"任意宽度"的注释是错觉。
2. **asyncpg 拒绝在 `SET` 里绑定参数**：`SET LOCAL hnsw.ef_search = :ef` → `syntax error at or near "$1"`（工具语句不支持预处理参数）。修复：`SELECT set_config('hnsw.ef_search', :ef, true)`——`is_local=true` 的事务作用域语义与 `SET LOCAL` 等价，且安全绑定。
3. 修完之后：`RECALLWEAVE_TEST_PG_URL=... pytest -m integration` → **1 passed**（512 维 HNSW 排序 + write-once schema + 真 Postgres 上的 supersede 链）。

### Settings 校验器的教训
预算四层从「三层」改为「四层占总预算」时，测试里只 override 单层导致 sum==1 validator 拒绝（0.9 / 1.1 / 0.72 各踩一次）。validator 是对的，测试是错的——这类快速失败正是配置校验的价值。

## 修正登记（对 proposal 的回写）
见 `agent-proposals/A-RecallWeave/PROPOSAL.md` §14 修订记录（A1–A7），对应 ADR-001..006。

## 待办（诚实未做清单）
- [ ] 真 provider 评测运行（DeepSeek/Qwen/GLM，预算 ≤¥200）→ 从此数字才可上简历【实测填入】。
- [ ] LongMemEval-S / LoCoMo：pin 版本 + license → `docs/eval-datasets.md` → 30 题人工复核流程。
- [ ] P1：会话摘要（§4.4）、遗忘归档（§4.5）、`/v1/reflect` 真实现。
- [ ] pg 集成测试在本机跑过一轮（Docker 起库 + `RECALLWEAVE_TEST_PG_URL`）。
- [ ] mypy strict 与 CI（Makefile 留位）。
