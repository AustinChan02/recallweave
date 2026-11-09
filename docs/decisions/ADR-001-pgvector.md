# ADR-001 — pgvector 单库，而非独立向量数据库

日期：2026-11-09 ｜ 状态：已实现（本仓默认路径 posture）

## 背景（proposal §3 要点）
个人自托管部署要"一个容器"的简单性；分层后又有零依赖开发路径需求。

## 决定
1. 生产/演示主路径：Postgres 16 + pgvector，`memory_embeddings` 表存
   `vector(512)`，HNSW（m=16, ef_construction=64, cosine ops）。
2. embedding 不进 ORM 模型：放在独立表、独立 SQL（写路径原子性由同一事务
   内 upsert 承担）。理由：ORM 与 pgvector 扩展耦合会让 SQLite 后端彻底没法复用。
   （提案 §3 的 `memories.embedding vector(768)` 内嵌设计因此修订，见下"偏差"。）
3. SQLite 是一等公民的 dev/demo 后端：JSON blob 向量 + Python brute-force
   cosine；`memory_embeddings` 由 `ensure_schema` 建裸表。**上限语义**：向
   量召回候选来源于该 user 的 active 行（扫描级 O(n)），只配笔记本地场景。
4. 运行时 `hnsw.ef_search` 在每次查询事务内 `SET LOCAL`（proposal §4.2
   "ef_search 可配"），不污染连接池。

## 实现偏差登记（提案修订）
- vector(768) → **vector(512)**：BGE-small-zh-v1.5 实际 512 维；提案当时写错。
- embedding 列内嵌 → 独立表 + sqlite JSON（跨后端复用所迫）。

## 不做什么 / 权衡
- 不引入 Qdrant/Milvus：运维面+采样延迟收益在 <1M 向量时不抵一库两用。
- 不做分库分表：迁移路径（user_id 分片 → 独立 ANN 服务）写在提案 §12 L4。
- SQLite 路径性能数字**不是**生产数字：bench 输出的范围标签明确写明。
