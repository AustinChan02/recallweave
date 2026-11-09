# TODO 路线图（待办的具体实施步骤）

> 与 `docs/devlog.md` 待办清单一一对应；每项给：前置条件 → 具体步骤 → 改动文件 → 验收口径。
> 诚实红线不变：mock 产物永远标注、预算超限 fail-closed 拒跑、简历数字 `【实测填入】`。

---

## Track P0 · 真 provider 评测（解锁简历数字的唯一路径）

### 阶段 0：准备【需要你提供：API key + provider 决策】
建议 DeepSeek（OpenAI 兼容、按量便宜、控制台可查 cache-hit tokens，正好喂 §7.4 实验）。Qwen/GLM 备选同价档。

| 步骤 | 内容 |
|---|---|
| 0.1 | `cp .env.example .env`，填 `RECALLWEAVE_LLM_PROVIDER=openai_compat` / `BASE_URL=https://api.deepseek.com/v1` / `API_KEY` / `MODEL=deepseek-chat`（.gitignore 已挡 .env）|
| 0.2 | 连通性冒烟：`uv run python - <<'EOF' … from_env().llm_adapter … chat_json 一次提取` → 断言 `provider == "openai_compat:deepseek-chat"`；顺手验证 429/超时重试路径日志 |
| 0.3 | 预 spend 核对：控制台用量 ≈ 一次提取调用（<¥0.1） |

验收：`.env` 生效 + `make run` 下 `/v1/health` 显示 `llm_provider=openai_compat:<model>`。

### 阶段 1：eval 框架升级为"可在线"（纯代码，可先做）
| 步骤 | 改动 | 验收 |
|---|---|---|
| 1.1 | `evals/conditions.py::run_condition` 增加 **answer 步**：把 `context_messages + question` 发给 answer 模型（`OpenAICompatAdapter.chat_text`），返回 answer；mock 模式保持 echo 协议（分支显式声明） | mock 冒烟结果与 run1/run2 逐字节一致（协议未变） |
| 1.2 | `evals/run_eval.py`：`--judge llm` 加入 choices；定义 `JudgeVerdict(BaseModel){score, rationale}` 喂 `LLMJudge`；结果文件增 `provider.answered_by` / `usage{answer_tokens, judge_tokens}` | `--judge llm` 在 mini 6 题真跑一轮 |
| 1.3 | **预算护栏**：`configs/prices.toml`（单价+控制台出处注释）；每次调用后累计估算成本，超 `--max-cost-yuan` 即 SystemExit 拒跑（fail-closed，不静默） | 人为调小上限 → 运行中止且留痕 |
| 1.4 | 结果文件协议字段升级：`protocol: "qa-accuracy (real provider)"`；人工复核列（30 题模板 `evals/human-review-template.md`） | 与 ADR-006 差异登记同步 |

预算速算（deepseek-chat 量级，以控制台价为准）：mini 6 题 × C0/C3 ≈ 3 次回答 + 6 次 judge ≈ <¥1；30 题 × 2 条件 ≈ 120 次调用 ≈ ¥2–5；完整 150 题 × 4 条件在 ¥20 档内可控，护栏默认 `--max-cost-yuan 200`。

### 阶段 2：数据集入库
| 步骤 | 内容 | 产出 |
|---|---|---|
| 2.1 | websearch 确认 LongMemEval-S 官方发布页/版本/license；LoCoMo 同理 | `configs/datasets.toml`（source、pin、sha256、license、url） |
| 2.2 | 实现 `scripts/download_data.py`：下载到 `data/datasets/`（已在 data/ gitignore 内）→ 校验 sha256 → 按 type 抽子集 → 转成 `item schema jsonl`（qid/type/turns/question/reference） | 同一 commit 重跑 byte-identical |
| 2.3 | `docs/eval-datasets.md`：来源/许可/预处理/已知偏差（对立面：许可不明 → 维持"不可运行"状态，拒绝假装） | 许可段落 + 人工复核流程定义 |
| 2.4 | 全量抽样跑通：30 题（每 type）× `--judge llm` 完整落盘 | `judged/total` 如实 |

### 阶段 3：正式评测 + 简历回填
1. 全量 run：数据集题集 × C0/C3（时间富余加 C2），结果 write-once。
2. §7.4 第二半：DeepSeek 控制台日志对拍 `bench/cache_ab.py --write` 的稳定性结论（cache-hit tokens/TTFT 差值），更新 ADR-003 数证。
3. 人工复核 30 题 → 不一致率写进 summary 旁文件。
4. 回填 `agent-proposals/RESUME-READY.md` A§1.4 的 `【实测填入】`：eval accuracy、bench p95、cache 命中差值——**以仓内 write-once 文件为准**。
5. `docs/devlog.md` 记录当日 run 与成本。

---

## Track P1 · 提案 §4.4/§4.5/`/v1/reflect`（可全程离线开发，不依赖 key）

### P1.a 会话摘要（§4.4）
1. `migrations/002_session_summaries.sql`（pg）+ `ensure_schema` sqlite 分支：`session_summaries(session_id, seq, content, token_est, provider, created_at)`，只追加。
2. `service.summarize_session()`：滚动计数每 20 轮触发；cheap model = 同 llm adapter（注释注明廉价档建议）；**mock 模式 = no-op + provider 标签**（不伪造摘要）。
3. compiler episodic 层改造：优先取摘要（会话序天然稳定，满足 §4.3 顺序规则），补充 event 记忆；episodic cap 不变。
4. tests：触发阈值边界 / mock fallback 铸bi / 与 event 补位的顺序稳定性。

### P1.b 遗忘=分层存储（§4.5）
1. `MemoryRepo.archive_stale(now, tau=30d, importance_lt=0.3)`：active → archived（可恢复，√ 不删行）。
2. 触发：`scripts/forget.py` + `make forget`（个人部署明确不做后台线程）；`/v1/stats` 增加 archived 分类。
3. tests：边界（正好 30d / importance 0.3）与恢复路径（archived 不参与检索）。

### P1.c `/v1/reflect` 真实现
1. 聚簇：对 active 记忆两两余弦 > 0.95 的近重复簇（复用 hash embedding）。
2. 每簇送既有 conflict 管道（LLM 或 mock sticky NOOP）→ 产出 UPDATE 决策，全走 `meta.decision` 审计。
3. 路由从 501 换真实现；tests：聚簇→合并链→审计字段；501 测试相应替换。
4. devlog 记删除 501 诚实 stub 的时间点（面谈卖点：从"诚实拒绝"到"真实现"的演进）。

---

## Track P2 · 工程化（半天量级）
1. `Makefile` lint 纳入 `scripts/ sdk_demo.py`（我们手工跑过，但 MAKE 目标没含——补上）。
2. mypy：`make typecheck`；第一步 `mypy src/recallweave --ignore-missing-imports`，公共接口 strict 渐进（proposal §6 允许视时间定 strict）。
3. CI：`.github/workflows/ci.yml`：ruff → mypy → pytest(sqlite) → integration（service container: pgvector/pgvector:pg16 + env 注入）→ eval-smoke 用 `--results-dir /tmp/eval-ci`（规避 write-once 与仓内产物冲突）。
4. GitHub repo：`README.md` 顶部查重提示照办后公开（`make reset` 后 push）。

---

## 已完成（2026-11-09 当天勾掉）
- [x] pg 集成测试真跑一轮：`RECALLWEAVE_TEST_PG_URL=... pytest -m integration` → **1 passed**（含当日两 bug 修复：pgvector 定宽、asyncpg SET 绑定）。
- [x] 仓库首个 commit + 文档补全 commit（git log: 6fea096, fe0725a）。
