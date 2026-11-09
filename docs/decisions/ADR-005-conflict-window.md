# ADR-005 — 冲突消解为什么窄窗触发（0.80–0.93）

日期：2026-11-09 ｜ 状态：已实现（阈值即配置，可被实验推翻）

## 背景
提案 §4.1 的三段路由：
- sim ≥ 0.93：近重复，直接跳过（不花钱不写库）；
- sim < 0.80：直接 ADD（没必要询问 LLM）；
- 0.80–0.93：**窄窗**才送 LLM 四分类。

## 决定（与理由）
1. 窗口宽度=LLM 的"器量"：只有"相似但不同"才可能改写既有事实；把
   LLM 调用压缩到窄窗，错判损失面与成本同降（面试点位）。
2. **fail-closed 强制（`coerce_decision`）**：UPDATE/DELETE 没写合法的
   邻居 id，或缺 UPDATE 内容 → 强制翻成 NOOP 并保留原 rationale 进
   skipped。LLM 幻觉出的 target 永远落不了库。
3. 全量决策流水进 `meta.decision` + `IngestReport`（含 provider 标签）。
4. 阈值是超参不是真理（`conflict_lo`/`dedup_near` 在 Settings，validator
   只锁 0<lo<near<=1）：敏感性实验属于 eval 范畴（§7、W1 验收）。

## 测试确定性注记
哈希 embedding 的相似度内容相关，单测里"必须走 LLM 路由"的用例用
`conflict_lo=0.001, dedup_near=0.999` 把路由钉死；默认阈值路由本身用
表驱动纯函数测试覆盖（sim=None/0.5/0.86/0.93/0.99 → ...）。
