# 07 · 评测与基准:协议、护栏、数据集 pin、bench scope

> 来源:`evals/run_eval.py`、`conditions.py`、`judge.py`、`pricing.py`;
> `bench/latency.py`、`cache_ab.py`;结果只引用 write-once 文件
> (`evals/results/`、`bench/results/`);数据集事实来自 `docs/eval-datasets.md` 与
> `configs/datasets.toml`。协议取舍见 ADR-006。

## 1. 四条件 C0–C3(`evals/conditions.py:11-16,83-147`)

| 条件 | 名字 | 上下文构造(逐行抄自 `run_condition`) | provider 标签 |
|---|---|---|---|
| C0 | no-memory | `system="You are a helpful assistant."` + 最近 20 轮原文 + 末尾问题;**记忆层不参与** | `local-truncate` |
| C1 | full-history | 该 item 的 turns 全部 ingest 进服务,然后 `active_candidates(user, 2000)` **全量倾倒**:`ALL MEMORIES (C1 full-dump):` 头 + 每条 `- content`;再接全部轮次 + 末尾问题 | `local-fulldump` |
| C2 | naive-vector-RAG | ingest 后**仅向量**检索 `vector.search(k=3)`,命中逐条原文倾倒(`RETRIEVED (C2 naive vector-RAG, verbatim, no compiler):`);轮次仍是最近 20 轮截窗 | `local-vector-rag` |
| C3 | recallweave | 完整管道:ingest(extract→dedup→resolve)→ `svc.compile(turns + [问题], budget)` 四层装配 | `local-compiler` |

harness 的服务构建(`conditions.py:63-80`):每题独立临时 SQLite、`make_settings`
(`llm_provider="mock"`、`embedding_provider="hash"`、`embedding_dim=64`、
`context_budget_default=600`、`env_file=""`);LLM = `MockLLMAdapter([抽取脚本], sticky=STICKY_NOOP)`:

- 抽取脚本(`_extraction_script`,`conditions.py:52`):每个 user 轮 → 一条候选
  (kind 由 item.type 映射,importance 恒 0.6)——**声明式脚本,非测得的抽取质量**;
- `STICKY_NOOP = {"op": "NOOP", "rationale": "offline harness: conflict resolution
  scripted to NOOP", "confidence": 0.5}`(`conditions.py:45`):冲突消解被脚本钉死为
  NOOP,结果文件披露此事。

## 2. 离线协议:`context-evidence-recall` proxy(红线)

`--answer-mode echo`(`answer_adapter=None`,`conditions.py:168-170`)下
**answer = 上下文本身的回显**;mock keyword judge(`judge.py:40`)按参考答案关键词
覆盖率打分(score = 命中关键词数 / 总数)。

**红线**(`conditions.py` 模块注释 + 结果文件 note):这测的是"参考证据是否落进上下文",
是管道行为的**代理指标**;它不是 QA accuracy,永远不得作为准确率引用。judge 无关键词
可提时抛错 → 该题 unjudged(judge 不能拿空评分表打 0/1)。

## 3. 在线协议:真 provider 回答 + 解耦 judge(ADR-006 在线化)

`--answer-mode provider --judge llm`(`run_eval.py:186-207`):

1. **回答步**(`conditions.py:159-186`):把(记忆 system 块 + `ANSWER_SYSTEM` 策略 +
   转写(去掉末尾重复的问题)+ 显式 `QUESTION`)发给回答模型;judge 判的是**回答**,
   不是上下文回显。`ANSWER_SYSTEM`:只用记忆上下文与上文作答、不编造、按问题语言、
   最多两句、上下文没有就说不知道(`conditions.py:153`)。
2. **judge 解耦**:`--judge-model` 默认 = 回答模型,可显式覆盖为不同模型
   (`run_eval.py:221-232`);judge 输出必须满足 `JudgeVerdict{score∈[0,1], rationale}`
   严格 schema(`judge.py:59`),rubric:1.0 完整正确 / 0.5 部分正确 / 0.0 错误或矛盾,
   只判正确性不判文风(`judge.py:69-72`)。
3. **拒跑三连**(网络调用前,fail-closed,`run_eval.py:191-207`):provider 模式必须
   `openai_compat`;真 provider 回答永不交给 mock keyword judge;echo 模式不配 LLM judge。

### 协议不变量(代码强制,非约定)

| 不变量 | 实现 | 来源 |
|---|---|---|
| write-once 结果 | 文件名 `eval-<date>-<condition>-run<N>.json`;目标已存在 → `SystemExit` 拒写(覆盖即让后跑的藏住前跑的) | `run_eval.py:71-83` |
| unjudged 出分母 | `ConditionRun.metrics`:accuracy 分母只含 judged;`judged/total/unjudged` 并列报告,不折成假 0/1 | `conditions.py:216-243` |
| provider 全披露 | 结果文件固定 `provider{llm, embedding, judge, answered_by, note}`;mock note 固定"ALL outputs are mock … must not be quoted as measured LLM quality" | `run_eval.py:46-59` |
| uncomputed 恒 null | `cost_yuan`(控制台实价)/`ttft_ms`/`cache_hit_tokens` 未核对前保持 `null` + 原因注释,绝不虚构 | `run_eval.py:278-291` |
| token 口径 | answer/judge tokens 取 **provider usage payload**(事实);`tokens_context_est` 是本地 tiktoken 估算(唯一 est 列) | `conditions.py:304-311` |

## 4. fail-closed 成本护栏(`evals/pricing.py` + `configs/prices.toml`)

- 单价表 `configs/prices.toml` 是**保守上界占位**(deepseek-flash 假设 4/16 ¥/M in/out,
  deepseek-v4-pro 8/32,未知模型 fallback `*` 16/64),控制台核对前不得当实价引用;
- `CostGuard.charge(model, usage)`:累计 prompt/completion tokens 与上界估算花费;
  超过 `--max-cost-yuan`(run_eval 默认 200.0,cache_ab 在线半默认 5.0)→
  `SystemExit` 中止,**在任何结果文件写出之前**;
- 实测行为(devlog 2026-11-09 + ADR-006):`--max-cost-yuan 1e-05` → 首次计入
  0.0017¥ 即中止,结果目录连创建都没发生;
- 正式 run 的护栏读数(来自 summary 文件):run7(LME-t30)上界 ¥1.142876 / cap ¥200;
  run8(LoCoMo-t30)¥0.745084;bench 线上半 ¥0.0590 / cap ¥5。

## 5. 数据集 pin(`configs/datasets.toml` + `docs/eval-datasets.md`)

| 数据集 | 来源与 pin | 许可 | 体量 |
|---|---|---|---|
| LongMemEval-S(cleaned) | HF `xiaowu0162/longmemeval-cleaned`,sha256 `d6f21ea9…c3a442`(与 HF LFS oid 双侧验证,277,383,467 bytes) | MIT | 500 题,题均历史 ~115k tokens |
| LoCoMo(locomo10.json) | github `snap-research/locomo` @ commit `3eb6f2c5…`,sha256 `79fa87e9…698ff4` | CC BY-NC 4.0(非商业,已披露) | 10 段对话,QA 1,986 条 |

确定性转换(`scripts/download_data.py`):30 题分层抽样(类型配额覆盖稀有类型,
`seed=2026`),重跑**字节级一致**(已实测);输出 sha256 记录在
`evals/fixtures/converted-manifest.json`。预处理(会话日期注入、LoCoMo 说话人名内联、
LME 原生角色保留)与已知偏差清单见 `docs/eval-datasets.md`。

## 6. 正式结果(如实转述,不超出原文结论)

引用文件:`evals/results/summary-2026-11-10-1ad563d18360.md`(run7,LME t30)、
`summary-2026-11-10-c7d7ddc3f2b2.md`(run8,LoCoMo t30)。协议
`qa-accuracy (real provider, LLM-judged)`;回答 `deepseek-flash`,judge
`deepseek-v4-pro`(解耦);预算 600 tokens;n=30 题子集,judged 30/30、unjudged 0。

| 数据集 | 条件 | accuracy | context tokens(本地估,~) |
|---|---|---:|---:|
| LongMemEval-S t30 | C0 | 0.0167 | 136 599 |
| LongMemEval-S t30 | C3 | 0.0 | 1 507 |
| LoCoMo t30 | C0 | 0.25 | 18 304 |
| LoCoMo t30 | C3 | 0.20 | 3 243 |

**如实解读(summary 与 README 的原文口径,不添加结论)**:

- 这两个子集上 **C3 未超过 C0**(LoCoMo 20% vs 25%,小样本噪声带内;LME 两者近地板);
  不构成任何"记忆提升准确率"的 claim;
- 立得住的差异是**上下文开销**:LoCoMo 上 C3 以 ~1/5.6 的 context tokens 取得噪声带内
  相当的精度;LME 上 C0 的 context 是 C3 的 ~90×;
- accuracy 数字**尚不构成简历级结论**:须先过 30 题人工复核(盲评草稿
  `evals/results/human-review-*-draft.md` 已生成;门槛:一致率 ≥80% 且反转 = 0;
  `scripts/make_human_review.py` 把 judge 分隔离在文末防锚定);
- 2026-11-09 的存档共 6 份 summary:3 份 mini echo 冒烟(`1b20e74c92a1` /
  `2be26a31000f` / `5482fef67d4c`,offline proxy)、1 份 mini 真跑(`560dfb89a0d9`,
  C0/C3 均 0.9167,judged 6/6)、2 份 t30 早期真跑(`d8fbd80b53c1` LME C0/C3 均 0.0、
  `912da282bbc8` LoCoMo C0 0.2267 / C3 0.1667);全部 write-once 存于
  `evals/results/`,不与本批混报。devlog 已勘误早期 run 的 answered_by 标签误读,
  并声明 run7/run8 为 P1.a/b/c + P2 之后代码头上的正式重跑。

## 7. bench:scope 与解读

### 7.1 `bench/latency.py`(schema `rw-bench-latency/1`)

- 构造:进程内 SQLite + hash 嵌入(dim 64)、单线程 CLI;`SIZES=[10,100,1000]`、
  `REPS=30`;量 `compile()` 与 `search()` 的 p50/p95;
- **scope 标签随文件走**(`latency.py:31-34`):`scope: in-process sqlite + hash
  embeddings, single-threaded client; excludes network/OS jitter; NOT comparable to
  prod pg+HNSW numbers`——刻画**算法斜率**,不是服务端延迟口径;
- 已落盘数字(`bench/results/latency-2026-11-09-1.json`,mock 机制展示,非生产实测):

| memory_count | compile p50/p95 | search p50/p95 |
|---:|---|---|
| 10 | 3.875 / 6.262 ms | 2.964 / 5.924 ms |
| 100 | 7.870 / 13.161 ms | 4.542 / 6.580 ms |
| 1000 | 27.782 / 74.898 ms | 23.989 / 40.812 ms |

### 7.2 `bench/cache_ab.py`(离线半 schema `rw-bench-cache-ab/1`,在线半 `…-online/1`)

- **离线半**(默认,6 轮):同一记忆集,对比 v2(compiler 内容寻址序)与 v1
  (score 漂移模拟:每轮轮转 profile 条目);可测的只有**前缀稳定性**;
  `uncomputed{cache_hit_tokens, ttft_ms}` 恒 null(mock 不能测,绝不编);
  结果(`bench/results/cache-ab-2026-11-09-1.json`):v2 恒 1 种前缀,v1 漂出 6 种。
- **在线半**(`--provider`,fail-closed):需 `openai_compat` + key;轮数 ≥2
  (首调用必 miss,比较的就是 delta);每轮把 v2/v1 两个 system 块**都**发给真 provider,
  逐调用记录 usage 与 wall;CostGuard 计费(默认 cap ¥5);cache 命中字段按
  `prompt_cache_hit_tokens / cached_tokens / cache_read_input_tokens` 顺序认领,
  字段缺失 = 该半 unmeasured,不猜。
- 已落盘(`bench/results/cache-ab-online-2026-11-10-1.json`,真 provider 12 次调用):
  稳定性复证(v2=1 / v1=6);**`prompt_cache_hit_tokens` 字段存在且 12 次全为 0**
  ——是"测得 0"而非"没测",因此**无命中差值可 claim**;假设(前缀 142 tok 或低于
  分块粒度/预热窗/字段语义)与落定途径(控制台对拍)记于 ADR-003 数证补录;
  wall 均值 v2 2403.5 / v1 3362.3 ms 是**整响应**延迟(非流式,n=6/臂),不作显著性论断。

## 差异说明(与既有文档)

- `README.md` 徽章与 quickstart 写"110 tests passing";与评测无关,但同属数字纪律:
  当前实测 112 passed + 1 skipped(2026-11-13),README 未刷新(见 08 篇)。
- `docs/decisions/ADR-006` 写"LongMemEval/LoCoMo 未落地";其"在线化修订"段落后
  数据集已 pin 并落盘(run5–run8),ADR-006 尾段为历史快照,以 `docs/eval-datasets.md`
  + `configs/datasets.toml` + 本文为准。
