# litellm-proxy — local LLM serving on DGX Spark

An OpenAI- and Anthropic-compatible API, plus an admin MCP server, for local models running on an NVIDIA DGX Spark (GB10, 121 GB unified memory).

```
clients (LAN / Tailscale / loopback)
  └─► LiteLLM :4000  (Docker, API key required) — the only network-exposed service
        ├─► /v1/*  → llama-swap 127.0.0.1:8080 → llama-server (llama.cpp), loaded on demand
        └─► /mcp   → local-llm MCP 127.0.0.1:8090 (read-only tools)
```

- **LiteLLM** handles keys, model names, and both the OpenAI and Anthropic API formats.
- **llama-swap** starts a model on its first request and unloads it after 30 minutes idle.
- **llama.cpp** is built natively for sm_121.
- **`local_llm.py`** is an MCP server for admin tasks and for sending prompts to local models.

## Models

| Name | Notes |
|---|---|
| `gemma-4-26b-a4b` | Default. MoE, Q4_K_XL, vision. 75–100 tok/s with MTP speculative decoding. Thinks by default. |
| `qwen3.8-27b` | Dense, Q4_K_XL, vision. ~10 tok/s. |

A cold start (first request after the model unloads) takes about 30 s.

## Using the API

Base URL is `http://<host>:4000`. `<host>` is `LAN_HOST` or `TAILSCALE_HOST` from `local.env`. Auth is `Authorization: Bearer <LITELLM_MASTER_KEY>` (the key is in `.env`).

```bash
curl http://<host>:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"gemma-4-26b-a4b","messages":[{"role":"user","content":"Hi"}],
       "chat_template_kwargs":{"enable_thinking":false}}'
```

- **Gemma thinking:** Gemma thinks by default, so a small `max_tokens` can return empty content. To skip thinking, send `chat_template_kwargs: {enable_thinking: false}`.
- **OpenAI SDKs:** set `base_url="http://<host>:4000/v1"`.
- **Anthropic format:** use `POST /v1/messages`.

## MCP server (`local-llm`)

| Tool | Local (Claude Code, stdio) | Remote (`:4000/mcp/`) |
|---|---|---|
| `status`, `list_models`, `logs(service, lines)`, `ask_local(model, prompt, max_tokens, think)` | allowed | allowed |
| `load`, `unload`, `pull(hf_repo, file)`, `add_model(name, engine, path, args)`, `remove_model` | asks first | blocked |

- **Local:** `.mcp.json` registers the server for Claude Code sessions started in this folder.
- **Remote:** point any MCP client at `http://<host>:4000/mcp/` (streamable HTTP) with the same Bearer key. Tool names get a prefix, for example `local_llm-status`.
- **Changing what remote clients can call:** edit `allowed_tools` in `litellm/config.yaml`, then restart LiteLLM.

`add_model` and `remove_model` edit both configs and keep their comments. Each edit saves a `*.bak-<timestamp>` backup and replaces the file atomically. llama-swap reloads on its own; LiteLLM needs `docker restart litellm-proxy-litellm-1`.

Self-check: `uv run --script local_llm.py --check`

## Setup

1. Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), llama.cpp built with CUDA, `llama-swap` in `~/.local/bin/`.
2. Machine config: `cp local.env.example local.env` and edit it.

   | Variable | Used by | Meaning |
   |---|---|---|
   | `PROJECT_DIR` | systemd units | absolute path of this checkout |
   | `LLAMA_SERVER` | llama-swap (`${env.LLAMA_SERVER}`) | path to the `llama-server` binary |
   | `MODELS_DIR` | llama-swap, `local_llm.py` | directory holding model weights |
   | `LAN_HOST`, `TAILSCALE_HOST` | docs / operator reference | how clients reach the box |

3. Secret: `echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)" > .env && chmod 600 .env`
4. Services:
   ```bash
   mkdir -p ~/.config/local-llm && ln -sf "$PWD/local.env" ~/.config/local-llm/local.env
   ln -sf "$PWD"/systemd/*.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now llama-swap local-llm-mcp
   loginctl enable-linger "$USER"   # keep user services running without a login session
   docker compose up -d
   ```

## Files

| Path | Purpose |
|---|---|
| `docker-compose.yml` | LiteLLM container (pinned image, host network, :4000) |
| `litellm/config.yaml` | model routes + `mcp_servers` |
| `llama-swap/config.yaml` | model commands; reloaded automatically on save |
| `local_llm.py` | MCP server (uv inline deps) |
| `systemd/*.service` | `llama-swap`, `local-llm-mcp` user services (symlinked into `~/.config/systemd/user/`) |
| `.mcp.json`, `.claude/settings.json` | Claude Code MCP registration and tool permissions |
| `.env` | `LITELLM_MASTER_KEY` — chmod 600, gitignored |
| `local.env` | machine-specific paths and hostnames — gitignored; template `local.env.example` |
| `HANDOVER.md` | design decisions, benchmarks, next steps |

Model weights are in `$MODELS_DIR`. The llama-server binary is `$LLAMA_SERVER` (both set in `local.env`).

## Operations

```bash
docker compose up -d | restart                       # LiteLLM
systemctl --user {status,restart} llama-swap local-llm-mcp
journalctl --user -u llama-swap -f
curl 127.0.0.1:8080/running                          # loaded models
curl -X POST 127.0.0.1:8080/api/models/unload        # unload all
curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:4000/v1/models   # 401 = up, auth on
```

**Adding a model by hand:**
1. Add an entry under `models:` in `llama-swap/config.yaml`.
2. Add a matching `model_list` entry (`openai/<name>`, `api_base: http://127.0.0.1:8080/v1`) in `litellm/config.yaml`.
3. Restart LiteLLM.

The `add_model` tool does all three except the restart.

## Security

- Only :4000 is reachable from the network. llama-swap and the MCP backend listen on loopback only.
- Remote access is through Tailscale. There are no router port forwards and no Funnel.
- The LiteLLM image is pinned to a specific version, because a compromised LiteLLM release was published to PyPI in 2026. Upgrade deliberately.
