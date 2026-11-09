CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_embeddings (
  memory_id uuid PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
  embedding vector(512) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_mem_emb_hnsw
  ON memory_embeddings USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
