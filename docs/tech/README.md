# docs/tech/ — 模块级技术深潜(参考手册)

> 本目录是**已实现事实**的模块级参考手册。每一条字段、默认值、阈值、端点均标注来源路径
> (如 `src/recallweave/read/compiler.py`);设计取舍的"为什么"不在本目录展开,一律指向
> `docs/decisions/ADR-001..006`。高层地图见 `docs/architecture.md`(架构总览)与
> `docs/api.md`(API 快查);本目录是它们下钻一层:同一事实体系,粒度到函数与列。

## 与既有文档的分工

| 文档 | 角色 | 读法 |
|---|---|---|
| `README.md`(仓根) | 作品集门面:特性、快速开始、评测结论摘要 | 5 分钟了解项目 |
| `docs/architecture.md` | 高层架构地图 + 与 proposal 的偏差登记索引 | 理解"有哪些模块、什么决定" |
| `docs/api.md` | API 快查表(9 端点 + 错误码 + SDK 示例) | 日常调用查这张 |
| `docs/decisions/ADR-001..006` | 每个技术取舍的背景/决定/代价 | 想知道"为什么这么设计" |
| **`docs/tech/0x-*.md`(本目录)** | **模块深潜:逐字段、逐函数、逐端点** | 改代码/排障/对接时查 |
| `docs/devlog.md` | 全程试错记录(保留全部失败) | 面试/复盘素材 |

## 阅读地图

| 篇 | 内容 | 给谁读 |
|---|---|---|
| [01-system-overview.md](01-system-overview.md) | 三条路径(写/读/管理)+ 唯一组合点 `RecallWeaveService` + 一次写入/一次检索的完整调用链 + 配置单一入口 + 身份模型 | 第一次读代码的人 |
| [02-write-pipeline.md](02-write-pipeline.md) | 提取提示词与严格 JSON 契约 → 近重门 → 冲突消解 → 双时态落库;P1.a 会话摘要 / P1.b 分层遗忘 / P1.c reflect 聚簇 | 改写路径、调阈值、排查"为什么没存进去"的人 |
| [03-read-path.md](03-read-path.md) | 三路召回(vector/lexical/recency)→ RRF/加权/MMR 融合 → Context Compiler 四层装配与 cache-stable 三规则 → trace 结构 | 改检索/编译、对接 prompt 缓存的人 |
| [04-data-model.md](04-data-model.md) | 全部表逐列(pg migration + ORM)、双后端差异面、向量索引双实现、检索 SQL 路径 | 改 schema、写迁移、管数据库的人 |
| [05-api-reference.md](05-api-reference.md) | 9 个端点逐个:请求/响应 JSON 实测样例、字段约束、错误语义、Bearer 认证 | 调用方、联调 |
| [06-adapters-and-configuration.md](06-adapters-and-configuration.md) | LLM 适配器契约与重试语义、MockLLM FIFO/sticky、嵌入双实现、全部 `RECALLWEAVE_*` 环境变量表 | 部署、配置、接真实 provider 的人 |
| [07-evaluation-and-benchmarks.md](07-evaluation-and-benchmarks.md) | 评测四条件 C0–C3、离线 proxy 与在线协议、成本护栏、数据集 pin、bench 脚本 scope | 想引用数字、想跑评测的人 |
| [08-testing-and-operations.md](08-testing-and-operations.md) | 测试布局与标记、质量门、CI 双 job、scripts/ 逐个、故障排查 | 贡献者、运维 |

## 硬性口径(全目录适用)

1. **诚实红线**:每个事实标注来源路径;代码里没有的就写"未实现",不推断不编造。
2. **数字纪律**:性能/评测数字只引用 write-once 文件(`evals/results/`、`bench/results/`),
   不复述超出原文的结论;凡 `provider=mock` / `embedding=hash` 的产物一律标注
   "机制展示,非实测"。
3. **与 README/architecture.md 冲突时以代码为准**,每篇文末列"差异说明"。
4. 代码标识符、路径、配置键保留英文反引号;图一律 ASCII(与仓库文档风格一致)。

## 按任务找篇

| 你要做的事 | 先读 | 再读 |
|---|---|---|
| 第一次看懂这个代码库 | 01 | architecture.md |
| 改写路径 / 调冲突阈值 | 02 | ADR-004、ADR-005 |
| 改检索或编译器 / 接 prompt 缓存 | 03 | ADR-002、ADR-003 |
| 改表结构 / 写迁移 / 管数据库 | 04 | ADR-001 |
| 对接 HTTP / 联调 | 05 | api.md |
| 部署 / 接真实 provider / 改配置 | 06 | .env.example、docker-compose.yml |
| 跑评测 / 引用数字 | 07 | ADR-006、docs/eval-datasets.md |
| 跑测试 / 排障 / 日常运维 | 08 | Makefile、ci.yml |

## 关键事实速查表(各篇交叉引用,详细出处见对应篇)

| 事实 | 值 | 出处 |
|---|---|---|
| HTTP 端点数 | 9(冒号路由 4 + UUID 路径 2 + 无体 3) | `api/routes.py`,05 篇 |
| RRF k | 60 | `core/config.py:51` |
| MMR λ | 0.7 | `core/config.py:52` |
| 近重门 / 冲突窗 | `dedup_near=0.93` / `conflict_lo=0.80` | `core/config.py:47-48`,ADR-005 |
| 聚簇阈值(严格 >) | `reflect_threshold=0.95` | `core/config.py:88` |
| 邻居窗口 | `neighbor_top_n=8` | `core/config.py:64` |
| 三路深度 | vector 32 / lexical 32 / recency 8,扫描上限 2000 | `core/config.py:67-70` |
| 四层预算 | 10% / 30% / 40% / 20%,默认总预算 1500 | `core/config.py:73-77`,ADR-003 |
| 摘要触发 | 每 20 轮(`summarize_every_turns`) | `core/config.py:80` |
| 遗忘条件 | 未访问 > 30 天 **且** importance < 0.3(均严格 <) | `core/config.py:85-86` |
| 向量维度 | 512(BGE-small-zh-v1.5 实际维度;768 为提案笔误) | `migrations/001_init_vector.sql`,ADR-001 |
| HNSW 参数 | m=16, ef_construction=64, cosine ops;运行时 `ef_search=40` | 001_init_vector.sql + `core/config.py:44` |
| kind 权重 | fact 1.0 / preference 1.1 / instruction 1.2 / event 0.9 | `core/config.py:54-57` |
| importance 乘子 | 0.7 + importance × 0.6 | `core/config.py:58-59` |
| 迁移表清单 | `memories`、`mem_sources`、`session_turn_stats`、`session_summaries`、`memory_embeddings` | 04 篇 |
| Settings 字段数 | 48(46 标量 + 2 Path),前缀 `RECALLWEAVE_` | `core/config.py`,06 篇 |
| 测试基线 | 112 passed + 1 skipped(2026-11-13 实测) | `uv run pytest`,08 篇 |
| trace 上限 | 60 条(`_TRACE_LIMIT`) | `read/compiler.py:49` |
| 评测协议 | 离线 = `context-evidence-recall (offline proxy)`;在线 = `qa-accuracy (real provider, LLM-judged)` | `evals/run_eval.py:58-59`,ADR-006 |

## 术语表

| 术语 | 定义 | 首现 |
|---|---|---|
| fail-closed | 结构化失败 ≠ 成功:重试耗尽后跳过并上报,绝不写半成品 / 绝不伪造输出 | 01 篇 §8 |
| write-once | 结果文件目标已存在即拒绝写(覆盖 = 让后跑藏住前跑) | 07 篇 §3 |
| 双时态 | `valid_from/valid_to` 有效窗 + `superseded_by` 版本链;UPDATE/DELETE 只封口不覆盖 | 02 篇 §3.4 |
| provider 标签 | 每个响应/结果行标注数据由哪种模式产生;mock 永不冒充实测 | 01 篇 §7 |
| sticky | MockLLMAdapter 的显式确定性兜底参数(队列空时),评测文件必须披露,非静默默认 | 06 篇 §2 |
| unjudged | judge 失败/不可判的题:出 accuracy 分母,单独报告 | 07 篇 §3 |
| cache-stable | 记忆集合不变 → 编译产物前缀字节全同(provider 前缀缓存的前提) | 03 篇 §3 |
| 分层存储 | 遗忘 = archive(可恢复),物理删除只有用户显式 DELETE | 02 篇 §5 |

## 各篇目录速览

**01-system-overview.md**:1 组合点规则 · 2 三条路径方法对照 · 3 一次写入的完整调用链 ·
4 一次检索的完整调用链 · 5 配置单一入口 · 6 身份模型 · 7 provider 标签 ·
8 错误模型 · 9 模块速查

**02-write-pipeline.md**:1 提取(提示词/JSON 契约/重试) · 2 近重门与冲突消解(路由/四操作/coercion) ·
3 应用与事务边界(IngestReport/per-candidate fail-closed/双时态落库) ·
4 P1.a 会话摘要 · 5 P1.b 分层遗忘 · 6 P1.c reflect 聚簇

**03-read-path.md**:1 三路召回(vector/lexical/recency + 为何不用 tsvector) ·
2 融合(RRF/权重/MRR/`rrf_detail`/计算实例) · 3 Context Compiler(四层/cache-stable 三规则/
token 估算/trace/边界行为) · 4 检索 vs 编译差异

**04-data-model.md**:1 表清单与建立方式 · 2 `memories` 逐列 · 3 `mem_sources` ·
4 P1.a 两表 · 5 `memory_embeddings` 双后端(维度 512 由来) · 6 检索 SQL 路径 ·
7 双时态语义 · 8 repo 公共方法与事务规则

**05-api-reference.md**:1 端点总表 · 2 请求 schema 约束 · 3 端点逐个(实测 JSON) · 4 错误语义

**06-adapters-and-configuration.md**:1 LLM 适配器契约 · 2 MockLLM FIFO/sticky ·
3 嵌入双实现 · 4 全部环境变量(7 张分表) · 5 docker compose 与 `.env.example`

**07-evaluation-and-benchmarks.md**:1 四条件 C0–C3 · 2 离线 proxy 红线 ·
3 在线协议(拒跑三连/协议不变量) · 4 成本护栏 · 5 数据集 pin ·
6 正式结果与如实解读 · 7 bench scope(latency/cache_ab 双半)

**08-testing-and-operations.md**:1 测试布局与确定性策略 · 2 质量门 · 3 CI 双 job ·
4 scripts/ 逐个 · 5 SDK 用法与离线演示链 · 6 故障排查

## 维护约定

- 代码变更触及本目录描述的事实时,应同步更新对应篇并在该篇"差异说明"节注明;
- 新增性能/评测数字:只允许引用 `evals/results/`、`bench/results/` 里新增的
  write-once 文件,并保留 provider 标签语境;
- 本目录不收录 proposal 原文、ADR 全文或 devlog 摘抄——只做指向与事实对齐。

## 常见问题导航

| 问题 | 答案位置 |
|---|---|
| 为什么 mock 模式下 `/v1/memories:extract` 返回 `extraction_error` 而不是记忆? | 08 篇 §6 第 3 行;机制根因在 06 篇 §2 |
| 为什么 UPDATE 后旧记忆还在库里? | 02 篇 §3.4(双时态只封口);检索面行为在 04 篇 §8 `active_candidates` |
| 为什么 `/v1/context:compile` 两次输出前缀不一致/一致? | 03 篇 §3 cache-stable 三规则;实证在 07 篇 §7.2 |
| `cost_yuan` 为什么是 null? | 07 篇 §4(控制台实价未核对前保持 null,ADR-006) |
| C3 的 accuracy 为什么没写进结论? | 07 篇 §6(30 题人工复核是门槛;负结果如实呈报) |
| SQLite 路径向量检索为什么是两步取回? | 04 篇 §5(Uuid 无连字符陷阱,禁止跨格式 JOIN) |
| 迁移加载器为什么要先剥注释再切分? | 08 篇 §3(CI 首跑抓到的真 bug + 回归测试) |
| 反映记忆重复 `/v1/reflect` 会删行吗? | 02 篇 §6(只做 status 迁移,永不物理删除) |
| 怎么给新用户发身份? | 06 篇 §4.1(`RECALLWEAVE_API_KEYS_JSON` 增加映射,token→UUID) |
| 编译器会调 LLM 吗? | 03 篇 §3(纯算法零 LLM,provider 恒 `local-compiler`) |
| `last_accessed` 什么时候被写? | 03 篇 §4(search 命中条 / compile 中 included 的非摘要条;best-effort) |
| 评测的 answer 是谁写的? | 07 篇 §2/§3(echo 模式=上下文回显;provider 模式=真回答模型,judge 判回答) |

## 本目录在文档体系中的位置

```
README.md(仓根)            作品集门面:特性 / 快速开始 / 评测结论摘要
docs/architecture.md        高层架构地图 + 偏差登记索引(ADR)
docs/api.md                 API 快查表(9 端点 / 错误码 / SDK 示例)
docs/decisions/ADR-001..006 取舍的"为什么"(本目录只引用不重复)
docs/devlog.md              全程试错记录(保留全部失败)
docs/eval-datasets.md       数据集来源 / 许可 / 预处理 / 偏差
docs/tech/(本目录)          模块深潜:逐字段 / 逐函数 / 逐端点(本事实体系的最细层)
   ├─ 01 系统总览            ├─ 02 写路径       ├─ 03 读路径
   ├─ 04 数据模型            ├─ 05 API 参考     ├─ 06 适配器与配置
   └─ 07 评测与基准          └─ 08 测试与运维
```

冲突裁决顺序(本目录口径):**代码 > 本目录 > api.md/architecture.md/README > ADR 历史快照**;
每篇文末"差异说明"登记具体不一致点及其代码出处。
