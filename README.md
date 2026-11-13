<div align="center">

# 🧠 RecallWeave

**长生命周期 Agent 的记忆层 + 上下文编译器 · A memory layer & context compiler for long-lived agents, eval-driven**

不是"又一个 RAG 封装"——是给 Agent 补上**真正难的那两层**:
**写入管道**(extract → 去重 → 冲突消解 → 双时态存储)×
**读出编译器**(三路召回 RRF 融合 → 四层预算装配 → cache-stable 排序)。
每个数字都有脚本与 write-once 结果文件,**mock 永不冒充真实测**。

> 设计原则:写慢读快(读路径纯算法无 LLM)· fail-closed(结构化失败 ≠ 成功,绝不写半成品记忆)· 一切可追溯(每条注入记忆带 id/来源/得分/预算占用)

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector%20%2B%20HNSW-4169E1?logo=postgresql&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-fallback-orange?logo=sqlite&logoColor=white)
![ruff](https://img.shields.io/badge/ruff-format%20%2B%20lint-261230?logo=ruff&logoColor=white)
![mypy](https://img.shields.io/badge/mypy-0%20errors-blue)
![Tests](https://img.shields.io/badge/tests-110%20passing-brightgreen)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions%20(suite%20%2B%20pg)-2088FF?logo=githubactions&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-black)

**📊 关键数字(全部实测,脚本 + write-once 结果文件,见 [证据索引](#-证据索引))**

| 编译器延迟(读路径) | 混合检索 | cache-stable 前缀 | 上下文开销 | 质量 |
|:---:|:---:|:---:|:---:|:---:|
| **p50 7.9ms @100 条**<br>27.8ms @1000 条 | **p50 4.5ms @100 条**<br>三路 RRF 融合 | **1 个唯一前缀**(v2)<br>vs 漂移版 6 个 | **C3 比 C0 省 ~5.6× context**<br>(LoCoMo-t30 同精度带) | **110 tests passing**<br>mypy 0 errors |

</div>

---

## 📑 目录

- [这是什么](#-这是什么)
- [架构](#-架构)
- [核心特性](#-核心特性)
- [界面速览](#-界面速览)
- [快速开始](#-快速开始)
- [一键 Demo](#-一键-demo)
- [API](#-api)
- [评测(招牌模块)](#-评测招牌模块)
- [项目结构](#-项目结构)
- [Roadmap](#-roadmap)
- [已知限制与诚实性](#-已知限制与诚实性)
- [证据索引](#-证据索引)

## 🤔 这是什么

给长生命周期 Agent 做记忆,市面上两条路:要么把 Everything 塞进向量库(检索回来了上下文却超预算、缓存全漂),要么全量历史硬塞(成本爆炸)。**RecallWeave 把"存什么"和"怎么进 prompt"拆成两个受控管道**:

- **写路径(慢,允许 LLM)**:从会话转写提取记忆候选 → cosine 近重门(重复直接跳过)→ 候选 × 邻居冲突消解(`ADD/UPDATE/DELETE/NOOP` + rationale)→ **双时态**入库(`valid_from/valid_to` + `superseded_by` 版本链,UPDATE 只封口不覆盖)
- **读路径(快,纯算法)**:vector ANN / lexical / recency **三路召回** → RRF(k=60)融合 → 类型加权 + importance + MMR(λ=0.7)→ **Context Compiler** 四层装配(persona → profile → episodic → working,预算 10/30/40/20),profile 内容哈希序、易变尾置——**同一个记忆集合编译两次,prompt 前缀逐字节稳定**(provider 前缀缓存的前提)
- **诚实性是一等公民**:每个响应带 `provider` 标签;mock 严格队列制,live 下拒绝编造;评测结果 write-once,`unjudged` 不进分母

适合谁:**给 Agent/聊天产品加长期记忆的后端工程师**、**关心 prompt 缓存命中与上下文成本的平台团队**、**想看"评测驱动开发"怎么落地的建设者**。

## 🏗️ 架构

```text
SDK (sync httpx) ─┐
                  ├─> FastAPI /v1 ──> RecallWeaveService(唯一组合点,SDK 与 HTTP 共用)
CLI/demo ─────────┘                      │
                    ┌────────────────────┼──────────────────────┐
                 写路径(write)         读路径(read)            管理
                    │                    │
    extract(LLM, strict JSON)      三路召回: vector ANN / lexical / recency
      → cosine 近重门(dedup)          → RRF(k=60) 融合 → 类型加权
      → 冲突消解 ADD/UPDATE/          → importance + MMR(λ=0.7) → top-k
        DELETE/NOOP + rationale          │
      → 双时态存储(valid_from/to         ▼
        + superseded_by 版本链)     Context Compiler(纯算法,无 LLM):
                                   四层装配 persona/profile/episodic/working
                                   预算 10/30/40/20,内容哈希序,trace 逐条来源
                       └────────────┴──> SQLAlchemy async(唯一 ORM 层)
                                          pgvector(HNSW)主路径 / SQLite fallback
```

三条铁律(**全部有测试看守**):

1. **fail-closed**:LLM 结构化输出重试后仍失败 → 跳过该条 + 失败日志,绝不写"我认为可能对"的半成品记忆
2. **版本链不删历史**:UPDATE/DELETE 只封口(`valid_to`)+ `superseded_by` 关链;遗忘 = 分层存储(archive,可恢复),物理删除只有显式 DELETE
3. **cache-stable**:编译两次,前缀逐字节相同;profile 用内容哈希序,易变内容尾部放置(ADR-003)

## ✨ 核心特性

| | 特性 | 一句话 |
|---|---|---|
| ✍️ | **写入管道** | LLM 严格 JSON 提取 → 近重余弦门 → 冲突消解四操作,单事务提交 + per-candidate fail-closed |
| 🔍 | **三路混合检索** | vector(pgvector HNSW)+ lexical + recency,RRF(k=60) 融合,响应带 `rrf_detail` 逐路明细 |
| 🧩 | **上下文编译器(招牌)** | 纯算法四层装配,token 预算硬分配,每条注入记忆可归因(trace: id/来源 span/得分/预算占用) |
| 🕰️ | **双时态数据模型** | `valid_from/valid_to` + `superseded_by`——改口供不丢旧版本,任何时点可回溯 |
| 📝 | **会话摘要(P1.a)** | 阈值 20 轮自动滚动摘要,compiler episodic 层摘要优先、无摘要回退事件 |
| 🍂 | **遗忘 = 分层存储(P1.b)** | 过期/低重要性 → `archived`(行列全保留可恢复),绝不物理删除 |
| 🔮 | **/v1/reflect(P1.c)** | 聚簇(余弦 > 0.95 + union-find)→ 逐簇走冲突管道合并,审计字段齐全 |
| 🧪 | **四条件评测框架** | C0 无记忆 / C1 全量 / C2 naive RAG / C3 完整管道;真 provider 回答 + 解耦 LLM judge |
| 💸 | **成本护栏 fail-closed** | `--max-cost-yuan` 上界估算超限即拒跑(实测 0 文件落盘);价格是保守上界占位,实价待控制台核对 |
| 📌 | **数据集 pin** | LongMemEval-S / LoCoMo 官方源 + sha256 校验 + 确定性转换(重跑逐字节一致) |
| 🏷️ | **provider 标签** | 每个响应/每行结果都标注数据由哪种模式产生;mock 产物永不被引用为实测 |

## 🖥️ 界面速览

**OpenAPI 文档(9 端点,连描述里都写着诚实条款):**

![OpenAPI docs](docs/assets/api-docs-screenshot.png)

## 🚀 快速开始

前置:[uv](https://docs.astral.sh/uv/)(自带托管 Python 3.12)。

```bash
# 1) 环境与依赖(全离线路径:mock LLM + hash 嵌入 + SQLite,零外部依赖)
uv venv --python 3.12 && uv sync --extra dev

# 2) 起服务(make run 会读 .env;离线 demo 用 mock 模式启动)
RECALLWEAVE_LLM_PROVIDER=mock make run    # http://127.0.0.1:8100

# 3) 测试 / 质量门(仓库标准:全绿才算完)
make test          # 110 passed + 1 skipped(pg 集成,需 PG)
make lint          # ruff check + format,73 files 全绿
make typecheck     # mypy,0 errors in 32 files
```

<details>
<summary><b>Postgres(pgvector)主路径 + 集成测试</b></summary>

```bash
make db-up                                  # docker compose 起 pgvector/pg16
export RECALLWEAVE_DB_URL=postgresql+asyncpg://recallweave:recallweave@localhost:5432/recallweave
uv run pytest -m integration                # pg 集成测试(HNSW 向量检索真跑)
```

切真实供应商:`.env` 设 `RECALLWEAVE_LLM_PROVIDER=openai_compat` + key(DeepSeek/Qwen/GLM 均为 OpenAI 兼容协议)。

</details>

## 🎬 一键 Demo

```bash
./scripts/demo.sh        # 或 make demo
```

对运行中的服务做端到端演示,**每行输出都来自线上真实响应**:

```text
== 0) 健康检查(离线 demo 强制 mock 模式)
== 1) mock 模式下写管道拒绝编造记忆:
      extraction_error: mock llm: script queue exhausted
      -> 诚实设计:live mock 是空脚本队列,永不冒充真实供应商(fail-closed)
== 1b) 种子数据:scripts/seed_demo.py(声明式脚本化 mock,标签如实)
      [ADD] 用户对花生过敏,饮食需避开含花生的菜。
      [ADD] 用户喜欢吃粤菜,尤其是清蒸鱼。
      [ADD] 用户每周四晚上加班,请勿在周四晚安排活动。
      (重复跑演示时,近重门以 sim≈1.0 拦截全部三条 — dedup 在岗)
== 2) 三路混合检索:vector+lexical+recency 三泳道全中,RRF 融合分数 + rrf_detail
== 3) 上下文编译:四层装配(persona 28 + headers 8 + profile 76 + working 29)
      = 141/600 tokens,逐条可归因
== 4) SDK 视图(recallweave.sdk):同一流程,sdk_demo.py
== 5) /v1/stats 计数 + OpenAPI /docs
```

## 🔌 API

| 方法 / 路径 | 作用 |
|---|---|
| `POST /v1/memories:extract` | 会话转写 → 写入管道(extract → dedup → resolve → 双时态存储) |
| `POST /v1/memories:search` | 三路混合检索,`items[].lanes` 标注每条命中的泳道,`rrf_detail` 给逐路明细 |
| `POST /v1/context:compile` | 四层预算装配,返回 `context_messages` + `usage`(逐层 token)+ trace |
| `GET / DELETE /v1/memories/{id}` | 单条读取 / 物理删除(唯一的删除入口) |
| `POST /v1/sessions/{id}:summarize` | 会话滚动摘要(P1.a) |
| `POST /v1/reflect` | 记忆聚簇消解合并(P1.c) |
| `GET /v1/health` · `GET /v1/stats` | 健康(带 provider 标签)/ 计数(含 `archived_total`) |

身份:`Authorization: Bearer <token>`(dev 默认 `dev-key-1`);`user_id` 只从 token 解析,请求体带不了身份。

## 🧪 评测(招牌模块)

**评测框架本身是这个仓库的核心交付**:四条件对照(C0 无记忆 / C1 全量 / C2 naive RAG / C3 完整管道)、真 provider 回答步、**解耦 LLM judge**(回答与判分不同模型)、fail-closed 成本护栏、人工复核模板(judge 分隔离防锚定)、pin 数据集(sha256)、write-once 结果。

**正式评测结果(run7/run8,2026-11-10,真 provider DeepSeek,n=30 题子集,600 token 预算):**

| 数据集 | 条件 | accuracy | judged/total | context tokens(~) |
|---|---|---:|---:|---:|
| LongMemEval-S t30 | C0 无记忆 | 0.0167 | 30/30 | 136 599 |
| LongMemEval-S t30 | C3 RecallWeave | 0.0 | 30/30 | 1 507 |
| LoCoMo t30 | C0 无记忆 | 0.25 | 30/30 | 18 304 |
| LoCoMo t30 | C3 RecallWeave | 0.20 | 30/30 | 3 243 |

**如实解读(不粉饰)**:

- 在这两个 n=30 子集上,**C3 未超过 C0**:LoCoMo 上 20% vs 25%(小样本噪声带内),LME 上两者都近地板(1.7% / 0%),不构成任何"记忆提升准确率"的 claim。
- **真实立得住的差异是上下文开销**:LoCoMo 上 C3 以 ~1/5.6 的 context tokens 取得噪声带内相当的精度;LME 上 C0 的 context 是 C3 的 ~90×(前者把全量历史塞进 prompt)。对以 token 计费 + 前缀缓存敏感的场景,这是直接的成本差异。
- 这些数字**尚不构成简历级结论**:须先过 30 题人工复核(复核草稿已生成,`evals/results/human-review-*-draft.md`,复核本体 = 用户侧动作,门槛:一致率 ≥80% 且反转=0)。
- 离线协议(`context-evidence-recall` proxy)只测"证据是否落进上下文",**永远不冒充 QA accuracy**;mock judge 结果同理。
- Cache-stable A/B(§7.4):结构稳定性已双重复证(v2 内容寻址 = 1 个唯一前缀,v1 分数漂移版 = 6 个);线上真 provider 跑通了实验,但该 provider 未回报 `prompt_cache_hit_tokens`(12 次调用全 0)→ **命中率差值无法 claim,假设已标注**,控制台对拍 = 用户侧。

<details>
<summary><b>编译器/检索延迟基准(写明 scope)</b></summary>

`bench/latency.py`:**进程内 SQLite + hash 嵌入,单线程 CLI**——量的是编译器/检索算法本身的斜率,不是服务端 p95(那是另一口径,别混):

| 记忆条数 | compile p50 / p95 | search p50 / p95 |
|---:|---|---|
| 10 | 3.9 / 6.3 ms | 3.0 / 5.9 ms |
| 100 | 7.9 / 13.2 ms | 4.5 / 6.6 ms |
| 1000 | 27.8 / 74.9 ms | 24.0 / 40.8 ms |

读路径纯算法、无 LLM——1k 条记忆下编译仍在几十 ms 量级,骨架未到优化期(见 Roadmap)。

</details>

## 📂 项目结构

```text
recallweave/
├── src/recallweave/
│   ├── core/        # config(单一入口,env_prefix=RECALLWEAVE_)· errors · logging
│   ├── tokens.py    # tiktoken + 无依赖回退启发式
│   ├── adapters/    # llm.py(OpenAI 兼容 + 重试/fail-closed;MockLLM 严格 FIFO)
│   │                #  · embeddings.py(hash【仅测试】/ BGE-lazy)
│   ├── db/          # models · repos · session · vector_index(pg HNSW / sqlite 双后端)
│   ├── write/       # extraction.py · conflict.py · pipeline.py
│   ├── read/        # retrievers.py · fusion.py(RRF+MMR) · compiler.py(四层装配)
│   ├── service/     # service.py(SDK 与 HTTP 共用的唯一组合点)
│   ├── api/         # schemas · deps(Bearer 身份) · routes · app
│   └── sdk/         # client.py(sync httpx)
├── migrations/      # 001_init_vector.sql(pgvector + HNSW;sqlite 由 ensure_schema 建)
├── evals/           # run_eval.py · conditions.py(C0-C3) · judge.py + write-once results
├── bench/           # latency.py · cache_ab.py + write-once results
├── scripts/         # demo.sh(一键演示) · demo_offline.py(零服务版) · seed_demo.py(声明式种子)
│                  #  · forget.py · download_data.py · make_human_review.py(盲评草稿)
├── tests/           # 110 offline + pg integration(Testcontainers 式 service container in CI)
└── docs/            # architecture.md · api.md · eval-datasets.md · TODO.md · devlog.md · decisions/
```

## 🗺️ Roadmap

| Track | 内容 | 状态 |
|---|---|---|
| P0 | 真 provider 评测:在线 eval 框架(schema v2)· 成本护栏 · 数据集 pin(LME-S/LoCoMo,t30)· 正式评测落盘 | ✅ 代码侧(run7/run8 write-once) |
| P1.a | 会话摘要(§4.4):迁移 · 阈值 20 轮 · compiler episodic 接管/回退 | ✅ `329e082` |
| P1.b | 遗忘 = 分层存储(§4.5):archive 可恢复 · `--dry-run` 取证 · stats 暴露 | ✅ `2daf383` |
| P1.c | `/v1/reflect` 真实现:聚簇 → 冲突管道 → 审计(501 stub 退役) | ✅ `a7a2ea6` |
| P2 | 工程化:lint 纳入 scripts · mypy 14 错误真修 · GitHub Actions CI(suite + pg) | ✅ `00612f6` |
| 用户侧 | GitHub 公开 · 30 题人工复核 · 控制台实价核对 → 简历数字回填 | ⏳ 待用户动作 |

后续候选(未排期):编译器 p95 收口(1k+ 条)· 真嵌入模型(BGE)端到端评测 · 扩大评测 n 与 C2 条件 · 多用户隔离压测。

## ⚠️ 已知限制与诚实性

- **正式评测的负结果如实呈报**(C3 未超 C0,见评测节);accuracy 进简历前必须过人工复核门槛——这是流程要求,不是装饰。
- mock 产物(内容、judge、种子)全是 mechanics-only,带 `provider` 标签,**永不作为实测数字引用**。
- 延迟基准的 scope 是进程内 SQLite + hash 嵌入单线程——刻画算法斜率,不是服务端延迟口径。
- 价格表 `configs/prices.toml` 是**保守上界占位**;`cost_yuan` 实价 / `ttft_ms` / `cache_hit_tokens` 在未核对前一律保持 `null`(绝不虚构),护栏累计上界 ≈ ¥3.28 / 预算 ¥200。
- hash 嵌入仅测试用(512 维确定性);语义检索的真实召回质量依赖真实嵌入模型(候选 BGE-lazy 已接好)。

## 🔗 证据索引

| 证据 | 内容 |
|---|---|
| [evals/results/](evals/results/) | **write-once 评测原始文件**(run7 LME-t30 / run8 LoCoMo-t30 + 6 组离线轮 + summary) |
| [bench/results/](bench/results/) | 延迟基准 + cache-stable A/B(离线 + 线上双份) |
| [docs/TODO.md](docs/TODO.md) | 全部待办的实施步骤与验收口径(含用户侧清单) |
| [docs/architecture.md](docs/architecture.md) | 已实现事实 + 与 proposal 的偏差登记(全在 ADR) |
| [docs/eval-datasets.md](docs/eval-datasets.md) | 数据集来源 / 许可 / 预处理 / 已知偏差 |
| [docs/decisions/](docs/decisions/) | ADR(含 ADR-003 cache-stable 铁律 + ADR-006 在线化修订) |
| [docs/devlog.md](docs/devlog.md) | 全程试错记录(保留全部失败,不删) |
| [evals/human-review-template.md](evals/human-review-template.md) | 人工复核流程定义(judge 分隔离防锚定) |
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | CI:suite job(ruff→mypy→pytest→离线 eval-smoke)+ pg-integration job |

---

<div align="center">

**RecallWeave** · 评测驱动的 Agent 记忆层

fail-closed · write-once · mock 永不伪装 · 负结果也是结果

</div>
