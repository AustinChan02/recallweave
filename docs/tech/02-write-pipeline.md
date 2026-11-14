# 02 · 写路径:提取 → 近重门 → 冲突消解 → 双时态落库(+ P1.a/b/c)

> 来源:`src/recallweave/write/extraction.py`、`conflict.py`、`pipeline.py`、`reflection.py`,
> `src/recallweave/db/repos.py`,以及 `service/service.py` 的摘要/reflect 编排。事实均在代码;
> 取舍见 ADR-004(双时态)、ADR-005(冲突窗口)。

## 1. 提取(`write/extraction.py`)

### 1.1 提示词 `EXTRACT_SYSTEM`(extraction.py:30,全文要点)

```
You are the memory-extraction engine of a long-term-memory service.
From the transcript below, extract concise user-level facts worth remembering:
- kind must be one of: fact (stable facts/state), preference (likes/dislikes/rules),
  event (dated happenings), instruction (standing orders for the assistant).
- Prefer short, self-contained sentences in the user's own language.
- importance in [0,1]: 1.0 = life-critical (health/allergies/legals), 0.5 = routine.
- span may reference the source message index as {"message_index": <int>}.
- Cache-stability advisory: when a fact embeds a time, prefer an absolute anchor
  ("2024-05 起", "每周一") over relative wording ("昨天", "三天前"); the context
  compiler never emits wall-clock relative phrases (ADR-003 rule #2).
- Do NOT invent content; skip opinions about abstract topics with low signal.
- Do NOT extract the assistant's own statements.
Output: JSON object {"memories": [...]} and nothing else.
```

设计点(`extraction.py:30-43`):`kind` 四类(fact/preference/event/instruction);
importance 标尺 1.0=生命攸关(健康/过敏/法律)、0.5=常规;`span` 用
`{"message_index": <int>}` 引用来源消息;cache-stable 建议要求绝对时间锚
("2024-05 起"、"每周一"),不用"昨天/三天前"——与编译器 ADR-003 规则 #2 对齐;
两条禁令:不编造、不提取 assistant 自己的话。温度 0(`llm_temperature=0.0` 默认)。

### 1.2 严格 JSON schema 与重试/fail-closed 语义

| 项 | 值 | 来源 |
|---|---|---|
| 单条候选 | `Extracted{kind: Literal[4类], content: 2≤len≤1000, importance: 0≤x≤1 默认0.5, span: dict|None}` | `extraction.py:19` |
| 批 | `ExtractedBatch{memories: ≤16 条,默认 []}` | `extraction.py:26` |
| 请求格式 | `response_format={"type":"json_object"}`;输出剥 ```json 围栏后 `json.loads`,必须为 dict | `adapters/llm.py:35,116` |
| 解析/校验失败 | `ProviderSchemaError` → 适配器**重试**(schema 漂移可重试,与传输失败共享受界预算,默认 `llm_max_retries=2` 即最多 3 次尝试) | `adapters/llm.py:87-95,143` |
| 重试耗尽 | `ProviderSchemaError` 或 `ProviderError` 上抛;管道捕获 → 该批**整体跳过**:`extraction_error` 非空、`extracted=0`、零写入 | `pipeline.py:110-123` |
| user prompt | `TRANSCRIPT:\n- [i] role: content\n…\n\nExtract memories now. JSON only.`,i 是**最近 k 轮切片内**的下标;`k=extract_recent_turns=8` | `extraction.py:47-58`,`config.py:62` |

## 2. 近重门与冲突消解(`write/conflict.py`)

阈值(`core/config.py:47-48`):`dedup_near=0.93`、`conflict_lo=0.80`
(validator 强制 `0 < conflict_lo < dedup_near <= 1.0`,`config.py:100-102`)。
路由纯函数 `route_candidate(max_sim, conflict_lo, dedup_near)`(`conflict.py:58`):

| 条件 | 路由 | 动作 |
|---|---|---|
| `max_sim >= 0.93` | `near_dup` | 跳过,`skipped{reason: "near_dup_skip", similarity}` |
| `max_sim is None` 或 `< 0.80` | `add` | 直接入库,不调 LLM |
| `0.80 <= max_sim < 0.93` | `llm` | 候选 × 邻居送消解 |

邻居窗口:写入事务内 `vector.search(k=neighbor_top_n=8)`(`pipeline.py:156`,
`config.py:64`),候选 embedding 对**当前活跃记忆**取近邻,按相似度降序。

### 2.1 四操作语义与 rationale(`conflict.py:41-55` RESOLVE_SYSTEM 要点)

| op | 语义 | 要求 |
|---|---|---|
| `ADD` | 候选是新信息 | content 不变 |
| `UPDATE` | 候选**改写**某条既有记忆 | 必须给 `target_memory_id`(邻居之一)+ 合并后的 `content` |
| `DELETE` | 既有记忆已失效 | 给 `target_memory_id`,不写 content |
| `NOOP` | 复述/改述 | 优先于冒险改写 |

输出 schema `Decision{op, target_memory_id, content(≤1000), confidence(0..1), rationale}`
(`conflict.py:33`);提示词规则:信息随时间变化时必须 DELETE/UPDATE 旧的,**绝不让新旧
同时作为 active 真相**。

### 2.2 冲突窗口与 fail-closed 强制

LLM 只在窄窗(0.80–0.93)被调用——"相似但不同"才可能改写既有事实,把 LLM 压到窄窗使
错判损失面与成本同降(理由见 ADR-005)。`coerce_decision`(`conflict.py:66`):

- `UPDATE/DELETE` 的 `target_memory_id` 不在邻居 id 集合 → 强制翻成 `NOOP`,
  rationale 前缀 `coerced: target … not in window | original: …`;
- `UPDATE` 缺非空 content → `NOOP`,`coerced: UPDATE without content | original: …`。

无邻居时 `resolve_with_llm` 直接返回 `Decision(op="ADD", confidence=1.0,
rationale="no neighbors in window")`(`conflict.py:87-88`),零 LLM 调用。

## 3. 应用与事务边界(`write/pipeline.py`)

### 3.1 批级流程(`pipeline.py:93` `ingest()`)

1. `turns_ingested` = role∈{user,assistant} 且 content 非空的轮数(P1.a 摘要触发输入);
2. 提取(§1.2,失败整批 fail-closed);
3. 批内去重:规范化 key = `" ".join(content.lower().split())`,撞车 →
   `skipped{reason:"in_batch_duplicate"}`(`pipeline.py:133-148`);
4. `embedder.embed_many` **一次批量**调用;
5. 单 session + `session.begin()` 一个事务,逐候选走 §2 路由与应用。

### 3.2 IngestReport(`pipeline.py:45`)

```json
{"user_id": "…", "session_id": "…|null", "provider": "mock",
 "extracted": 3, "decisions": [DecisionRecord…], "skipped": [SkippedRecord…],
 "extraction_error": null, "turns_ingested": 4}
```

- `DecisionRecord{op, memory_id, target_memory_id, content, similarity, confidence, rationale}`;
- `SkippedRecord{content(截 140 字符), reason, similarity}`;
- 常见 `reason`:`in_batch_duplicate` / `near_dup_skip` / `provider_error_in_resolution`
  / `noop:<rationale[:60]>` / `coercion_race` / `extract 失败时仅 extraction_error`。

### 3.3 per-candidate fail-closed 与事务边界

- **LLM/provider 失败只跳过该候选**(`provider_error_in_resolution`),绝不中断批次;
- **每批一次 `session.begin()`**:中途 DB 失败整批回滚,不写半成品记忆
  (`pipeline.py` 模块注释,proposal §4.1 stage 4 的 durability rule);
- **detached 重挂规则**:UPDATE/DELETE 的 target 必须 `session.get(Memory, id)`
  在**当前写事务内**重新 attach(`pipeline.py:227` 注释)——邻居来自别的 session,
  直接改 detached 对象 flush 看不见(devlog 2026-11-09 事故 #2);
- `coercion_race`:coercion 本应拦住、仍取不到 target → skipped,不静默。

### 3.4 双时态落库(ADR-004)

UPDATE/DELETE **永不覆盖**:旧行被"封口"(`repos.py:82` `mark_superseded`):

| 字段动作 | UPDATE | DELETE |
|---|---|---|
| `status` | `superseded` | `superseded` |
| `valid_to` | `now()`(封口) | `now()`(封口) |
| `superseded_by` | 新行 id | `NULL`(无后继) |
| `meta.close_reason[]` | 追加 `{reason, at}` | 追加 `{reason, at}` |

新行(UPDATE)带 `meta.replaces=<旧 id>`、`meta.decision{op, similarity, confidence,
rationale, provider}`,content = 决策的合并文本,重新 embed 后
`insert_with_embedding`(`repos.py:62`:flush 取 id → 同事务内 `vector.upsert` →
写 `mem_sources` 行,`message_ref="msg:<span.message_index>"`)。
物理删除只有用户显式 `DELETE /v1/memories/{id}` → `status='deleted'` +
`meta.delete_reason`(`repos.py:94`,`service/service.py:283`)。

## 4. P1.a 会话摘要(`service/service.py:103` `summarize_session`)

| 项 | 值 | 来源 |
|---|---|---|
| 触发阈值 | `summarize_every_turns=20`:每 session 轮数计数 ≥20 且未覆盖 → 触发;`turns_since_last < 20` 且未 `force` → no-op | `service.py:123-128`,`config.py:80` |
| 轮数计数 | `session_turn_stats` 原子 upsert(`turns += n`),写入端在 ingest 后 `bump_turns` | `db/repos.py:268` |
| 摘要源材 | **会话内已存记忆**(active 候选过滤 `session_id`,上限 200 条),不持久化原始转写(提案 §14 修订 A8) | `service.py:131-137` |
| 提示词 | `SUMMARY_SYSTEM`:≤200 token、事实性、时间正序、自包含句;**保留绝对日期("2024-05-01"),不用相对("yesterday")**;不发明 | `service.py:34` |
| 存储 | `session_summaries` append-only,`(session_id, seq)` UNIQUE,seq=当前 max+1;记 `turns_covered`、`token_est`、`provider` | `db/repos.py:326` |
| 失败语义 | 阈值未到/无轮/无会话记忆/provider 失败/空输出 → `summarized=false` + `note`(如 `below trigger threshold (20 turns)`),**绝不伪造摘要** | `service.py:121-156` |
| 自动触发 | ingest 后**仅 `llm_provider=="openai_compat"`** 自动摘要;mock 队列被抽取/冲突路径共用,摘要调用不得挤占脚本;异常只 `log.warning("session_summary_skip")`,不打断 ingest | `service.py:89-99` |
| 编译器接管 | 有摘要 → episodic 层由摘要承担(`kind="summary"`,trace id 为内容寻址 `summary:<md5[:12]>`);无摘要 → 回退 event 记忆 | `read/compiler.py:264-300` |

## 5. P1.b 遗忘 = 分层存储(`db/repos.py:154-245`)

```
未访问(或最近访问)时间 = coalesce(last_accessed, created_at)
候选 = status=="active"  AND  有效访问 < now() - forget_tau_days(30 天)
                          AND  importance < forget_importance_lt(0.3)
```

- 两条边界都是**严格 `<`**:恰好 30 天未访问、importance 恰为 0.3 → 保留
  ("宁留勿忘");过滤在 Python 侧用 `aware()` 归一化后做,方言安全(`repos.py:171` 注释);
- `archive_stale`:只动 `active` 行(superseded/deleted 链行永不触碰);置
  `status='archived'`,行与列全保留(可恢复);审计写
  `meta.forgotten{at, reason:"stale+low-importance", tau_days, importance_lt}`;
- `restore(user, memory_id)`:仅 `archived → active`,补 `meta.restored_at`;
  非 archived 状态拒绝恢复(会破坏双时态链,`repos.py:226-229`);
- 触发面:`scripts/forget.py`(支持 `--dry-run`:只报候选写零字节;`--tau-days`
  /`--importance-lt` 显式覆盖,默认取 settings);
- 可见性:`GET /v1/stats` 暴露 `archived_total`(`service/service.py:296-301`)。

## 6. P1.c `/v1/reflect` 聚簇(`write/reflection.py`)

流程(`reflection.py:84` `reflect()`,2026-11-10 由诚实 501 stub 转为真实现):

```
active 记忆(扫描上限 reflect_max_scan=200,按 (created_at, id) 时序稳定排序)
  → embed_many 一次批量 → 纯 Python O(n²) 余弦(复用 db/vector_index.cosine)
  → 严格 sim > reflect_threshold(0.95) 的传递闭包 = union-find 聚簇
     (恰等于阈值不合并:聚簇宁可漏检,不熔不同记忆;reflection.py:57-61)
  → 零簇 → 零 LLM 调用,直接返回
  → 每簇一次 resolve_with_llm(candidate=簇内最新成员, neighbors=较老成员,按 sim 降序)
     fail-closed coercion 原样复用;ProviderError → skipped(不伪造合并)
  → 全部决策先算完,再开**一个**事务应用(与 ingest 同一 durability rule)
```

应用细节(`reflection.py:152-236`):

| 决策 | 动作 | 审计 |
|---|---|---|
| UPDATE | 合并成一行新行:`importance = max(簇内)`,`kind/session_id` 继承 target;`mark_superseded(target, new_id)`,然后簇内**其余全部成员**(含 candidate 本尊)在写事务内 `session.get` 重挂后逐个封口指向新行 | `meta.decision{op, similarity, confidence, rationale, provider, reflect: True, cluster: [成员 id 列表]}` |
| DELETE | 仅被点名的 target 封口(`new_id=None`) | rationale 进 `close_reason` |
| NOOP / 失败 | `skipped`(`noop:…` / `provider_error_in_resolution:…`),行状态零变动 | — |

防错面:target 不在簇内(重读竞态)→ `skipped{reason:"target_not_in_cluster"}`
或 `coercion_race`;本模块是**维护任务**,不提取任何内容,不做物理删除(status 迁移而已)。

## 差异说明(与既有文档)

- `docs/api.md` 仍写 reflect 为 501;代码已真实现(见上 §6),以代码为准。
- `docs/architecture.md`"遗留(P1,未实现)"小节已过时:P1.a/b/c 三项均于
  2026-11-10 落地(devlog 与 README Roadmap 有记录);architecture.md 未同步。
