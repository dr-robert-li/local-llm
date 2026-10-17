# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-10-17

### Added
- Operator portal (Caddy, `:8000`) linking every UI, with basic-auth proxies for the loopback-only llama-swap UI (`:8001`) and DGX Dashboard (`:8002`).
- Open WebUI (`v0.11.4`, `:3000`) wired to LiteLLM.
- Postgres (`17.6`, loopback) for LiteLLM virtual keys, spend tracking and the admin UI at `:4000/ui`.
- `gemma-4-26b-a4b-vllm`: vLLM (NGC `vllm:26.08-py3`) with NVFP4 weights as a llama-swap entry, for concurrent load.
- `gemma-4-26b-a4b-q8`: Q8_0 + MTP variant.
- `gemma-4-26b-a4b-think`: LiteLLM alias with thinking enabled.
- `claude-local.sh`: run Claude Code on local models through LiteLLM's Anthropic endpoint.
- `bench.sh`: llama-server throughput benchmark.

### Changed
- Gemma thinking is off by default (server-side); enable per request or via the `-think` alias.
- Each compose service receives only the secrets it needs (no shared `env_file`).

### Notes
- MTP `--spec-draft-n-max` sweep (1–6): default 3 is fastest (avg 79 tok/s), so unchanged.

## [0.1.0] - 2026-10-17

### Added
- LiteLLM gateway (`ghcr.io/berriai/litellm:v1.105.1`, pinned) on `0.0.0.0:4000` with master-key auth; OpenAI and Anthropic API formats.
- llama-swap (v265, native systemd user service, `127.0.0.1:8080`) for on-demand model loading with 30-minute idle unload and live config reload.
- llama.cpp `llama-server` built natively for GB10 (sm_121).
- Models: `gemma-4-26b-a4b` (default; MTP speculative decoding, 75–100 tok/s) and `qwen3.8-27b`.
- `local-llm` MCP server (`local_llm.py`): `status`, `list_models`, `logs`, `ask_local`, `load`, `unload`, `pull`, `add_model`, `remove_model`. Comment-preserving config edits with timestamped backups and atomic writes. Built-in `--check` self-test.
- Local MCP access over stdio for Claude Code (`.mcp.json`; read-only tools allowlisted, mutating tools require approval).
- Remote MCP access at `:4000/mcp/` through LiteLLM's MCP gateway (key required, read-only tools only); backend on `127.0.0.1:8090` as the `local-llm-mcp` systemd user service.
- Machine-specific paths and hostnames in gitignored `local.env` (template `local.env.example`), read by llama-swap, systemd units and `local_llm.py`.
- README and HANDOVER documentation.
