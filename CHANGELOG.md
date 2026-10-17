# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
