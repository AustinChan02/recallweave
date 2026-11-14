# 04 · 数据模型:全部表逐列 + 双后端差异

> 来源:`src/recallweave/db/models.py`(ORM)、`migrations/001_init_vector.sql`、
> `migrations/002_session_summaries.sql`(pg DDL)、`db/session.py`(schema 引导)、
> `db/vector_index.py`(向量索引)、`docs/decisions/ADR-001`(维度偏差登记)。

## 1. 表清单与建立方式

| 表 | 建立方式 | 用途 |
|---|---|---|
| `memories` | `Base.metadata.create_all`(双后端) | 记忆主表(双时态) |
| `mem_sources` | 同上 | 来源引用(message_ref + span) |
| `session_turn_stats` | 同上;pg 另由 002 显式建 | P1.a 轮数计数 |
| `session_summaries` | 同上;pg 另由 002 显式建 | P1.a append-only 摘要 |
| `memory_embeddings` | pg:001_init_vector.sql;sqlite:`ensure_schema` 裸表 DDL | 向量列(独立表,ADR-001) |

`ensure_schema`(`db/session.py:66`):先 `create_all`,再按 dialect 分支——
`postgresql` 执行 `_load_pg_migrations()`(按名字序执行 `migrations/*.sql`,缺失
`001_init_vector.sql` 即 `ConfigError`);否则执行 sqlite 裸表
`CREATE TABLE IF NOT EXISTS memory_embeddings(memory_id TEXT PRIMARY KEY, embedding_json TEXT NOT NULL)`。
两条路径都幂等。

## 2. `memories`(`models.py:31-54`)

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| `id` | `Uuid` | PK,默认 `uuid4` | |
| `user_id` | `Uuid` | NOT NULL,index | 租户隔离键 |
| `session_id` | `Uuid` | NULL | 分组字段(可空) |
| `kind` | `String(16)` | NOT NULL | `fact|preference|event|instruction` |
| `content` | `Text` | NOT NULL | 记忆正文 |
| `importance` | `Float` | NOT NULL,默认 `0.5` | |
| `status` | `String(16)` | NOT NULL,默认 `active` | `active|superseded|archived|deleted` |
| `valid_from` | `DateTime(timezone=True)` | NOT NULL,server_default `now()` | 双时态有效起点 |
| `valid_to` | `DateTime(timezone=True)` | NULL | 封口时间(未封口=仍有效) |
| `superseded_by` | `Uuid` | NULL | 版本链后继(UPDATE 用,DELETE 为 NULL) |
| `meta` | `JSON` | NULL | 键只增不重释:`span`/`decision`/`replaces`/`close_reason[]`/`forgotten`/`restored_at`/`delete_reason` |
| `created_at` | `DateTime(timezone=True)` | NOT NULL,server_default `now()` | |
| `last_accessed` | `DateTime(timezone=True)` | NULL | 读路径盖章(P1.b 计龄基准之一) |

索引:`ix_mem_user_status (user_id, status)`(`models.py:54`)。

## 3. `mem_sources`(`models.py:57-66`)

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `Uuid` PK | 默认 `uuid4` |
| `memory_id` | `Uuid`,NOT NULL,index | 归属记忆 |
| `message_ref` | `String(64)`,NULL | 形如 `"msg:12"`(来自 span 的 message_index) |
| `span` | `JSON`,NULL | 提取期来源区间 |
| `created_at` | `DateTime(timezone=True)`,server_default `now()` | |

## 4. `session_turn_stats` 与 `session_summaries`(P1.a)

`session_turn_stats`(`models.py:69-84`;pg DDL `002_session_summaries.sql:6-12`):

| 列 | 类型 |
|---|---|
| `user_id` | `Uuid`,复合 PK 一半 |
| `session_id` | `Uuid`,复合 PK 另一半 |
| `turns` | `Integer`,NOT NULL,默认 0 |
| `updated_at` | `timestamptz`,NOT NULL,默认 `now()` |

`session_summaries`(`models.py:87-110`;pg DDL `002_session_summaries.sql:14-24`):

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `Uuid` PK | |
| `user_id` | `Uuid`,NOT NULL,index | |
| `session_id` | `Uuid`,NOT NULL | |
| `seq` | `Integer`,NOT NULL,默认 1 | 会话内递增;append-only |
| `turns_covered` | `Integer`,NOT NULL,默认 0 | 截至本条覆盖的轮数 |
| `content` | `Text`,NOT NULL | 摘要正文 |
| `token_est` | `Integer`,NOT NULL,默认 0 | 本地 token 估算 |
| `provider` | `String(64)`,NOT NULL | 生成方标签 |
| `created_at` | `timestamptz`,NOT NULL,默认 `now()` | |

索引(ORM 与 pg DDL 双向一致):`ix_ssum_session_seq (session_id, seq)` **UNIQUE**、
`ix_ssum_user_created (user_id, created_at)`。

## 5. `memory_embeddings`(向量表,双后端)

| | Postgres(001_init_vector.sql) | SQLite(`session.py:73-78`) |
|---|---|---|
| 建表 | `CREATE EXTENSION IF NOT EXISTS vector;` + `memory_embeddings(memory_id uuid PK REFERENCES memories(id) ON DELETE CASCADE, embedding vector(512) NOT NULL)` | `memory_embeddings(memory_id TEXT PRIMARY KEY, embedding_json TEXT NOT NULL)`(JSON blob) |
| 索引 | `ix_mem_emb_hnsw USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)` | 无(brute-force cosine) |
| upsert | `INSERT … ON CONFLICT (memory_id) DO UPDATE SET embedding = EXCLUDED.embedding` | `INSERT OR REPLACE` |
| 检索 | `<=>` 距离排序,`1 - distance` 记 sim | Python `cosine()` 全量打分排序取 top-k |

**维度 512 而非 768**(ADR-001 偏差登记):生产嵌入模型 `BAAI/bge-small-zh-v1.5`
实际输出 512 维(`adapters/embeddings.py:87`);提案当时写 768 属笔误,已在 ADR 登记。
pgvector **强制声明宽度**(test_integration_pg 注释:16 维写入 512 列会报
`expected 512 dimensions, not 16`),集成测试必须用与生产一致的形状。

**sqlite 上限语义**(ADR-001):候选来源被 user 隔离 + status 过滤限制,扫描级 O(n),
只配笔记本地场景;生产路径是 Postgres HNSW。sqlite 检索的读法是**两步**:ORM 类型化
select 取候选(绑定/结果处理一致)+ 用**我们自己写库的带连字符 id 字符串** `IN` 查
embedding 行——绝不做跨格式字符串 JOIN(SQLAlchemy `Uuid` 在 sqlite 存无连字符 hex,
裸 JOIN 永远失配;devlog 2026-11-09 事故 #1)。

## 6. 检索 SQL 路径(`vector_index.py`)

pg(`vector_index.py:32-42`):

```sql
SELECT m.id::text AS mid, 1 - (e.embedding <=> (:qv)::vector) AS sim
FROM memory_embeddings e
JOIN memories m ON m.id = e.memory_id
WHERE m.user_id = :uid AND m.status = 'active'
  AND (m.valid_to IS NULL OR m.valid_to > now())
ORDER BY e.embedding <=> (:qv)::vector
LIMIT :k
```

- `hnsw.ef_search` 在查询事务内 `SELECT set_config('hnsw.ef_search', :ef, true)` 设置
  (asyncpg 拒绝在 `SET` 里绑定参数,`is_local=true` 与 `SET LOCAL` 语义等价、不污染
  连接池,`vector_index.py:84-91`);
- sqlite:候选 + embedding 两步取回后 Python 打分(`vector_index.py:100-131`);
  embedding 行缺失的候选**跳过不崩**(`vector_index.py:120-121`);
- 公共 `cosine(a, b)`:长度不等或零范数 → `0.0`(`vector_index.py:134-147`),
  被 sqlite 检索与 reflect 聚簇共用。

## 7. 双时态语义小结(ADR-004)

```
UPDATE: 旧行 status=superseded, valid_to=now, superseded_by=新行id, meta.close_reason[] 追加
        新行 meta.replaces=旧id
DELETE(LLM 决策):旧行 status=superseded, valid_to=now, superseded_by=NULL
DELETE(用户端点):status=deleted, meta.delete_reason="user_deleted"
遗忘(P1.b):status=archived(行列全保留,meta.forgotten 审计,可 restore)
```

## 8. MemoryRepo / SummaryRepo 公共方法(`db/repos.py`)

| 方法 | 事务归属 | 作用 |
|---|---|---|
| `insert_with_embedding(session, memory, emb, vector, sources)` | 调用方的写事务 | add → flush 取 id → 同事务 `vector.upsert` → 写 `mem_sources` |
| `mark_superseded(session, memory, new_id, reason)` | 调用方的写事务 | 封口:status=superseded + valid_to + superseded_by + `close_reason[]` |
| `soft_delete(session, memory, reason)` | 调用方的写事务 | status=deleted + `meta.delete_reason` |
| `get_any_status(user, id)` | 自开短 session | 任意状态读(管理端点用) |
| `by_ids(user, ids)` | 自开短 session | id 批量取(邻居窗口用) |
| `active_candidates(user, limit, kinds)` | 自开短 session | status=active + created_at desc;Python 侧滤 `valid_to<=now` 与 kinds |
| `touch_last_accessed(user, ids)` | 自开事务 | last_accessed 盖章(≤top_k 条,ORM update 保绑定类型安全) |
| `stale_candidates` / `archive_stale` / `restore` | 各自短事务 | P1.b 三件套(§见 02 篇) |
| `counts(user)` | 自开短 session | (status, kind) 分组计数 |

SummaryRepo(`db/repos.py:261-362`):`bump_turns`(pg/sqlite 显式方言分支的
on_conflict upsert)、`turns_of` / `turns_covered` / `next_seq` / `last_summary` /
`add_summary` / `recent_summaries`(取最新 N 条再**反转成时间正序**返回——
compiler 依赖该确定性顺序)。

事务规则(`repos.py:1-6` 模块注释):写路径操作(insert + embedding + supersedes)
跑在**调用方(pipeline)拥有**的一个 session/tx 里;读路径自开短 session;
`expire_on_commit=False` 使提交后属性访问不失效(预先规避经典 rollback-expiry bug)。

## 差异说明(与既有文档)

- `docs/architecture.md` 模块索引只列 `001_init_vector.sql`,未列 002;
  代码与本文以上表为准(002 于 P1.a 落地,devlog 有记录)。
- `docs/api.md` 写"DELETE:软删(status=deleted, 封口 valid_to)",与代码一致;
  注意 LLM 决策的 DELETE 与用户 DELETE 是两种不同终态(superseded vs deleted),
  api.md 未区分,本文 §7 已补。
