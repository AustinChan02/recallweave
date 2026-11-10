# Eval summary — run c7d7ddc3f2b2
- date: `2026-11-10` (UTC)
- dataset: `data/datasets/converted/locomo10-t30-seed2026.jsonl`
- protocol: `qa-accuracy (real provider, LLM-judged)`
- judge: `openai_compat:deepseek-v4-pro`
- answered_by: `openai_compat:deepseek-flash`
- budget: `600` tokens
- cost guard: spent-upper-est `0.745084¥` / cap `200.0¥` (prices: `configs/prices.toml (UPPER-BOUND placeholders)`)
- token columns: answer/judge are PROVIDER-reported; context is a local estimate.

| condition | accuracy | judged/total | unjudged |
|---|---|---|---|
| C0 | 0.25 | 30/30 | 0 | ctx~18304 | ans 3363 | judge 14323 | 0.433976¥ upper-est |
| C3 | 0.2 | 30/30 | 0 | ctx~3243 | ans 2884 | judge 12754 | 0.311108¥ upper-est |

> accuracy 语义: qa-accuracy（真实 provider 回答 + 真实 LLM judge；须伴 30 题人工复核后才能进简历）。
> `cost_yuan`（控制台实价）/ `ttft_ms` / `cache_hit_tokens` 保持 null（见各 result 文件 uncomputed 段），绝不虚构.

*generated 2026-11-10T05:42:40.226635+00:00 — derived from the raw
write-once files in this directory; raw files are never edited.*
