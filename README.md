# RecallWeave — Memory layer + context compiler for long-lived agents

> **占位名**：开 GitHub 仓库前先做查重（GitHub/PyPI/搜索引擎，见 `../agent-proposals/README.md` §0）。
> 定位：Agent 的记忆层（Memory as a Service）+ 上下文编译器（Context Compiler），评测驱动、数字可复现。
> 本仓库是按 `../agent-proposals/A-RecallWeave/PROPOSAL.md` 实现的实现仓库；proposal 存放于上层目录。

## Quickstart

前置：uv（含托管 Python 3.12）。

```sh
uv venv --python 3.12 && uv sync --extra dev
uv run pytest                     # 单测 + e2e（SQLite 路径，全离线）
make eval-smoke                   # 评测管线冒烟（mock judge，合成 fixture）
```

Postgres(pgvector) 主路径：

```sh
make db-up                        # docker compose 起 pgvector/pg16
export RECALLWEAVE_DB_URL=postgresql+asyncpg://recallweave:recallweave@localhost:5432/recallweave
uv run pytest -m integration
```

REST 服务：

```sh
make run                          # http://127.0.0.1:8100/health
python sdk_demo.py                # SDK 演示（mock 模式、离线）
```

## 离线运行的诚实性说明

- 开发/测试默认 `LLM_PROVIDER=mock` + `EMBEDDING_PROVIDER=hash`（确定性哈希嵌入，**仅测试用**）。
- 每个响应都带 `provider` 字段标明数据由哪种模式产生；mock 产生的内容**不得**作为"实测数字"写进简历或 README 表格。
- 切换真实供应商：`.env` 里设 `RECALLWEAVE_LLM_PROVIDER=openai_compat` + key（DeepSeek/Qwen/GLM 均为 OpenAI 兼容协议）。

## 结构

```
src/recallweave/   core/ adapters/ db/ write/ read/ service/ api/ sdk/ tokens.py
migrations/        001_init_vector.sql (pgvector + HNSW)
evals/  bench/  tests/ docs/
```

- License: MIT
- 架构/接口/ADR/devlog：见 `docs/`（devlog 保留全部试错过程，不删）。
