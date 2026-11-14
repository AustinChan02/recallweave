# 01 · 系统总览:三条路径与唯一组合点

> 来源:`src/recallweave/service/service.py`、`src/recallweave/api/*`、`src/recallweave/sdk/client.py`、
> `src/recallweave/core/config.py`。本文写"已实现事实";取舍见 `docs/decisions/ADR-001..006`。

## 1. 组合点规则:SDK 与 HTTP 完全同构

`RecallWeaveService`(`service/service.py`)是**唯一组合点**:域规则全部住在 service 层,
HTTP(`api/routes.py`)与 SDK 客户端(`sdk/client.py`)都是薄传输层。SDK 本身不带重试
(`sdk/client.py` 模块注释:服务端已 fail-closed,客户端再重试 502 会放大写入副作用)。

```
FastAPI /v1 ──┐                       ┌─ 写: ingest / summarize_session / reflect
              ├─> RecallWeaveService ─┼─ 读: search / compile
SDK httpx ────┘   (service.py)        └─ 管理: get_memory / delete_memory / stats
                    │
                    ├─ llm       LLMAdapter       (mock | openai_compat)  adapters/llm.py
                    ├─ embedder  EmbeddingAdapter (hash | bge)           adapters/embeddings.py
                    ├─ vector    VectorIndex      (pg HNSW | sqlite)     db/vector_index.py
                    ├─ repo      MemoryRepo                              db/repos.py
                    └─ sum_repo  SummaryRepo                             db/repos.py
```

构造:`RecallWeaveService(settings, engine, *, llm=None, embedder=None)`(`service/service.py:47`);
未注入适配器时由 `make_llm_adapter` / `make_embedder` 按 settings 装配。
生命周期:`bootstrap()` 执行 `ensure_schema`(`db/session.py`),`aclose()` 执行
`engine.dispose()`。工厂函数 `build_service(settings)`(`service/service.py:320`)。

## 2. 三条路径的方法对照

| 路径 | service 方法 | HTTP 端点(`api/routes.py`) | SDK 方法(`sdk/client.py`) |
|---|---|---|---|
| 写 | `ingest(user_id, session_id, messages)` | `POST /v1/memories:extract` | `ingest()` |
| 写 | `summarize_session(user, session, force=)` | `POST /v1/sessions/{id}:summarize` | 未实现(无对应方法) |
| 写 | `reflect(user)` | `POST /v1/reflect` | 未实现 |
| 读 | `search(user_id, query, top_k=5)` | `POST /v1/memories:search` | `search()` |
| 读 | `compile(user_id, session_id, messages, budget=)` | `POST /v1/context:compile` | `compile()` |
| 管理 | `get_memory` / `delete_memory` | `GET/DELETE /v1/memories/{id}` | `get_memory` / `delete_memory` |
| 管理 | `stats(user)` | `GET /v1/stats` | `stats()` |
| — | — | `GET /v1/health`(不走身份) | `health()` |

冒号路由(`/v1/memories:extract`)是刻意的:`resource:verb` 读法,把 query-string 约定
留空(`api/routes.py` 模块注释)。

## 3. 一次写入的完整调用链(逐层到函数)

以 `POST /v1/memories:extract` 为例(`mock` 模式离线实测,样例来自本机 curl):

```
api/routes.py:memories_extract            依赖注入 current_user → user_id
  └─ service.ingest(user_id, session_id, body.plain())
       ├─ write/pipeline.py:ingest
       │    1. turns_ingested = 计 user/assistant 非空轮数           pipeline.py:105
       │    2. extract_memories(llm, messages, extract_recent_turns)  extraction.py:61
       │       └─ llm.chat_json(EXTRACT_SYSTEM, user_prompt, ExtractedBatch)
       │          ProviderError → extraction_error,零写入            pipeline.py:110-123
       │    3. 批内去重(规范化 content 撞 set → skipped)             pipeline.py:133-148
       │    4. embed_many(批内容)单次批量                              pipeline.py:151
       │    5. 一个 session+tx 内逐候选:                               pipeline.py:153
       │        vector.search(k=neighbor_top_n) → by_ids → Neighbor[]  (近邻窗口)
       │        route_candidate(max_sim) → near_dup | add | llm        conflict.py:58
       │        resolve_with_llm → Decision(经 coerce_decision)        conflict.py:86
       │        ADD / UPDATE / DELETE / NOOP 应用                      pipeline.py:196-279
       │    (per-candidate fail-closed:单条失败 → skipped,批继续)
       ├─ sum_repo.bump_turns(user, session, turns)   (有 session_id 且 turns>0)
       └─ summarize_session(...)  仅 llm_provider=="openai_compat" 时自动触发
```

`session_id` 契约:`service.ingest` 签名收 `uuid.UUID | None`(HTTP 路径由 pydantic
预解析;脚本直传 `str` 会触发 SQLAlchemy 绑定错误,`scripts/seed_demo.py` 曾踩,devlog
2026-11-13 有记录)。

## 4. 一次检索的完整调用链

以 `POST /v1/memories:search`(`service/service.py:179` `search()`)为例:

```
service.search(user_id, query, top_k)
  1. repo.active_candidates(user_id, lexical_scan_limit=2000)   候选池(一次 ORM 查询)
  2. embed_many([query]) → vector.search(k=vector_top_m=32)      三路
  3. lexical_lane(query, rows, lexical_top_m=32)    read/retrievers.py
  4. recency_lane(rows, recency_top_m=8)
  5. rrf_fuse([vec, lex, rec], rrf_k=60)             read/fusion.py
  6. weighted_rank(... kind_weight, decay_tau_days, importance_*) 截 top_k*3
  7. mmr_select(ranked, embeddings, k=top_k, lam=mmr_lambda=0.7)
  8. _touch(user_id, 命中 id)   last_accessed 盖章,best-effort
  → {items:[{memory, score, lanes}], rrf_detail:{rrf_k, lanes{...}}, provider:"local-hybrid"}
```

`compile()`(`service/service.py:233`)多一步摘要注入:先取 `sum_repo.recent_summaries
(user, limit=summary_recent_limit=8)`,有摘要则 episodic 层由摘要接管,无摘要回退
event 记忆(`read/compiler.py:299`);随后 `compile_context`(`read/compiler.py:173`)
纯算法装配,最后对 trace 中 included 且非 `summary:` 前缀的 id 补 `last_accessed`。

## 5. 配置单一入口规则

`core/config.py` 是**唯一配置路径**(模块 docstring:"THIS MODULE IS THE ONE AND ONLY
CONFIG PATH"):所有 settings 字段都定义在 `Settings(BaseSettings)` 上,
`env_prefix="RECALLWEAVE_"`,`env_file=".env"`,`extra="ignore"`。任何代码需要设置值
都必须走 `Settings` 实例(运行期经 `from_env()` 的进程级缓存,或测试用 `make_settings(**overrides)`)。
运行期方法 `api_keys()`(JSON 解析失败即抛 `ValueError`,fail-closed)与
`kind_weight(kind)` 也定义在 Settings 上,避免第二处散落常量。

启动装配只有一处:`api/app.py:create_app`(`service=None` 时自行
`create_async_engine(settings.db_url)` + `RecallWeaveService`),lifespan 里
`prep_sqlite_path` + `bootstrap()` + shutdown 时 `engine.dispose()`。

## 6. 身份模型

| 规则 | 事实 | 来源 |
|---|---|---|
| 身份只来自 Bearer | `current_user` 从 `Authorization: Bearer <token>` 解析;请求体只能带 `session_id`(分组字段),带不了身份 | `api/deps.py:26` |
| token→user 映射 | `RECALLWEAVE_API_KEYS_JSON`(JSON map);默认 `{"dev-key-1": "11111111-1111-1111-1111-111111111111"}` | `core/config.py:28` |
| 无 Bearer 时 | `api_auth_enabled=False` → 返回 `settings.dev_user`;否则 `AuthError(401,"missing bearer token")` | `api/deps.py:29` |
| 未知 token | `AuthError(401, "unknown token")`;映射值非 UUID → `AuthError("token maps to an invalid user id")` | `api/deps.py:34-40` |
| 读写隔离 | 所有 repo 查询/更新带 `user_id` 条件(如 `active_candidates`、`by_ids`、`touch_last_accessed`);跨用户读取等同 not_found | `db/repos.py` |

## 7. 诚实性:provider 标签

每个响应携带数据来源标签:写入/摘要/reflect 响应的 `provider` 取自 LLM 适配器的
`provider_label`(`openai_compat:<model>` 或 `mock` / `mock:<自定义>`,标签生成见
`adapters/llm.py:79`、`pipeline.py:56-57`),检索是 `local-hybrid`
(`service/service.py:230`),编译是 `local-compiler`(`read/compiler.py:77`)。`/v1/health`
的 `provider_note` 字段固定声明:mock/hash 是离线占位,mock 输出不得作为实测引用
(`api/routes.py:104-107`)。mock 产物是机制展示,非实测——本目录所有实测样例均为此口径。

## 8. 错误模型:域错误携带 HTTP 状态,传输层是薄适配器

域错误定义在 `core/errors.py`,每个异常自带 `status_code` 与 `code`,
`as_dict()` 输出 `{code, message, detail}`(`errors.py:6-16`):

| 异常 | status | code | 语义 |
|---|---|---|---|
| `AuthError` | 401 | `auth_failed` | 缺/错 Bearer |
| `NotFoundError` | 404 | `not_found` | 记忆不存在或归属他人 |
| `ValidationError` | 422 | `invalid_request` | 域级校验失败 |
| `ProviderError` | 502 | `provider_error` | 上游 LLM 在有界重试后仍失败(传输或预算耗尽) |
| `ProviderSchemaError` | 502 | `provider_schema_error` | provider 200 但 payload 不满足 schema(ProviderError 子类) |
| `ConfigError` | 500 | `config_error` | 服务未接线 / pg 迁移缺失 / 非法 key 映射 |
| `RecallWeaveError`(基类) | 500 | `internal_error` | 兜底 |

异常处理器装配(`api/app.py:93-96`):`RecallWeaveError` → 按自带状态返回;
`RequestValidationError` → 422 + `detail.errors` 截前 20 条;`Exception` 兜底 →
500 通用体(日志带堆栈,响应不带内部信息,`app.py:45-52`)。写路径的"结构化失败"
(提取失败)不走 502 而是业务响应里的 `extraction_error` 字段,详见 `05-api-reference.md` §4。

## 9. 模块速查(与 architecture.md 模块索引同一事实体系)

| 模块 | 文件 | 一句话 |
|---|---|---|
| core | `core/config.py` | 唯一配置入口(env_prefix=RECALLWEAVE_)+ validator |
| core | `core/errors.py` / `core/logging.py` | 域错误模型 / structlog JSON 日志(dev 渲染 Console) |
| tokens | `tokens.py` | tiktoken `cl100k_base` + 无依赖回退启发式 |
| adapters | `adapters/llm.py` | OpenAI 兼容适配器(有界重试、fail-closed)+ MockLLM FIFO/sticky |
| adapters | `adapters/embeddings.py` | hash(仅测试)/ BGE(懒加载);共享分词器 |
| db | `db/models.py` / `repos.py` / `session.py` / `vector_index.py` | ORM / 唯一 ORM 访问层 / schema 引导 / 双后端向量索引 |
| write | `write/extraction.py` / `conflict.py` / `pipeline.py` / `reflection.py` | 提取 / 路由与消解 / 编排 / P1.c 聚簇 |
| read | `read/retrievers.py` / `fusion.py` / `compiler.py` | lexical+recency 路 / RRF+MMR / 四层装配 |
| service | `service/service.py` | 唯一组合点(域规则所在) |
| api | `api/schemas.py` / `deps.py` / `routes.py` / `app.py` | 校验 / Bearer 身份 / 9 端点 / 应用工厂 |
| sdk | `sdk/client.py` | 同步 httpx 客户端(无自带重试) |

深潜分篇:写路径见 `02-write-pipeline.md`,读路径见 `03-read-path.md`,
数据面见 `04-data-model.md`,端点见 `05-api-reference.md`,配置见
`06-adapters-and-configuration.md`。

## 差异说明(与既有文档)

- `docs/api.md` 的路由表还写着 `POST /v1/reflect → 501 not_implemented`,且未列
  `/v1/sessions/{id}:summarize`。代码事实(`api/routes.py:68-90`):reflect 已是
  P1.c 真实现(501 stub 于 2026-11-10 退役),summarize 端点已上线。`docs/api.md`
  未同步,以代码与本目录 `05-api-reference.md` 为准。
- `README.md` 徽章写 110 tests passing;当前代码库实测 `uv run pytest` 为
  **112 passed + 1 skipped**(2026-11-13,含迁移加载器回归测试),README 未刷新。
