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

### 阶段 3：正式评测 + 简历回填【代码侧 ✅ 2026-11-10 完成；复核/核价/回填 = 用户侧】
1. [x] 全量 run：pin 集 t30 × C0/C3 ×（真空答 + 解耦 judge，30/30×2 落盘 write-once）——run7（LME-t30：C0 0.0167 / C3 0.0，run_id 1ad563d18360）+ run8（LoCoMo-t30：C0 0.25 / C3 0.2，run_id c7d7ddc3f2b2），sha256 与 manifest 复核一致后跑；"时间富余加 C2"未做，如实留空。
2. [x] §7.4 第二半（代码侧）：`bench/cache_ab.py --provider` 线上半实跑落盘（`cache-ab-online-2026-11-10-1.json`：前缀稳定性真 provider 复证 v2=1/v1=6；**12 调用 `prompt_cache_hit_tokens` 全为 0 → 无命中差值可 claim**，假设已标注）+ ADR-003 数证补录；**控制台日志对拍 = 用户侧**，落定后回填本行。
3. [ ] 人工复核 30 题：4 份盲评草稿已生成（`evals/results/human-review-*-draft.md`，judge 分隔离文末防锚定，`scripts/make_human_review.py`）；复核本体 + 不一致率旁文件 = **用户侧动作**。
4. [ ] 回填 `agent-proposals/RESUME-READY.md` A§1.4 的 `【实测填入】`：**用户侧**，以仓内 write-once 文件为准，且须先过步骤 3（一致率 ≥80% 且反转 = 0）。
5. [x] `docs/devlog.md` 记录当日 run 与成本（DEVLOG「P0 阶段 3」小节；护栏累计上界 ≈¥3.28 / 预算 ¥200）。

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

### P1.c `/v1/reflect` 真实现【✅ 2026-11-10 完成，501 stub 退役】
1. [x] 聚簇：active 记忆扫描 ≤200，纯 Python O(n²) 余弦（hash embedding 复用）+ union-find，阈值严格 > 0.95；无簇 → 零 LLM 调用（空队列自证）。
2. [x] 每簇送既有 conflict 管道（candidate=最新成员 + resolve_with_llm + fail-closed coercion 原样复用）→ 单事务应用；UPDATE 合并成新行（importance=max，`meta.decision{reflect,cluster}` 审计 + 全员 bitemporal 关链）；DELETE/NOOP 如报所述。
3. [x] 路由 501 → 真实现；tests：聚簇→合并链→审计字段 / NOOP / DELETE / 未脚本化诚实 skip / coercion 拒改名 candidate / API 级零伪造面。
4. [x] devlog 记录 501→真实现的时间点与「同款 detached 坑在 reflect 复现」的落网记。

---

## Track P2 · 工程化（半天量级）【✅ 2026-11-10 完成，commit 00612f6】
1. [x] `Makefile` lint 纳入 `scripts/ sdk_demo.py`：check 行原有，format --check 行已补；`sdk_demo.py` 重排版后 65 files 全绿。
2. [x] mypy：`make typecheck`（`.PHONY` 已同步）——14 个错误逐一**真修**（config 模块级缓存 / fusion MMR 守卫 / session 返回类型 / repos 显式方言分支替换 truthy 诡计 / llm 返回 tuple 类型 / reflect `cast(Kind,…)` + Decision 正型 / service 复用 `_provider_label` / app handler 契约签名），**未新增任何 type: ignore**；`0 errors in 32 files`。
3. [x] CI：`.github/workflows/ci.yml` 落地——suite job（ruff → mypy → pytest(sqlite) → 离线 eval-smoke 用 `--results-dir /tmp/eval-ci` 隔离仓内 write-once）+ pg-integration job（`pgvector/pgvector:pg16` service container + `RECALLWEAVE_TEST_PG_URL` 注入）。
4. [ ] GitHub repo 公开（README 顶部查重提示已就位；`make reset` 后 push——**用户侧** GitHub 动作，代码侧无剩余）。

---

## 已完成（2026-11-09 当天勾掉）
- [x] pg 集成测试真跑一轮：`RECALLWEAVE_TEST_PG_URL=... pytest -m integration` → **1 passed**（含当日两 bug 修复：pgvector 定宽、asyncpg SET 绑定）。
- [x] 仓库首个 commit + 文档补全 commit（git log: 6fea096, fe0725a）。
