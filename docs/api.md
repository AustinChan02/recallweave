# HTTP API（proposal §5）

Base: `http://127.0.0.1:8100`（`make run`）。OpenAPI 文档：`/docs`。

认证：`Authorization: Bearer <token>`；token→user_id 的映射来自
`RECALLWEAVE_API_KEYS_JSON`（JSON map）。**身份只来自 token**，请求体中的
session_id 只是分组字段。错误模型统一 `{code, message, detail}`。

## 路由

| Method | Path | 说明 |
|---|---|---|
| POST | `/v1/memories:extract` | body `{session_id?, messages[]}` → 执行写入管道，返回 `IngestReport{provider, extracted, decisions[], skipped[], extraction_error}` |
| POST | `/v1/memories:search` | body `{query, k}` → `{items[{memory, score, lanes}], rrf_detail{rrf_k, lanes{vector,lexical,recency}}, provider}` |
| POST | `/v1/context:compile` | body `{session_id?, messages[], budget_tokens?}` → `{context_messages, trace[], usage{budget, layers, caps,...}, provider:"local-compiler"}` |
| GET | `/v1/memories/{id}` | 任意状态详情（含 superseded 链字段） |
| DELETE | `/v1/memories/{id}` | 软删（status=deleted, 封口 valid_to） |
| POST | `/v1/reflect` | **501 not_implemented**（P1 未实现，诚实 stub） |
| GET | `/v1/health` | `{status, app_version, app_env, db_dialect, llm_provider, embedding_provider, provider_note}` |
| GET | `/v1/stats` | `{counts_by_status_kind: {active:fact: n, ...}}` |

## 语义要点

- **provider 透明**：所有响应携带 `provider`（`mock` / `openai_compat:<model>` /
  `local-compiler` / `local-hybrid`）。mock/hash 产出只表达机制，不是实测。
- 提取失败（LLM 不可用/格式不符）→ 200 + `extraction_error` 非空、零写入；
  冲突消解失败 → 该候选进 `skipped`（reason=provider_error_in_resolution），批次继续。
- `rrf_detail` 是诊断字段：看三路各自召回什么，便于演示"为什么要三路"。

## 错误码

| status | code | 触发 |
|---|---|---|
| 401 | auth_failed | 缺/错 bearer |
| 404 | not_found | 记忆不存在或归属他人 |
| 422 | invalid_request | schema 校验失败 |
| 501 | not_implemented | /v1/reflect |
| 502 | provider_error / provider_schema_error | LLM 4xx 或重试耗尽 |
| 500 | config_error / internal_error | 配置/未预期（日志带堆栈，响应不带内部信息） |

## SDK 示例

```python
from recallweave.sdk import RecallWeaveClient

with RecallWeaveClient("http://127.0.0.1:8100", api_key="dev-key-1") as c:
    print(c.health())
    print(c.ingest([{"role": "user", "content": "我对花生过敏。"}]))
    print(c.search("我能吃什么", k=5))
    out = c.compile([{"role": "user", "content": "我能吃什么？"}], budget_tokens=600)
    print(out["context_messages"][0]["content"][:200])
```
