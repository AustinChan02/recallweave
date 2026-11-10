-- Migration 002 — P1.a session summaries (proposal §4.4).
-- Dialect-agnostic tables are also created by ORM create_all; this file makes
-- the pg path EXPLICIT and identical (single config path rule, session.py
-- executes every migrations/*.sql in name order on postgres).

CREATE TABLE IF NOT EXISTS session_turn_stats (
  user_id uuid NOT NULL,
  session_id uuid NOT NULL,
  turns integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, session_id)
);

CREATE TABLE IF NOT EXISTS session_summaries (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL,
  session_id uuid NOT NULL,
  seq integer NOT NULL,
  turns_covered integer NOT NULL DEFAULT 0,
  content text NOT NULL,
  token_est integer NOT NULL DEFAULT 0,
  provider varchar(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_ssum_session_seq
  ON session_summaries (session_id, seq);
CREATE INDEX IF NOT EXISTS ix_ssum_user_created
  ON session_summaries (user_id, created_at);
