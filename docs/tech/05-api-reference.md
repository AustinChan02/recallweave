# 05 · API 参考:9 个端点

> 来源:`src/recallweave/api/routes.py`、`schemas.py`、`deps.py`、`app.py`。
> 所有 JSON 样例为 **mock 模式**(`llm_provider=mock` + `embedding_provider=hash`)
> 下 `curl` 实测(`provider=mock` 产物 = 机制展示,非实测);生产语义结构相同,数值不同。

## 0. 基础

- Base:`http://127.0.0.1:8100`(`make run`,`Makefile:8`)。
- 认证:`Authorization: Bearer <token>`;token→user 映射来自
  `RECALLWEAVE_API_KEYS_JSON`,默认 `{"dev-key-1": "11111111-1111-1111-1111-111111111111"}`
  (`core/config.py:28`)。`user_id` 只从 token 解析(`api/deps.py:26`)。
- OpenAPI:`/docs`(Swagger UI)与 `/openapi.json`,由 FastAPI 自动生成
  (`api/app.py:82-92`,标题/描述/版本 0.1.0)。
- 错误体统一 `{code, message, detail}`(`core/errors.py:15-16`)。

## 1. 端点总表(`routes.py`)

| # | 方法 | 路径 | 请求体 | 身份 | 作用 |
|---|---|---|---|---|---|
| 1 | POST | `/v1/memories:extract` | `IngestRequest` | Bearer | 写入管道(extract→dedup→resolve→双时态存储) |
| 2 | POST | `/v1/memories:search` | `SearchRequest` | Bearer | 三路混合检索 + MMR |
| 3 | POST | `/v1/context:compile` | `CompileRequest` | Bearer | 四层上下文装配 |
| 4 | GET | `/v1/memories/{memory_id}` | — | Bearer | 单条详情(任意状态) |
| 5 | DELETE | `/v1/memories/{memory_id}` | — | Bearer | 软删(status=deleted) |
| 6 | POST | `/v1/sessions/{session_id}:summarize` | —(路径参数) | Bearer | 滚动会话摘要(force=True) |
| 7 | POST | `/v1/reflect` | — | Bearer | P1.c 聚簇消解 |
| 8 | GET | `/v1/health` | — | **无** | 健康 + provider 标签 |
| 9 | GET | `/v1/stats` | — | Bearer | 分类计数 + archived_total |

## 2. 请求 schema(`api/schemas.py`)

| 端点 | 字段 | 约束 |
|---|---|---|
| `IngestRequest` | `session_id: uuid\|null`;`messages: list[Message]` | `1 ≤ len(messages) ≤ 200` |
| `SearchRequest` | `query: str`;`k: int` | `1 ≤ len(query) ≤ 400`;`k` 默认 5,`1 ≤ k ≤ 20` |
| `CompileRequest` | `session_id: uuid\|null`;`messages: list[Message]`;`budget_tokens: int\|null` | messages 同上;`200 ≤ budget_tokens ≤ 8000` |
| `Message` | `role: Literal["user","assistant","system"]`;`content: str` | `1 ≤ len(content) ≤ 8000` |

另有配置层兜底:`max_ingest_messages=200`(`config.py:63`)与 schema 上限一致。

## 3. 端点详情与实测响应

### 3.1 `POST /v1/memories:extract`

mock 空队列的诚实空操作(实测,`provider=mock`):

```json
{"user_id": "11111111-1111-1111-1111-111111111111",
 "session_id": "22222222-2222-2222-2222-222222222222",
 "provider": "mock", "extracted": 0, "decisions": [], "skipped": [],
 "extraction_error": "mock llm: script queue exhausted", "turns_ingested": 1}
```

`IngestReport` 字段(`write/pipeline.py:45-53`):`extracted` 提取数;`decisions[]`
每项 `{op, memory_id, target_memory_id, content, similarity, confidence, rationale}`;
`skipped[]` 每项 `{content(≤140 字符), reason, similarity}`;`extraction_error` 非空
即整批零写入。`turns_ingested` 是 P1.a 摘要触发计数。

### 3.2 `POST /v1/memories:search`

实测(`query="用户能吃什么", k=3`,mock+hash 机制展示):

```json
{"items": [{
    "memory": {"id": "96440409-…", "user_id": "…", "session_id": "…",
      "kind": "fact", "content": "用户对花生过敏，饮食需避开含花生的菜。",
      "importance": 1.0, "status": "active",
      "valid_from": "2026-11-13T06:33:23", "valid_to": null,
      "superseded_by": null, "meta": {"span": {"message_index": 0},
      "decision": {"op": "ADD", "similarity": null, "confidence": 1.0,
                   "rationale": "no neighbor inside conflict window", "provider": "mock"}},
      "created_at": "2026-11-13T06:33:23", "last_accessed": "…"},
    "score": 0.063143,
    "lanes": ["vector", "lexical", "recency"]}],
 "rrf_detail": {"rrf_k": 60, "lanes": {"vector": ["…","…","…"],
               "lexical": ["…","…","…"], "recency": ["…","…","…"]}},
 "provider": "local-hybrid"}
```

`memory` 形状来自 `memory_to_public`(`db/repos.py:34-49`);零命中时 `items=[]`、
`rrf_detail.lanes` 三项均为空数组。

### 3.3 `POST /v1/context:compile`

实测(`budget_tokens=600`,mock 机制展示;数字与样例见 README demo 一节同口径):

```json
{"context_messages": [
   {"role": "system", "content": "You are an assistant with durable long-term memory…\nPROFILE (sorted for cache-prefix stability):\n- 用户喜欢吃粤菜…\n- …"},
   {"role": "user", "content": "帮我推荐一个周四能吃的工作餐，注意我的忌口。"}],
 "trace": [{"memory_id": "…", "kind": "instruction", "score": 0.058392, "rank": 1,
            "budget_slot": "profile", "tokens": 24, "included": true,
            "reason": null, "source_ref": "msg:3"}],
 "usage": {"budget": 600, "working_tokens": 29, "system_tokens": 112,
           "total_tokens": 141,
           "layers": {"persona": 28, "headers": 8, "profile": 76, "episodic": 0, "working": 29},
           "caps": {"persona": 60, "profile": 180, "episodic": 240, "working": 120}},
 "provider": "local-compiler"}
```

### 3.4 `GET /v1/memories/{memory_id}`

任意状态可见(含 superseded 链字段);跨用户读取按 not_found 处理
(`service/service.py:277-281`)。响应为 `memory_to_public` 形状(同 3.2 的 `memory`)。

### 3.5 `DELETE /v1/memories/{memory_id}`

实测:

```json
{"deleted": true, "id": "6371cd74-9c4c-412a-98f6-a87686ed8164"}
```

语义(`service/service.py:283-294`):`status='deleted'` + `valid_to` 封口 +
`meta.delete_reason="user_deleted"`;删除后检索不可见,但 `GET` 仍可查到该行
(实测:`{"status": "deleted", "valid_to": "…", "meta": {…, "delete_reason": "user_deleted"}}`)。

### 3.6 `POST /v1/sessions/{session_id}:summarize`

显式触发(`force=True`,`routes.py:77`)。未脚本化 mock 下的实测:

```json
{"session_id": "22222222-2222-2222-2222-222222222222", "turns": 1,
 "turns_covered": 0, "provider": "mock",
 "summarized": false, "note": "no session memories yet to summarize"}
```

成功时:`{"summarized": true, "seq": <n>, "content": "…", "token_est": <n>,
"turns_covered": <n>, "provider": "<label>"}`。所有"未产出"路径都带 `note`
(阈值未到 / 无轮 / 无会话记忆 / provider 失败 / 空输出),**绝不伪造摘要**
(`service/service.py:121-156`)。

### 3.7 `POST /v1/reflect`

实测(无重复聚簇,mock 机制展示):

```json
{"user_id": "11111111-1111-1111-1111-111111111111", "provider": "mock",
 "threshold": 0.95, "scanned": 3, "clusters_found": 0, "decisions": [], "skipped": []}
```

`ReflectionReport`(`write/reflection.py:46-53`):`threshold`/`scanned`/`clusters_found`
为聚簇事实;`decisions`/`skipped` 语义同 3.1;UPDATE 审计在落库行的
`meta.decision{reflect: true, cluster: [成员 id]}`。

### 3.8 `GET /v1/health`(免认证,实测)

```json
{"status": "ok", "app_version": "0.1.0", "app_env": "dev", "db_dialect": "sqlite",
 "llm_provider": "mock", "embedding_provider": "hash",
 "provider_note": "llm_provider=mock and embedding_provider=hash are offline placeholders; mock output must never be presented as measured provider results."}
```

### 3.9 `GET /v1/stats`(实测)

```json
{"counts_by_status_kind": {"active:fact": 1, "active": 3, "active:instruction": 1, "active:preference": 1},
 "archived_total": 0}
```

`counts` 来自 `MemoryRepo.counts`(`db/repos.py:247-258`):按 (status, kind) 分组,
同时给 `"<status>:<kind>"` 键与 `<status>` 汇总键;`archived_total` 取
`counts["archived"]`(P1.b 分层存储可见性)。

## 4. 错误语义(`core/errors.py` + `app.py`)

| status | code | 触发 | 来源 |
|---|---|---|---|
| 401 | `auth_failed` | 缺 Bearer / 未知 token / 映射非 UUID | `deps.py`、`errors.py:19` |
| 404 | `not_found` | 记忆不存在或归属他人 | `errors.py:24`,`service.py:280` |
| 422 | `invalid_request` | pydantic schema 失败;`detail.errors` 截前 20 条 | `app.py:32-42` |
| 502 | `provider_error` | LLM 4xx(永久)或重试预算耗尽(传输/超时/429/5xx) | `errors.py:34`,`adapters/llm.py:156-167` |
| 502 | `provider_schema_error` | provider 200 但 payload 不满足 schema(重试耗尽后) | `errors.py:41` |
| 500 | `config_error` | 服务未接线 / pg 迁移缺失 / `api_keys_json` 非法 | `errors.py:47`,`deps.py:17` |
| 500 | `internal_error` | 未预期异常:日志带堆栈,响应只有通用体(不泄内部) | `app.py:45-52` |

fail-closed 提取语义:提取失败返回 **200 + `extraction_error` 非空**、零写入
(业务上的"结构化失败",不是 502——见 `pipeline.py:110-123`);仅当调用
`summarize`/`reflect` 等直接触达适配器的路径时 ProviderError 才会以 502 冒出。
SDK 侧 ≥400 一律抛 `RecallWeaveAPIError{status, code, message, detail}`
(`sdk/client.py:12-19,42-55`)。

## 差异说明(与既有文档)

- `docs/api.md`:reflect 行为过时(501 → 已真实现)、缺 summarize 端点、错误码表
  同样残留 501 行;`docs/api.md` 的 IngestReport 未列 `turns_ingested`。
  以 `routes.py` 与本文为准。
- `docs/api.md` 的 SDK 示例 `c.search("我能吃什么", k=5)` 与代码一致;SDK 无
  summarize/reflect 方法,属未实现(如实标注)。
