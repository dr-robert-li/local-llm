# Handover — local LLM serving stack (DGX Spark)

Last updated: 2026-10-17. Read this first when resuming.

## Machine
- NVIDIA DGX Spark: GB10 (compute 12.1), aarch64, ~121 GB unified CPU/GPU memory, CUDA 13.0 at /usr/local/cuda.
- Network addresses (LAN, Tailscale): see `local.env` (gitignored).
- Operator user in `docker` group (added this session; Claude's shell uses `sg docker -c "…"` until re-login). Linger enabled.

## Architecture (decided)
```
clients (LAN / Tailscale / loopback)
  └─► LiteLLM :4000  0.0.0.0, Docker, keys required   — the ONLY network-exposed service
        ├─► /mcp → local-llm MCP 127.0.0.1:8090/mcp (systemd user svc, read-only tools only via allowed_tools)
        └─► llama-swap 127.0.0.1:8080, native systemd user service, no auth
              ├─► llama-server (llama.cpp, native build)  — DEFAULT engine
              └─► vLLM (spark-vllm-docker)                 — LATER, add as llama-swap entry, exclusive
```
Decisions and why:
- LiteLLM = gateway (keys, names, OpenAI + Anthropic API formats). Pinned image `ghcr.io/berriai/litellm:v1.105.1` (had a compromised PyPI release in 2026 — pin, bump deliberately).
- llama-swap = load/unload on demand; ttl 1800 s. Native binary v265 at ~/.local/bin/llama-swap (no arm64 CUDA image existed).
- llama.cpp built natively for sm_121 (prebuilt images not trusted for GB10).
- vLLM only when there is real concurrent load. Unified memory: vLLM reserves `--gpu-memory-utilization` × 121 GB up front, so put it in a llama-swap group that unloads everything else. vLLM cold start = minutes → long healthCheckTimeout (already 600).
- No Postgres yet (add when per-client keys / spend tracking / LiteLLM admin UI needed).
- Web UIs: no custom UI for now. Planned off-the-shelf: Open WebUI (chat) + LiteLLM /ui (needs Postgres) + llama-swap /ui (127.0.0.1:8080/ui) + DGX Dashboard.
- Remote access via Tailscale only; no router port forwards / Funnel.

## Files (this folder)
| Path | Purpose |
|---|---|
| `docker-compose.yml` | LiteLLM, `network_mode: host`, port 4000 |
| `litellm/config.yaml` | routes `gemma-4-26b-a4b`, `qwen3.8-27b` → `openai/<name>` at 127.0.0.1:8080/v1 |
| `llama-swap/config.yaml` | model definitions; `--watch-config` → edits reload live |
| `systemd/llama-swap.service` | symlinked into ~/.config/systemd/user/, enabled |
| `systemd/local-llm-mcp.service` | `local_llm.py --http` on 127.0.0.1:8090/mcp; symlinked + enabled. LiteLLM fronts it at `:4000/mcp` (`mcp_servers` in litellm/config.yaml) |
| `local_llm.py` | `local-llm` MCP server (stdio, uv inline deps, `mcp<2` pinned). Logic = plain functions; `serve()` wraps them. Self-check: `uv run --script local_llm.py --check` |
| `.mcp.json`, `.claude/settings.json` | registers `local-llm`; read-only tools allowlisted, mutating ones ask |
| `.env` | `LITELLM_MASTER_KEY` (chmod 600, gitignored). A hook blocks Claude from reading it — test keyed calls via `docker exec litellm-proxy-litellm-1 python …` using the container's env. |

Elsewhere: llama.cpp build dir holds `llama-server` (= `$LLAMA_SERVER`) and `llama-bench`; models in `$MODELS_DIR`. Machine-specific values live in `local.env`.

## Models (measured on this box)
| Model | File | Gen tok/s | Prompt tok/s | Notes |
|---|---|---|---|---|
| **gemma-4-26b-a4b** (default) | unsloth UD-Q4_K_XL 17 GB + mmproj-F16 + `mtp-gemma-4-26B-A4B-it.gguf` | 50 plain / **75–100 with MTP**; 77 end-to-end via LiteLLM | ~2770 | MoE. `--spec-type draft-mtp`. DFlash draft also downloaded (64–99 tok/s, worse than MTP). Thinks by default — small `max_tokens` → empty content; clients can send `chat_template_kwargs: {enable_thinking: false}`. |
| qwen3.8-27b | unsloth UD-Q4_K_XL 17.6 GB + mmproj-F16 | 9.6 | ~750 | Dense, slow. Kept as an option. |

Benchmark script: scratchpad `specbench.sh` (session-temporary; recreate if needed: llama-server on :5999, 3 fixed prompts, temp 0, 512 tok, read `timings` from the response).

## Common commands
```bash
systemctl --user {status,restart} llama-swap
journalctl --user -u llama-swap -f
docker compose up -d / restart        # from this folder (after re-login; else sg docker -c)
curl 127.0.0.1:8080/running           # what's loaded
curl 127.0.0.1:8080/unload            # unload all
curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:4000/v1/models   # 401 = auth works
```

## Next steps
1. ~~`local-llm` MCP server~~ DONE 2026-10-17. Tools: `status`, `list_models`, `logs`, `ask_local` (allowlisted); `load`, `unload`, `pull`, `add_model`, `remove_model` (ask). Config edits: ruamel round-trip (comments kept), timestamped `.bak-*` backup, atomic replace; llama-swap reloads automatically (verified). LiteLLM needs `docker restart litellm-proxy-litellm-1` after add/remove (atomic replace also requires restart for the file bind mount). `ask_local` goes straight to llama-swap (no key). Restart Claude Code in this folder to pick up the server. External: `http://<host>:4000/mcp/` with `Authorization: Bearer <litellm key>`; tools appear as `local_llm-<tool>`; only status/list_models/logs/ask_local allowed (mutating ones return 'not allowed' — verified). Edit `allowed_tools` + restart LiteLLM to change.
2. Optional: tune MTP `--spec-draft-n-max` (default 3); benchmark Gemma Q8_0 (27 GB) for quality vs speed.
3. Optional: decide thinking default for Gemma (server-side off vs per-request).
4. Later: Open WebUI; Postgres + LiteLLM UI; vLLM entry in llama-swap; `ANTHROPIC_BASE_URL=http://127.0.0.1:4000` to run Claude Code on local models.
