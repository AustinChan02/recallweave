# 08 · 测试、质量门、CI 与运维脚本

> 来源:`tests/`(conftest 与各测试文件)、`pyproject.toml`、`Makefile`、
> `.github/workflows/ci.yml`、`scripts/`、`sdk_demo.py`。测试数量为**当日实测**
> (`uv run pytest` → 112 passed, 1 skipped,2026-11-13)。

## 1. 测试布局

- 全默认离线:mock LLM + hash 嵌入(dim 64)+ 临时 SQLite(`conftest.py:21,26-43`);
  `env_file=""` —— 测试**绝不读仓内残留的 `.env`**(`conftest.py:40`);
- pg 集成:唯一文件 `tests/test_integration_pg.py`,`pytestmark = pytest.mark.integration`
  (`pyproject.toml` 注册 marker),门控 `RECALLWEAVE_TEST_PG_URL`;未设 → skip;
  验证 sqlite 无法覆盖的方言面:HNSW 检索(pgvector 字面量绑定,必须 512 维)、
  upsert ON CONFLICT、真 Postgres 上的 superseded 链;
- MockLLM 严格 FIFO 契约意味着测试**必须声明每次 LLM 调用**——mock 不能静默替真
  provider 作答(`conftest.py` 模块注释)。

| 文件 | 用例数 | 覆盖面 |
|---|---:|---|
| `test_write_pipeline.py` | 21 | 提取/去重/路由表驱动/UPDATE 链/fail-closed |
| `test_fusion_pure.py` | 18 | RRF/加权/MMR 纯函数表驱动 |
| `test_llm_transport.py` | 15 | 适配器重试/4xx/schema 漂移/usage 记账 |
| `test_api.py` | 11 | HTTP 端点 + 认证 + 错误语义 |
| `test_compiler.py` | 9 | 四层装配/预算/稳定性/trace |
| `test_forgetting.py` | 8 | P1.b 边界(严格 <)/审计/恢复闭环 |
| `test_eval_pricing.py` | 7 | CostGuard/价格表/护栏中止 |
| `test_eval_realmode.py` | 7 | 在线协议接线/拒跑三连/unjudged |
| `test_reflect.py` | 6 | P1.c 聚簇/合并/审计/诚实 skip |
| `test_session_summaries.py` | 6 | P1.a 阈值/append-only/接管与回退 |
| `test_e2e_pipeline.py` | 2 | 端到端(提取→消解→编译) |
| `test_pg_migrations_loader.py` | 2 | 迁移加载器(注释先剥再切分)回归 |
| `test_integration_pg.py` | 1 | pg 集成(需 `RECALLWEAVE_TEST_PG_URL`) |
| **合计** | **113** | **112 passed + 1 skipped** |

## 1.1 测试确定性策略(ADR-005 + devlog 2026-11-09)

hash embedding 的相似度与内容相关,默认阈值(0.80/0.93)不能保证某条候选恰好落窗,
因此"必须走 LLM 路由"的用例不依赖默认阈值:

| 用例类型 | 钉法 | 来源 |
|---|---|---|
| 路由本身 | 表驱动纯函数测试:`sim=None/0.5/0.79/0.85/0.929/0.93/0.99 → add/llm/near_dup` | `test_write_pipeline.py`,ADR-005 |
| 强制走 LLM | override `conflict_lo=0.001, dedup_near=0.999` 把路由钉死在 llm 分支 | ADR-005 测试注记 |
| near-dup skip | 默认阈值下用**完全相同内容**(cosine 恰 1.0 ≥ 0.93),与阈值自然联结 | devlog |
| 真实 UPDATE 链演示 | 挑实测 cosine≈0.8853 的表述对,天然落入 (0.80, 0.93) 窗 | `demo_offline.py:42-90` |
| settings 覆盖 | `make_settings(**overrides)` 直注入 + `env_file=""`(不读仓内 .env) | `conftest.py:25-43` |

Settings validator 的快速失败也是测试面:四层预算 pct 单层 override 会被
sum==1 validator 拒绝(devlog 2026-11-09 记录三次实踩,0.9/1.1/0.72)。

## 2. 质量门(`Makefile`)

| 命令 | 内容 |
|---|---|
| `make test` | `uv run pytest`(离线套件) |
| `make lint` | `ruff check` + `ruff format --check`,范围 `src tests evals bench scripts sdk_demo.py`(2026-11-13 复核 73 files 全绿) |
| `make typecheck` | `mypy src/recallweave --ignore-missing-imports`(0 errors in 32 files,devlog P2 收口) |
| `make setup` | `uv venv --python 3.12` + `uv sync --extra dev` |

## 3. CI(`.github/workflows/ci.yml`,双 job)

```
push(main)/pull_request
  ├─ suite (ubuntu-latest)
  │    checkout → uv + Python 3.12 → uv sync --extra dev
  │    → ruff check + format --check (src tests evals bench scripts sdk_demo.py)
  │    → mypy src/recallweave --ignore-missing-imports
  │    → pytest (sqlite 套件;pg 集成无 env 自动 skip)
  │    → eval smoke:run_eval.py --dataset evals/fixtures/mini_eval.jsonl
  │       --conditions C0,C3 --judge mock --results-dir /tmp/eval-ci
  │       (离线 fixture、mock judge;结果写 /tmp,不碰仓内 write-once 目录)
  └─ pg-integration (ubuntu-latest + service container)
       services: pgvector/pgvector:pg16(recallweave/recallweave,5432,healthcheck)
       env: RECALLWEAVE_TEST_PG_URL=postgresql+asyncpg://…@localhost:5432/recallweave
       → pytest tests/test_integration_pg.py
```

CI 诚实约束(ci.yml 头注):CI 内无 API key、无伪造数字;pg job 是独立 env,
sqlite 套件必须在没有 Postgres 的环境下保持绿。
真实事件:CI 首跑抓到迁移加载器 bug——002 注释里的分号把语句流切在注释中间
(先按 `;` 切再剥注释的错误顺序);修复为**先剥 `--` 注释行、再按 `;` 切分**
(`db/session.py:58-62`),新增 `test_pg_migrations_loader.py` 回归
(devlog 2026-11-13,commit `7940527`,CI 第二轮双 job 全绿)。

## 4. scripts/ 与演示入口逐个

| 脚本 | 用途 | 关键行为(来源) |
|---|---|---|
| `scripts/demo.sh`(`make demo`) | 对**运行中的服务**做端到端演示,每行输出来自线上真实响应 | 步骤 0 用 `/v1/health` 的 `llm_provider` **守门**:发现服务带真实 key(`openai_compat`)即退出并给出 `RECALLWEAVE_LLM_PROVIDER=mock make run` 重启指令——离线演示强制 mock 模式;后续步骤:诚实空操作提取 → `seed_demo.py` 种子 → 三路检索 → 编译 → SDK 视图 → stats + `/docs` |
| `scripts/seed_demo.py`(`make seed`) | 给 mock 模式服务**声明式脚本化种子** | `MockLLMAdapter([SCRIPTED_EXTRACTION], sticky=STICKY_NOOP)`;3 条种子(花生过敏 fact imp=1.0 / 粤菜 preference 0.6 / 周四加班 instruction 0.5),全部行内 `provider=mock:scripted-seed` 标签;复跑时近重门以 sim≈1.0 拦截(dedup 在岗);**service.ingest 的 session_id 必须传 `uuid.UUID`**(直传 str 会绑定报错,devlog 2026-11-13) |
| `scripts/demo_offline.py` | 零服务版离线演示(进程内服务 + 临时 SQLite) | FIFO 显式脚本含一次真实 UPDATE 链演示(挑了 cosine≈0.885 的表述对,天然落窗 `(0.80, 0.93)`);输出带 `provider_note:"mock(脚本化产出,禁止作为实测引用)"` |
| `scripts/forget.py`(`make forget`) | P1.b 遗忘任务(归档,非删除) | `--dry-run` 只报候选写零字节;`--tau-days`/`--importance-lt` 显式覆盖(默认取 settings——"偷偷改自己阈值的任务正是本仓禁止的静默漂移");`AsyncEngine` 关闭用 `dispose()` 非 `aclose()` |
| `scripts/download_data.py` | 数据集 pin 桥:`list` / `download` / `convert` | sha256 **先于一切**校验(不符即 `SystemExit`);分层配额转换(seed 2026,重跑字节一致);manifest 写 `evals/fixtures/converted-manifest.json`;许可不明 = 拒绝假装可下载 |
| `scripts/make_human_review.py` | 生成 30 题盲评草稿(write-once) | 表内只放 qid/type/question/reference/模型回答,**judge 分隔离在文末**(防锚定);草稿已存在即拒写;正式文件名 `human-review-<run_id>.md` |
| `sdk_demo.py`(仓根) | SDK 视图演示 | 依赖运行中的服务(`RECALLWEAVE_BASE_URL` 可覆写,默认 `http://127.0.0.1:8100`;key 默认 `dev-key-1`);演示 fail-closed ingest 提示与 provider 标签;`RecallWeaveAPIError`/连接错误都有可读提示 |

## 5. SDK 用法示例(`src/recallweave/sdk/client.py` + `sdk_demo.py`)

```python
from recallweave.sdk import RecallWeaveClient

with RecallWeaveClient("http://127.0.0.1:8100", api_key="dev-key-1") as c:
    c.health()                                            # GET /v1/health
    c.ingest([{"role": "user", "content": "我对花生过敏。"}], session_id=None)
    c.search("我能吃什么", k=5)                            # POST /v1/memories:search
    c.compile([{"role": "user", "content": "我能吃什么？"}], budget_tokens=600)
    c.get_memory("<uuid>")                                # GET /v1/memories/{id}
    c.delete_memory("<uuid>")                             # DELETE /v1/memories/{id}
    c.stats()                                             # GET /v1/stats
# 错误:resp.status>=400 → RecallWeaveAPIError(status, code, message, detail)
```

客户端无重试(服务端已 fail-closed;对 502 双重重试会放大写入副作用,
`client.py` 模块注释);未实现 summarize/reflect 方法(如实)。

## 5.1 离线演示链(`demo_offline.py` 三轮脚本,FIFO 显式声明)

```
轮 1:提取脚本 → 1 候选("用户对花生过敏,吃了花生会起疹子",imp 0.9)→ ADD(无邻居)
轮 2:提取脚本 → 1 候选(event "上个月去大连出差三天…")→ ADD
轮 3:提取脚本 → 1 候选("…吃花生酱也会起疹子",imp 0.85)
      → 与轮 1 事实 hash-cosine ≈ 0.885,落入 (0.80, 0.93) 冲突窗
      → 消解脚本:UPDATE(target=轮 1 记忆 id,合并 content,confidence 0.9)
      → 双时态链:旧行 superseded_by=新行
```

该演示每个 LLM 输出都是队列里显式 push 的声明(无 sticky、无隐式默认),
输出统一附 `provider_note:"mock(脚本化产出,禁止作为实测引用)"`(`demo_offline.py:128`)。
对运行中服务的版本则是 `demo.sh`(HTTP 线上响应,`/v1/health` 守门强制 mock)。

## 6. 故障排查

| 症状 | 机理与处置 | 来源 |
|---|---|---|
| `make run` 端口被占 | 8100 被(旧进程/别的服务)占用;uvicorn 直接失败。换端口:`uv run uvicorn recallweave.api.app:app --port <n>`,SDK/demo 侧同步改 Base URL | `Makefile:8`,`sdk/client.py:23` |
| demo.sh 报 "server is running with llm_provider=openai_compat" | `.env` 残留真实 key;守门逻辑按设计**拒绝继续**(离线演示必须 mock),按提示 `RECALLWEAVE_LLM_PROVIDER=mock make run` 重启(env var 优先级高于 `.env`) | `demo.sh:28-34`,`config.py:21` |
| mock 提取返回 `extraction_error: "mock llm: script queue exhausted"` | 正常且诚实:live mock 是空队列,拒绝编造;要演示读路径用 `scripts/seed_demo.py` 种子 | `llm.py:211-216`,`seed_demo.py:1-14` |
| 种子脚本 SQLAlchemy 绑定错误 | `service.ingest` 的 `session_id` 必须是 `uuid.UUID`(HTTP 路径由 pydantic 预解析,脚本要自己转) | devlog 2026-11-13 |
| pg 路径建表报语法错误(历史) | `_load_pg_migrations` 现在**先剥 `--` 注释行再按 `;` 切分**;旧顺序会把 002 注释里的分号切进 SQL。已被 `test_pg_migrations_loader.py` 与 CI pg job 看守 | `db/session.py:46-63` |
| pg 集成测试全 skip | 未设 `RECALLWEAVE_TEST_PG_URL`;`make db-up` 后设该 env 再 `uv run pytest -m integration` | `test_integration_pg.py:26-38` |
| pgvector 报 `expected 512 dimensions, not N` | 列是 `vector(512)`,pgvector 强制定宽;实验必须用与生产一致的形状 | `test_integration_pg.py:49-52` |
| 向量检索静默返回空(sqlite) | 历史坑:跨格式字符串 JOIN(无连字符 hex vs dash 字符串)永不匹配;现规则 = ORM 取候选 + dash 键 IN 取 embedding,禁止裸 JOIN | `vector_index.py:6-13`,devlog |
| 评测跑完没有结果文件 | CostGuard 超限中止(超出 `--max-cost-yuan`),**0 文件落盘是设计行为**;或撞 write-once(同名 run 文件已存在 → `SystemExit`) | `pricing.py:69-82`,`run_eval.py:71-75` |
| mypy 报 `Decision.op` 等类型错 | reflect 在写入门处 `cast(Kind, …)`(DB 列是 str);不要用 truthy 技巧统一 pg/sqlite 方言分支(显式两支) | `reflection.py:126`,devlog P2 |

## 差异说明(与既有文档)

- `README.md` 多处写 **110 tests passing**(徽章、快速开始、devlog 摘引);实测当前
  为 **112 passed + 1 skipped**(2026-11-13,`uv run pytest`;增量来自
  `test_pg_migrations_loader.py` 等);README 数字未刷新,以本文为准。
- `README.md` 项目结构注释写 `docs/ · architecture.md …`(未列 `docs/tech/`)——
  本目录为新增,README 未同步。
- `docs/architecture.md`"遗留(P1,未实现)"整节过时(P1.a/b/c 已实现),
  详见 `02-write-pipeline.md` 差异说明。
