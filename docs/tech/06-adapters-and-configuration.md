# 06 · 适配器与配置:LLM / Mock / 嵌入 / 全部环境变量

> 来源:`src/recallweave/adapters/llm.py`、`embeddings.py`,全部默认值抄自
> `src/recallweave/core/config.py`;部署事实来自 `docker-compose.yml`、`.env.example`、
> `Dockerfile`、`Makefile`。

## 1. LLM 适配器契约(`adapters/llm.py`)

**契约**:`chat_json(system, user, schema)` **永远**返回按 pydantic schema 校验过的
dict,或抛 `ProviderError` / `ProviderSchemaError`——没有第三态(模块 docstring +
`llm.py:87-95`)。绝不静默降级:调用方(写管道)拿到异常后自行决定跳过并上报。

| 行为 | 事实 | 来源 |
|---|---|---|
| 请求体 | `{model, temperature, messages[system,user], response_format:{"type":"json_object"}}`(仅 `chat_json`) | `llm.py:106-117` |
| 输出解析 | 剥 ```` ```json ```` 围栏 → `json.loads` → 必须 dict(`parse_json_strict`);取 `choices[0].message.content`,必须 str | `llm.py:25-43` |
| schema 校验 | `schema.model_validate(obj).model_dump(mode="json")`;失败抛 `ProviderSchemaError` | `llm.py:46-50` |
| 可重试 | 传输失败(超时/`TransportError`)、HTTP 429/5xx(尊重 `Retry-After` 头,否则 0.5s 起)、**schema 失败也重试**(provider 会漂移) | `llm.py:119-131,143-167` |
| 不可重试 | HTTP 4xx → `ProviderError`(永久,`raise_for_status` 分支);`ProviderError` 本体在重试循环里直接 `raise` | `llm.py:132-137,156-157` |
| 退避 | `0.5 × 2^attempt` 秒,或 429/5xx 的 `Retry-After` | `llm.py:164` |
| 重试预算 | `max_retries`(默认 2)→ 共最多 3 次尝试;**传输失败与 schema 失败共享同一预算** | `llm.py:73,149` |
| catch 顺序 | `ProviderSchemaError` 必须在 `ProviderError` **之前**(子类先行),否则 schema 漂移被当永久失败——真实踩过的继承树路由坑 | `llm.py:152-157`,devlog |
| usage 记账 | `last_usage` 只在**成功**调用后写入(失败调用不计 spend);评测护栏在每次调用后立刻读取 | `llm.py:80-82,139-140` |
| provider 标签 | `openai_compat:<model>` | `llm.py:79` |

`chat_text(system, user)`:无 json_mode、不校验 schema,返回 strip 后文本;
同样走 `_with_retries`(`llm.py:97-104`)。

`make_llm_adapter(settings)`(`llm.py:219-231`,唯一装配点):
- `llm_provider=="mock"` → `MockLLMAdapter([])`;
- `openai_compat` 但 `llm_api_key` 为空 → `ProviderError`(缺 key 即拒,不半启动)。

## 2. MockLLMAdapter:严格 FIFO + sticky(`llm.py:176-216`)

- 队列元素三型:`dict`(直入 schema 校验)/ 合法 JSON 字符串(先严格 parse,失败抛
  `ProviderSchemaError`)/ `Exception`(直接 raise,可注入失败);
- `chat_json` 每次弹出队首并按目标 schema 校验;`chat_text` 返回 `str(item)`;
- **队列耗尽默认 `ProviderError("mock llm: script queue exhausted")`** —— 测试与
  demo 必须显式声明每次调用,这是"诚实设计":mock 永不静默替真 provider 作答
  (live mock 的提取是诚实空操作,demo.sh 步骤 1 即展示该行为);
- `sticky=payload`:显式声明的确定性兜底——仅当队列空**且**构造时给了 sticky 才启用,
  每次同样过 schema 校验;评测 harness 用它(如 `STICKY_NOOP`)并在结果文件里披露,
  **不是静默默认**(默认构造保持严格 fail-closed);
- `provider_label` 可自定义(如 `mock:scripted-eval`、`mock:scripted-seed`);
- `push(item)` 运行中追加。

## 3. 嵌入双实现(`adapters/embeddings.py`)

| | HashEmbedding | BGEEmbedding |
|---|---|---|
| 用途 | **仅测试/管道拟合**(similarity 无语义,ADR-001 + README 诚实注记) | 真语义路径 |
| 实现 | sha1 种子确定性伪向量,bag-of-token 累加,L2 归一;单文本最多 64 个 token(防病态长文本);8192 条 FIFO memo cache | `sentence-transformers` 本地模型 `BAAI/bge-small-zh-v1.5`,**lazy import**(默认离线路径不下载模型) |
| 维度 | `embedding_dim`(默认 512;测试用 64) | 模型实际维度(512) |
| `provider` 标签 | `hash` | `bge:BAAI/bge-small-zh-v1.5` |
| 选择 | `make_embedder`:非 `bge` 一律 hash(`embeddings.py:99-102`) | `embedding_provider=="bge"` |

共享分词器 `tokenize_for_lexical`(`embeddings.py:22-32`):latin 词+数字(小写)+
CJK 二元组(单字 run 保留单字);被 lexical 检索路、HashEmbedding 双方使用(ADR-002:
一个分词器,防"检索/查重语义漂移")。

## 4. 全部环境变量(`core/config.py` 逐字段核对)

前缀 `RECALLWEAVE_`,`.env` 文件读取,未知键忽略(`extra="ignore"`)。

### 4.1 基础与身份

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_APP_ENV` | `dev`(`Literal["dev","test","prod"]`) | 日志渲染 dev=ConsoleRenderer+DEBUG,否则 JSON+INFO(`core/logging.py:11-16`) |
| `RECALLWEAVE_DB_URL` | `sqlite+aiosqlite:///./data/recallweave.db` | 数据库连接串;pg 形见 §5 |
| `RECALLWEAVE_API_AUTH_ENABLED` | `True` | False 时无 Bearer 落 `dev_user`(`deps.py:29-31`) |
| `RECALLWEAVE_API_KEYS_JSON` | `{"dev-key-1": "11111111-1111-1111-1111-111111111111"}` | token→user JSON map;解析失败 fail-closed(`config.py:115-122`) |
| `RECALLWEAVE_DEV_USER` | `11111111-1111-1111-1111-111111111111` | 免认证模式下的固定身份 |

### 4.2 LLM 适配器

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_LLM_PROVIDER` | `mock`(`Literal["mock","openai_compat"]`) | mock=全离线;openai_compat 需 key |
| `RECALLWEAVE_LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容端点(自拼 `/chat/completions`) |
| `RECALLWEAVE_LLM_API_KEY` | `""` | openai_compat 必填,缺失启动即拒 |
| `RECALLWEAVE_LLM_MODEL` | `deepseek-chat` | |
| `RECALLWEAVE_LLM_TEMPERATURE` | `0.0` | 提取/消解/摘要恒温度 0 |
| `RECALLWEAVE_LLM_TIMEOUT_S` | `60.0` | httpx 超时 |
| `RECALLWEAVE_LLM_MAX_RETRIES` | `2` | 传输+schema 共享预算 |

### 4.3 嵌入与向量索引

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_EMBEDDING_PROVIDER` | `hash`(`Literal["hash","bge"]`) | bge=本地 sentence-transformers |
| `RECALLWEAVE_EMBEDDING_DIM` | `512` | hash 维度 + pg `vector(512)`/检索一致性 |
| `RECALLWEAVE_HNSW_EF_SEARCH` | `40` | pg 每查询事务内 `set_config`(`vector_index.py:89`) |

### 4.4 写路径超参(调过参的窗口)

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_DEDUP_NEAR` | `0.93` | 近重门:≥ 直接跳过(ADR-005) |
| `RECALLWEAVE_CONFLICT_LO` | `0.80` | 窗下界:< 直接 ADD;validator 锁 `lo < near ≤ 1` |
| `RECALLWEAVE_EXTRACT_RECENT_TURNS` | `8` | 提取只看最近 k 轮 |
| `RECALLWEAVE_MAX_INGEST_MESSAGES` | `200` | 请求 schema 同值兜底 |
| `RECALLWEAVE_NEIGHBOR_TOP_N` | `8` | 冲突窗口邻居数 |

### 4.5 融合(`fusion.py`)

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_RRF_K` | `60` | RRF 常数 |
| `RECALLWEAVE_MMR_LAMBDA` | `0.7` | MMR λ(仅 search 端点) |
| `RECALLWEAVE_DECAY_TAU_DAYS` | `21.0` | recency 时间常数 |
| `RECALLWEAVE_W_FACT` | `1.0` | kind 权重:fact |
| `RECALLWEAVE_W_PREFERENCE` | `1.1` | preference |
| `RECALLWEAVE_W_INSTRUCTION` | `1.2` | instruction |
| `RECALLWEAVE_W_EVENT` | `0.9` | event |
| `RECALLWEAVE_IMPORTANCE_LO` | `0.7` | importance 乘子下段 |
| `RECALLWEAVE_IMPORTANCE_SPAN` | `0.6` | importance 乘子斜率(0.5 → 1.0) |

### 4.6 读路径与编译器

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_VECTOR_TOP_M` | `32` | 向量路深度 |
| `RECALLWEAVE_LEXICAL_TOP_M` | `32` | lexical 路深度 |
| `RECALLWEAVE_LEXICAL_SCAN_LIMIT` | `2000` | 候选池扫描上限(lexical + compiler) |
| `RECALLWEAVE_RECENCY_TOP_M` | `8` | recency 路深度 |
| `RECALLWEAVE_CONTEXT_BUDGET_DEFAULT` | `1500` | 总 token 预算(请求可覆写 200..8000) |
| `RECALLWEAVE_BUDGET_SYSTEM_PCT` | `0.10` | persona 层占比 |
| `RECALLWEAVE_BUDGET_PROFILE_PCT` | `0.30` | profile 层占比 |
| `RECALLWEAVE_BUDGET_EPISODIC_PCT` | `0.40` | episodic 层占比 |
| `RECALLWEAVE_BUDGET_WORKING_PCT` | `0.20` | working 层占比(validator:四层和=1,working/profile>0) |
| `RECALLWEAVE_WORKING_TURNS` | `12` | working 尾最多条数 |
| `RECALLWEAVE_PERSONA_LINE` | 见 `config.py:91-94` | persona 文案(含 PROFILE/RECENT EVENTS 指引) |

### 4.7 P1.a / P1.b / P1.c 与目录

| 键 | 默认 | 作用 |
|---|---|---|
| `RECALLWEAVE_SUMMARIZE_EVERY_TURNS` | `20` | 摘要触发阈值(P1.a) |
| `RECALLWEAVE_SUMMARY_RECENT_LIMIT` | `8` | compile 注入的最近摘要条数 |
| `RECALLWEAVE_FORGET_TAU_DAYS` | `30.0` | 遗忘:未访问天数(P1.b) |
| `RECALLWEAVE_FORGET_IMPORTANCE_LT` | `0.3` | 遗忘:importance 严格小于(P1.b) |
| `RECALLWEAVE_REFLECT_THRESHOLD` | `0.95` | 聚簇阈值(严格 >,P1.c) |
| `RECALLWEAVE_REFLECT_MAX_SCAN` | `200` | 聚簇扫描上限 |
| `RECALLWEAVE_DATA_DIR` | `data` | `Path` 字段 |
| `RECALLWEAVE_EVAL_RESULTS_DIR` | `evals/results` | 评测结果落盘目录(run_eval 默认) |

合计 46 个标量键 + 2 个 `Path` 键 = **48 个 Settings 字段**;`.env.example` 只示例了
其中 10 个(基础/身份/LLM/嵌入/预算),其余取默认值——完整对照以本表为准。

## 5. docker compose 与 `.env.example`

- `docker-compose.yml`:单服务 `db`(`pgvector/pgvector:pg16`),库/用户/密码均为
  `recallweave`,端口 5432,卷 `pgdata`,健康检查 `pg_isready`。`make db-up` 起库,
  之后设 `RECALLWEAVE_DB_URL=postgresql+asyncpg://recallweave:recallweave@localhost:5432/recallweave`
  (`.env.example:4` 注释)。
- `.env.example`:21 行,注释即文档——"Copy to .env and fill in. NEVER commit real keys.";
  未列出的键走默认(§4)。
- `Dockerfile`:`python:3.12-slim`,`pip install .`,暴露 8100,入口
  `uvicorn recallweave.api.app:app`(`api/app.py` 模块级 `app = create_app()`)。

## 差异说明(与既有文档)

- `README.md` 快速开始写 `RECALLWEAVE_LLM_PROVIDER=mock make run` 且 `.env` 可能残留
  真实 key:demo.sh 会守门(见 `08-testing-and-operations.md`);`.env.example` 的
  `RECALLWEAVE_LLM_MODEL=deepseek-chat` 与 config 默认一致。
- 无与代码冲突项;本文数值全部逐字段抄自 `config.py`。
