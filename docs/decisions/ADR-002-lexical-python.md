# ADR-002 — 中文检索词法通道用 Python 实现，而不是 tsvector

日期：2026-11-09 ｜ 状态：已实现（提案修订案）

## 背景
提案 §4.2 第②路写的是 "PostgreSQL 全文（tsvector + ts_rank）"。实现时发现：
1. 中文 tsvector 需要额外 parser（zhparser/pg_jieba），默认镜像装不上 =
   docker-compose"一键起库"承诺破产；
2. ts_rank 与 BM25 之间的取舍本来就是提案里承认的近似；
3. 查重门也要分词——若检索与查重各用一个分词器，排序语义会漂移
   （分词器漂移是这类系统的经典暗坑）。

## 决定
词法通道在 Python 内实现（`read/retrievers.py`）：共享
`adapters/embeddings.tokenize_for_lexical`（latin 词 + CJK 二元组），
`lexical_scores = |Q∩D| / sqrt(|Q|·|D|)`（简化 BM25 近似，O(扫描)）。
扫描上限：`lexical_scan_limit=2000`（配置）。SQLite/PG 行为完全一致，
后端无感。

## 代价与升级路径
- O(n_users×n_mems) 扫描在大租户下必须进 db 层（zhparser 或 ParadeDB
  pg_search/BM25，或 SQLite FTS5）——P2，记入提案 §6 备选；
- 现在的算分只保证"关键词型查询能命中"，不承诺排序质量。诚实标注。
