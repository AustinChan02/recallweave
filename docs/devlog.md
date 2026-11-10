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

## 2026-11-09 · Day 2 真 provider 评测上线（阶段 0+1 完成）

### .env 契约与首个真调用
- 用户密钥到位后核对 `.env`：原值 `RECALLWEAVE_LLM_PROVIDER=DeepSeek` 不符 adapter 契约
  （`Literal["mock","openai_compat"]`，启动即崩）→ 修正为 `openai_compat`。
  base_url `https://api.deepseek.com` 可用（adapter 自拼 `/chat/completions`）；
  `GET /models` 实证模型清单 `['deepseek-flash', 'deepseek-v4-pro']`（用户的
  `deepseek-flash` 真实存在）。
- 首个真调用：`adapter.chat_json(EXTRACT_SYSTEM, ...)` → 严格 JSON 抽取成功
  （provider=openai_compat:deepseek-flash，wall=1450ms）。

### Eval 在线化（schema `rw-eval-result/2`）
- **回答步**：`run_harness(answer_adapter=...)` 真分支把（memory 系统块 +
  `ANSWER_SYSTEM` 策略 + 转写（去尾问）+ 显式 QUESTION）发给回答模型；
  judge 判**回答**，不是上下文回显。echo 分支协议不变——`run3`(echo) 与
  `run2` 逐项 score/rationale/tokens/provider 对照一致（协议字节等价复核）。
- **用量记账**：`OpenAICompatAdapter.last_usage`（仅成功调用写入——失败
  调用绝不计 spend）；answer/completion tokens 全部取 provider usage payload，
  本地估算仅剩上下文列（`tokens_context_est` 取代 `tokens_total_estimate`）。
- **judge升级**：`JudgeVerdict{score∈[0,1], rationale}` 严格 schema；
  judge usage 在其调用后读取并同样纳入护栏计费。
- **成本护栏**：`evals/pricing.py`（CostGuard）+ `configs/prices.toml`。
  现阶段单价是**保守上界占位**（deepseek-flash 假设 4/16 ¥/M in/out），
  控制台核对前 `cost_yuan` 恒 null——护栏偏高只会提前中止，绝不会低报。
  现场证据：`--max-cost-yuan 1e-05` → 首次计入 0.0017¥ 即 SystemExit，
  结果目录**连创建都没有发生**。
- **拒跑三连**（网络调用前）：provider 模式须 openai_compat + LLM judge；
  真实回答永不给 mock keyword judge（防"mock judge + 真答冒充实测"）；
  三条分支各自实测触发。

### 真跑记录（mini 6 题 × C0/C3，回答+judge 同模型 deepseek-flash）
- 6/6 judged，C0 accuracy 0.9167 / C3 accuracy 0.9167（**qa-accuracy 协议，
  须 30 题人工复核 + 全量 run 后才可引用进简历**）。
- 24 真调用；3855 in / 1705 out（provider 口径）；占位上界合计 **¥0.0427**
  （护栏上限 ¥2，实际支出远低于上界估算）。
- sample 读回：mini-1 正确拒"吃辣"（0 miss），mini-3 judge 给 0.5 并
  说明"能听粤语/说得一般 表述偏差"——judge 不是全自动满分机器。
- 文件落盘：echo 冒烟 = `run3`（C0/C3，协议标签 offline proxy + 新 schema），
  真跑 = `run4`（C0/C3，协议标签 qa-accuracy·real provider）；run 编号顺延，
  原有 run1/run2 未触碰。

### 测试
- 新增：usage capture（成功后才有值/失败不清零）、CostGuard 累计超额中止、
  price fallback、real-mode wiring（MockLLMAdapter 作 answer adapter 的离线
  真分支全链路）、answer 失败→unjudged、echo 协议回归。
- 基线：**89 passed, 1 skipped**；`make lint` 全绿（52 files formatted）。

### 真 provider 上的量级评测（P0 阶段 2，pin 数据集）
- 数据集 pin + 字节级转换器上线（`9e93932`）：LongMemEval-S **cleaned**（MIT，
  sha256 与 HF LFS oid 双重验证）、LoCoMo（**CC BY-NC 4.0** 非商业，URL pin 到
  commit `3eb6f2c5`）；`--seed 2026 --total 30` 分层抽样，重跑 diff=∅。
  预处理即代码：会话日期注入（时序题可答）、LoCoMo 说话人名内联、LME 原生角色保留。
- **LongMemEval-S-cleaned t30 真跑（run5）**：C0 accuracy **0.000** /
  C3 accuracy **0.000**，judged 30/30，护栏上界 ¥0.865（157k in / 14.7k out）。
  抽查产物：所有回答皆是"上下文没有该信息"——本轮测到的是**管线机制缺失**：
  （1）C0 last-20 截窗在 115k 历史上几乎必然丢证据；（2）mock 抽取每轮写一条
  噪声记忆，候选池 newest-200 截断把相关旧记忆挤出检索视野；（3）hash embedding
  语义能力弱（dev-only 占位）。这三点都是**诚实披露的测得弱点**，不是 bug；
  升级（真抽取 + BGE + 调预算）列为后续实验面，不混入本轮结论。
- **LoCoMo t30 真跑（run6）**：C0 **0.2267** / C3 **0.1667**（judged 30/30，
  上界 ¥0.427）。会话更短、截窗偶有命中，差异与预期一致。
- judge 行为合理性抽查：拒绝给"我不知道"式回答判 0，部分信息给 0.5 并说明
  理由——已非关键词匹配，是人审模板的合理搭档。
- 成本记账：三个真跑（run4/5/6）上界合计 ≈ **¥1.33**，预算纪律成立。

### P1.a 会话摘要（§4.4）落地
- 新表：`session_turn_stats`（触发计数）+ `session_summaries`（(session_id, seq)
  UNIQUE 只追加——顺序即会话序，先天 cache-stable）；`migrations/002`。
- `service.summarize_session`：阈值 20 轮（可配）；**摘要源材 = 会话内已存记忆**
  （A8 修订：原始转写不持久化，隐私面取择）；低于阈值不发起调用；mock 未脚本化
  → ProviderError → `summarized=false` + note，**决不伪造摘要**；脚本化 mock
  （FIFO/sticky）可产出摘要但行内 provider 标签如实标 "mock"。
- 编译器：episodic 槽优先吃会话摘要（取新弃旧至 cap、渲染按时间正序），
  无摘要回退 event 记忆（离线评测协议不受影响——run1/2/3 字节兼容验证过）；
  trace 增 kind=summary、内容寻址 id（`summary:<md5前12位>`）。
- ingest 自动摘要仅对真 provider 开启：脚本化 mock 的队列被抽取/冲突路径共用，
 不让摘要调用挤占脚本（评测 harness 在 mock 下字节不变）。
- 顺手修掉两个真缺陷：(1) `MockLLMAdapter` 的 sticky 传非 JSON 字符串时
  `chat_json` 曾裸抛 JSONDecodeError（fail-open 漏洞）→ 包成 ProviderSchemaError；
  (2) compiler 新增 summary trace 后 `_touch` 把 `summary:<hash>` 当 UUID 解析
 → 已按前缀过滤。
- 测试：95 passed / 1 skipped（新增 6 个 P1.a 用例：阈值边界、追加序、mock no-op、
  编译器接管与回退、ingest 轮计数）。

### P1.b 遗忘 = 分层存储（§4.5）落地
- `MemoryRepo` 三个公开方法：`stale_candidates`（只查不写）/`archive_stale`
  （active → archived，**行与列全保留**；审计追加 `meta.forgotten{at,reason,
  tau_days,importance_lt}`；superseded/deleted 永不触碰）/`restore`
  （archived → active，补 `meta.restored_at`；对非 archived 状态拒绝恢复）。
- 边界语义按提案「宁留勿忘」：两条边界都是**严格 `<`**——恰好 30d 未访问、
  importance 恰为 0.3 的记忆一律保留；从未访问的行以 created_at 计龄
  （`_effective_access`，方言安全地在 Python 侧过滤）。
- 触发面：`scripts/forget.py`（`--dry-run` 直证：先报候选、写零字节）+
  `make forget`；`/v1/stats` 新增 `archived_total` + 分类计数。
- 现场证据（临时 DB 播种 3 行）：dry-run 精确报出唯一 stale+low 候选 →
  真跑 archived 1 → sqlite 直查 **3 行全部仍在**（仅 status 变更）→
  复跑 dry-run 候选归零。顺带修掉一个真 bug：`AsyncEngine` 关闭应是
  `dispose()` 而非 `aclose()`（先前退出码非 0）。
- 测试 +8：边界 ×2、唯一 active 资格、created_at 计龄、审计字段、
  恢复闭环（含二次恢复拒绝）、未知/非归档 id 拒绝、端到端「归档即隐藏检索
  但不删行 + stats 可见 + 恢复后重见」。基线 **103 passed / 1 skipped**。

### P1.c `/v1/reflect` 真实现（501 → 真实的演进完成）
- **时间点**：2026-11-10，`api/routes.py` 里的诚实 501 stub 正式退役——
 面谈卖点：先"诚实拒绝"（不假装工作），再"真实现"（仍不假装决策力，
 失败照实报告）。两端的诚实是一套纪律，不是两种状态。
- 聚簇：active 记忆（≤200 条扫描上限）一次 `embed_many`，纯 Python O(n²)
  余弦 + union-find，阈值 **严格 >** 0.95（恰好阈值不合并——聚簇宁可漏检，
  不把不同记忆熔在一起）。无簇 → **零 LLM 调用**（测试用空队列自证）。
- 每簇一次既有 conflict 管道调用（candidate=最新成员，neighbors=较老成员，
  `resolve_with_llm` + fail-closed coercion 原样复用）；UPDATE → 合并成一行
  新行（importance 取簇内 max，`meta.decision{op,provider,reflect:true,
  cluster:[ids]}` 审计 + bitemporal 链 `mark_superseded` 全员指向新行）；
  DELETE → 仅被点名的 target 关链；NOOP → 记 skipped、行不动。
- **真 bug 落网记**（测试当场抓住）：合并时①candidate 本尊没被 supersede、
  ②detached ORM 副本直接改 flush 看不见——正是 `pipeline.py` 注释里记载过
  的同款坑在 reflect 复现。修复：全部成员在写事务里经 `session.get`
  重attach 后逐个关链；整个 reflection 的应用是**单事务**（与 ingest 同一
  durability rule）。
- 诚实面：未脚本化 mock → `skipped: provider_error_in_resolution`，
  decisions 恒空，行状态零变动（API 级测试固定此面）。修复过程中还抓出
  API 测试自身两个 bug：漏 `svc.bootstrap()`（schema 未建）与 Bearer 固定
  dev-user（种到随机 uid）——证据先行，不猜。
- 基线 **110 passed / 1 skipped**。

## 修正登记（对 proposal 的回写）
见 `agent-proposals/A-RecallWeave/PROPOSAL.md` §14 修订记录（A1–A7），对应 ADR-001..006
（ADR-006 已含 2026-11-09 在线化修订段）。

## 待办（诚实未做清单 → 具体实施步骤见 `docs/TODO.md`）
- [x] 真 provider 评测**框架**上线 + mini 真跑（2026-11-09，C0/C3 均 0.9167，6/6 judged；数字属 mini 冒烟，全量 run + 30 题人工复核前不得进简历）。
- [x] 预算护栏 fail-closed 上线（占位上界价；控制台核对待办见 P0 阶段 3）。
- [x] LongMemEval-S(cleaned)/LoCoMo：pin + license + 确定性转换器 + **30 题×2 条件真跑落盘**（2026-11-09 run5/run6：LME 0.0/0.0，LoCoMo 0.227/0.167；诚实披露的管线机制证据，不作模型能力结论）。`docs/eval-datasets.md` 记录预处理与偏差。
- [ ] 30 题人工复核流程执行 + 控制台价核对 → 全量 run → 回填 `RESUME-READY.md` A§1.4（P0 阶段 3）。
- [x] P1.a：会话摘要（§4.4）实现 + 触发/no-op 语义 + 编译器 episodic 接管（95 passed）。
- [x] P1.b：遗忘=分层存储 archive_stale/restore + `make forget` + stats 可见（103 passed）。
- [x] P1.c：`/v1/reflect` 真实现——聚簇→conflict 管道→单事务应用，501 stub 退役（110 passed）。
- [x] pg 集成测试真跑过一轮（2026-11-09，Docker 起 pgvector，1 passed，修两 bug）。
- [ ] mypy strict 与 CI（`make typecheck` + `.github/workflows/ci.yml`）。
