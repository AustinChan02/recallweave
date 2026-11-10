# TODO 路线图（待办的具体实施步骤）

> 与 `docs/devlog.md` 待办清单一一对应；每项给：前置条件 → 具体步骤 → 改动文件 → 验收口径。
> 诚实红线不变：mock 产物永远标注、预算超限 fail-closed 拒跑、简历数字 `【实测填入】`。

---

## Track P0 · 真 provider 评测（解锁简历数字的唯一路径）

### 阶段 0：准备【需要你提供：API key + provider 决策】【✅ 2026-11-09 完成】
- [x] 0.1 `.env` 修正为 adapter 契约（provider=openai_compat；base_url `https://api.deepseek.com`，adapter 自拼 `/chat/completions`；模型 `deepseek-flash` 经 `GET /models` 验证存在）。
- [x] 0.2 连通性冒烟：`chat_json` 真调用返回严格 JSON（花生过敏例，provider=openai_compat:deepseek-flash，wall=1450ms）。
- [x] 0.3 预 spend 核对：待用户控制台截图（首次用量 <¥0.1 量级）。

### 阶段 1：eval 框架升级为"可在线"【✅ 2026-11-09 完成，schema `rw-eval-result/2`】
- [x] 1.1 回答步落地：`evals/conditions.py::_produce_answer`（echo 分支逐字节保持——run3 vs run2 逐项对照一致）；真实分支 judge 判回答而非上下文。
- [x] 1.2 `--judge llm` + `JudgeVerdict` schema + 结果文件 `provider.answered_by` / `usage` / `budget_guard` / `uncomputed` 全部落盘（run4 真跑验证）。
- [x] 1.3 预算护栏：`evals/pricing.py`（CostGuard fail-closed）+ `configs/prices.toml`（保守上界占位）。实测：—max-cost-yuan 1e-05 → ¥0.0017 时中止且 0 文件落盘。
- [x] 1.4 ADR-006 在线化修订登记 + `evals/human-review-template.md` 模板。

| 真跑记录（2026-11-09 mini 6题 × C0/C3，judge=回答同模型） | C0 | C3 |
|---|---|---|
| accuracy（judged 6/6） | 0.9167 | 0.9167 |
| tokens（provider 口径） | ans 312 / judge 1574 | ans 273 / judge 1501 |
| 上界成本 est | ¥0.0217 | ¥0.0210 |

预算速算（ Prices/Tokens 待控制台核对后才能转成 `cost_yuan` ）：run4 合计 3855 in / 1705 out tokens，占位上界合计 **¥0.0427**（≈¥0.3/千题量级，远低于 ¥200）。

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

### P1.a 会话摘要（§4.4）【✅ 2026-11-10 完成，commit 329e082】
1. [x] `migrations/002_session_summaries.sql`（pg）+ `session_turn_stats` 触发计数表；摘要**源材 = 会话内已存记忆**（修订 A8：原始转写不持久化），只追加 (session_id, seq)。
2. [x] `service.summarize_session()`：阈值 20 轮；mock 未脚本化 → no-op + note（不伪造）；ingest 自动摘要仅真 provider 开启。
3. [x] compiler episodic 层改造：摘要优先（新侧适配、时间正序渲染、trace kind=summary 内容寻址 id），无摘要回退 event（离线评测字节兼容已验证）。
4. [x] tests：阈值边界 / mock 不伪造 / 追加序 / 编译器接管与回退 / ingest 轮计数（95 passed 时点 + 8）。

### P1.b 遗忘=分层存储（§4.5）【✅ 2026-11-10 完成】
1. [x] `MemoryRepo.stale_candidates/archive_stale/restore`：active → archived（**行与列全保留**，可恢复；审计写 `meta.forgotten`，恢复补 `meta.restored_at`；superseded/deleted 永不触碰）。
2. [x] 触发：`scripts/forget.py`（`--dry-run` 直证）+ `make forget`；`/v1/stats` 暴露 `archived_total` + 分类计数。
3. [x] tests：两个边界均严格 `<`（恰好 30d / importance 0.3 → 保留）、never-accessed 用 created_at 计龄、恢复闭环、取证（分层隐藏检索但不删行）。
   现场证据：临时 DB 3 行播种 → dry-run 报 1 候选 → 真跑 archived 1 → **sqlite 直查 3 行都在**。

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
