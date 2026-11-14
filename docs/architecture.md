# RecallWeave 架构（对应 proposal §2/§4）

> 本文档描述"已实现"的事实；提案原文在 `../agent-proposals/A-RecallWeave/PROPOSAL.md`。
> 偏差都在 ADR 与 devlog 里立项登记：embedding 维度 512（非 768）、lexical 通道用
> Python（非 tsvector）、评测结果 write-once 文件（非 DB 表）。

## 总览

```
SDK (sync httpx) ─┐
                  ├─> FastAPI /v1 ──> RecallWeaveService（唯一组合点）
CLI/demo ─────────┘                      │
                       ┌─────────────────┼──────────────────┐
                    写路径              读路径              管理
                 write/pipeline    read/compiler+search   get/delete/stats
                       │                │
                 ┌─────┴─────┐   ┌──────┴───────┐
                 │  LLM 适配  │   │  向量索引(双后端)│
                 │ 提取/消解  │   │ pg HNSW / sqlite│
                 └─────┬─────┘   └──────┬───────┘
                       └────────────────┴──> SQLAlchemy async（唯一 ORM 层）
                                            migrations/001_init_vector.sql (pg)
```

## 关键设计决定与约束

| 主题 | 决定 | 依据 |
|---|---|---|
| 配置 | 单一入口 `core/config.py`（env_prefix=RECALLWEAVE_） | 防"第二配置路径"漂移 |
| 身份 | user_id 只从 bearer token 解析 | 请求体带不了身份（防伪造） |
| 写入 | 单事务提交 + per-candidate fail-closed | LLM 挂掉不脏库、不半写 |
| 版本链 | UPDATE/DELETE 只封口（valid_to）+ superseded_by | 双时态语义，可回溯 |
| 检索 | 三路（vector/lexical/recency）+ RRF(k=60) + 权重/衰减/importance + MMR(λ=0.7) | proposal §4.2 |
| 编译 | 纯算法四层装配（persona/profile/episodic/working），profile 内容哈希序、易变尾置 | §4.3 两条 cache-stable 铁律 |
| 预算 | 四分 system10/profile30/episodic40/working20，working 从最旧侧裁剪 | §4.3（soft cap：单条超限不拦腰截断） |
| 评测 | write-once 结果文件；unjudged 不进分母 | §7.3 |

## 模块索引

```
src/recallweave/
  core/       config.py errors.py logging.py
  tokens.py   tiktoken + 无依赖回退启发式
  adapters/   llm.py（OpenAI 兼容 + 重试/超时/fail-closed；MockLLM FIFO）
              embeddings.py（hash【仅测试】/BGE-lazy；共享分词器）
  db/         models.py repos.py session.py vector_index.py
  write/      extraction.py conflict.py pipeline.py
  read/       retrievers.py fusion.py compiler.py
  service/    service.py（SDK 与 HTTP 共用）
  api/        schemas.py deps.py routes.py app.py
  sdk/        client.py
evals/  run_eval.py conditions.py judge.py + fixtures + results(write-once)
bench/  latency.py cache_ab.py + results(write-once)
migrations/001_init_vector.sql（pgvector + HNSW；sqlite 表由 ensure_schema 建）
```

## 两条数据后端

主路径 Postgres+pgvector（`make db-up`），SQLite fallback 零依赖。
差异被封装在两处：`db/vector_index.py`（向量检索）与 `db/session.ensure_schema`
（DDL）。其余代码对 dialect 无感——为此发生过两次真实 bug：
（1）SQLite 上 SQLAlchemy `Uuid` 以无连字符 hex 存储，裸 SQL JOIN 会失配 →
向量读一律"ORM 取候选 + 按我们自己写库的 dash 字符串取 embedding"；
（2）跨事务拿别处 session 的对象直接改字段不会 flush → UPDATE/DELETE 必须
在写事务内 `session.get()` 重新 attach。devlog 里有完整记录。

## P1 三项（原"未实现留位",2026-11-10 已全部落地）

- `/v1/reflect` 为 P1.c 真实现:余弦 > 0.95 聚簇(union-find)→ 逐簇走冲突管道,
  只做 status 迁移、永不物理删除(详见 [docs/tech/02-write-pipeline.md](tech/02-write-pipeline.md) §6)。
- 会话摘要(P1.a)已实现:每 20 轮触发,`session_summaries` append-only;
  编译器 episodic 层**摘要优先、event 回退**。
- 遗忘归档(P1.b)已实现:未访问 > 30 天 **且** importance < 0.3 → `status=archived`
  (可 restore,物理删除只有用户显式 DELETE)。
- 数据集已 pin 并跑通真 provider 评测(run5–run8):LongMemEval-S(cleaned, MIT)
  与 LoCoMo(CC BY-NC 4.0)的来源/sha256/许可见
  [docs/eval-datasets.md](eval-datasets.md) 与 `configs/datasets.toml`。
