# 03 · 读路径:三路召回 → 融合 → Context Compiler

> 来源:`src/recallweave/read/retrievers.py`、`fusion.py`、`compiler.py`,
> `src/recallweave/tokens.py`,阈值/权重默认值抄自 `core/config.py`。取舍见 ADR-002
> (lexical 用 Python)、ADR-003(compiler 纯算法 + cache-stable)。

## 1. 三路召回

候选池:`repo.active_candidates(user_id, lexical_scan_limit=2000)` —— 一次 ORM 查询
(`status='active'`,按 `created_at desc`,Python 侧再滤掉 `valid_to <= now()` 的封口行),
三路共享同一候选集(`compiler.py:199`)。

| 路 | 实现 | 参数 | 来源 |
|---|---|---|---|
| vector | pg:`memory_embeddings` 上 pgvector `<=>` 余弦距离,HNSW;sqlite:ORM 取候选 + Python `cosine` 全量打分排序 | `vector_top_m=32`;pg 运行时 `hnsw.ef_search=40`(事务内 `set_config`) | `db/vector_index.py:32,78-131` |
| lexical | **Python 实现**(DB 无关):共享 `tokenize_for_lexical`(latin 词+数字、CJK 二元组),`score = \|Q∩D\| / sqrt(\|Q\|·\|D\|)`,0 交集不记分 | `lexical_top_m=32`,扫描上限 `lexical_scan_limit=2000` | `read/retrievers.py:17-36`,`adapters/embeddings.py:22` |
| recency | `anchor = last_accessed or created_at`,按 `anchor.timestamp()` 降序 | `recency_top_m=8` | `read/retrievers.py:39-45` |

查询文本取 `messages` 中**最后一条**非空 user 消息(`compiler.py:202-207`);
查询为空时 vector/lexical 两路为空,仅 recency 生效(`compiler.py:209-216`)。

**为何 lexical 不用 tsvector**(ADR-002):中文 tsvector 需要额外 parser(zhparser/pg_jieba),
默认镜像装不上会破坏"一键起库"承诺;ts_rank 与 BM25 本就是近似;且查重门也要分词——
检索与查重共用 `tokenize_for_lexical` 一个分词器,避免"分词器漂移"(排序语义在检索与
去重两侧发散)。升级路径(zhparser / ParadeDB pg_search / SQLite FTS5)记 P2。

## 2. 融合(`read/fusion.py`,纯函数无副作用)

```
final = RRF(lanes) × kind_weight × recency × importance_factor
  RRF               = Σ_lanes 1/(k + rank)          rank 从 1 起
  kind_weight       = fact 1.0 / preference 1.1 / instruction 1.2 / event 0.9(未列出 1.0)
  recency           = exp(-age_days / tau),tau=decay_tau_days=21.0
                      age 基准 = coalesce(last_accessed, created_at),负值钳 0
  importance_factor = importance_lo + importance × importance_span
                      = 0.7 + importance × 0.6  (importance=0.5 → 1.0)
```

精确值全部抄自 `core/config.py:50-59` 与 `fusion.py:18-81`。三路 lane 顺序固定
`[vec_ids, lex_ids, rec_ids]`(`compiler.py:218`)。

### MMR 多样性(`fusion.py:88`)

`mmr_select(ranked, embeddings, k, lam)`:贪心;rel 按 max 分数归一;
`value = λ·rel − (1−λ)·max_cos(item, 已选)`,**λ = mmr_lambda = 0.7**;
embedding 缺失 → 相似度按 0(diverse floor)。用于 `/v1/memories:search`
(`service/service.py:212`),从 `max(top_k*3, top_k)` 的融合前排里选 `top_k` 条。
注意:compiler 路径**不经过 MMR**(四层装配直接按融合分填充)。

### RRF 计算实例(机制说明,非实测数据)

三条 lane 各自送入 best-first 的 id 序列;某 id 在三条 lane 的名次分别为 1/2/1
时(`k=60`):`1/61 + 1/62 + 1/61 ≈ 0.0492`;仅在单一 lane 出现名次 3 时:
`1/63 ≈ 0.0159`。多 lane 共现天然占优,这正是"三路互补"在分数层的表达
(`fusion.py:18-24`)。随后乘 `kind_weight × recency × importance_factor`
得到最终分(`fusion.py:40-49`),trace 里的 `score` 即该最终分(round 6)。

### 响应的 `rrf_detail` 结构(`service/service.py:224-231`)

```json
{"rrf_k": 60,
 "lanes": {"vector":  ["<id>","<id>","<id>"],
           "lexical": ["<id>","<id>","<id>"],
           "recency": ["<id>","<id>","<id>"]}}
```

诊断字段:看三路各自召回什么(每条 item 另带 `lanes` 成员列表)。此样例为 mock 机制
展示(实测于本机 mock 服务),非生产实测。

## 3. Context Compiler(`read/compiler.py`,纯算法、零 LLM)

```
budget(默认 1500,请求可带 budget_tokens 200..8000)
  → 四层 cap = int(budget × pct):  persona 10% / profile 30% / episodic 40% / working 20%
  → system 块 = persona → PROFILE(hash 序) → [RECENT SESSION SUMMARIES|RECENT EVENTS]
  → context_messages = [system] + working tail(原文,最旧侧裁剪)
```

budget=600 时实测 cap:`{persona: 60, profile: 180, episodic: 240, working: 120}`
(`compiler.py:186-192`,本机实测输出)。validator 强制四层 pct 和为 1.0(±0.02)
(`config.py:103-110`)。

| 层 | 内容与排序 | cap 与填充 | 来源 |
|---|---|---|---|
| persona | 固定 `persona_line`("You are an assistant with durable long-term memory…") | `budget×0.10` | `config.py:91` |
| profile | `fact/preference/instruction` 类记忆;**输出按内容哈希序**(`md5(content)[:12]`,与融合分无关) | 贪心按融合分填充,渲染行 `- {content}` 逐条计 token | `compiler.py:47,233-258` |
| episodic | 有会话摘要:取新弃旧(适配 cap)再按时间正序渲染;无摘要:event 类记忆按 created_at 新在前、content 破平 | `budget×0.40` | `compiler.py:260-300` |
| working | 原请求消息,只保留最近 `working_turns=12` 条,超出 cap 时**从最旧侧**逐条丢弃;单条超限**保留**(soft cap:用户当前轮不被静默截断) | `budget×0.20` | `compiler.py:159-170,194` |

section header 行(`PROFILE (sorted for cache-prefix stability):` /
`RECENT SESSION SUMMARIES (chronological):` / `RECENT EVENTS:`)单独计入
`usage.layers["headers"]` 桶(`compiler.py:305`,devlog 2026-11-09 账目修正)。

### cache-stable 三规则(ADR-003)

1. **内容哈希序**:profile 按 `md5(content)` 排序输出——记忆集合不变 → 前缀字节全同,
   provider 前缀缓存可命中;摘要天然稳定(append-only `(session_id, seq)`);
2. **易变尾置**:注入条目只带文本(无时间戳/相对时间),volatile 的 working 原文严格
   排在 system 块之后;提取提示词也要求绝对时间锚(§02);
3. **无 wall-clock 相对时间**:`profile_line` 与事件渲染都不嵌入"昨天/N 天前"类表述
   (`config.py:91` 注释即此规则的代码锚点)。

### token 估算(`tokens.py`)

首选 tiktoken `cl100k_base`(预算软上限口径,DeepSeek/Qwen 计数差 ~±20%,注释明示);
tiktoken 不可用时回退启发式:CJK ≈ 1 token/字符(上界)、latin ≈ 词数/1.3;永不崩
(`tokens.py:16-40`)。`count_messages` 每条消息 +4(结构性开销)。全局单例 `counter()`。

### trace 结构(`compiler.py:52-61,233-256`)

每个被考虑的记忆产出:

```json
{"memory_id": "…", "kind": "fact", "score": 0.041946, "rank": 2,
 "budget_slot": "profile", "tokens": 27, "included": true,
 "reason": null, "source_ref": "msg:0"}
```

- `score` 为融合后最终分(round 6);`rank` 是融合榜名次;
- `tokens` 计的是**渲染行**(`- {content}`)成本;`included=false` 时
  `reason="budget_exhausted"`;被摘要挤占的 event 行 `reason="slot_consumed_by_summaries"`;
- 摘要行 `kind="summary"`、`memory_id="summary:<md5[:12]>"`、`score=0.0`、`rank=null`
  (摘要不参与检索打分,`compiler.py:270-279`);
- `source_ref` 来自 `meta.span.message_index`(`msg:<idx>`);
- trace 截断在 **60 条**(`_TRACE_LIMIT`,文档化上限,`compiler.py:49`);
- `usage` 报 `{budget, working_tokens, system_tokens, total_tokens,
  layers{persona, headers, profile, episodic, working}, caps{…}}`,且
  `system_tokens == Σ layers` 精确成立(devlog 账目修正的验收口径);
- `provider` 恒为 `"local-compiler"`(纯算法,无 LLM)。

### 空输入与边界行为(代码明示)

| 边界 | 行为 | 来源 |
|---|---|---|
| messages 无非空 user 轮 | `query=""` → vector/lexical 两路为空,仅 recency 路生效 | `compiler.py:202-216` |
| 候选池为空 | `context_messages` 只有 persona+working;trace 空 | `compiler.py:228-258` |
| 单条消息超 working cap | 保留(soft cap:不拦腰截断当前轮) | `compiler.py:166-170` |
| `budget_tokens` 请求越界 | 422(schema 约束 200..8000) | `schemas.py:36` |
| 预算四层 pct 不和为 1 | 启动即 `ValueError`(validator) | `config.py:103-110` |
| tiktoken 缺失/编码失败 | 回退启发式(CJK 1/char,latin 词/1.3),永不崩 | `tokens.py:26-36` |
| 候选 `valid_to <= now()`(已封口) | 不入候选池(active_candidates Python 侧过滤) | `db/repos.py:130-137` |

## 4. 检索 vs 编译:两条读入口的差异

| | `/v1/memories:search` | `/v1/context:compile` |
|---|---|---|
| 融合 | RRF + 权重 + importance + recency | 同一套(`compiler.py:218-226`) |
| MMR | 有(λ=0.7,选 top_k) | 无 |
| 排序 | 按融合分输出 | profile 按内容哈希序;episodic 按时间正序 |
| episodic 层 | —(单条记忆列表) | 摘要优先,回退 event |
| trace | 无(带 `rrf_detail`) | 有(≤60 条) |
| `last_accessed` 盖章 | 命中条(`service.py:223`) | trace 中 included 的非摘要条(`service.py:258-263`) |

## 差异说明(与既有文档)

- `docs/architecture.md` 与 ADR-003 写"episodic 暂由 event 类记忆承担(P1 未实现)";
  代码已是"摘要优先、event 回退"双态(`compiler.py:264-300`),两份文档为
  P1.a 落地前的历史表述,以代码为准。
- `docs/decisions/ADR-003` 说 trace 截断 60 条,与代码一致;但未列 `headers` 独立
  计费桶,本文以 `compiler.py:305` 为准补充。
