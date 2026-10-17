# litellm-proxy — local LLM serving on DGX Spark

An OpenAI- and Anthropic-compatible API, plus an admin MCP server, for local models running on an NVIDIA DGX Spark (GB10, 121 GB unified memory).

```
clients (LAN / Tailscale / loopback)
  ├─► Portal :8000 (Caddy, basic auth) — links to every UI below
  │     ├─► :8001 → llama-swap UI   (loopback upstream, basic auth)
  │     └─► :8002 → DGX Dashboard   (loopback upstream, basic auth)
  ├─► Open WebUI :3000 (own accounts) ──┐
  └─► LiteLLM :4000 (API key; admin UI at /ui, keys/spend in Postgres 127.0.0.1:5432)
        ├─► /v1/*  → llama-swap 127.0.0.1:8080 → llama-server (llama.cpp) or vLLM (Docker), loaded on demand
        └─► /mcp   → local-llm MCP 127.0.0.1:8090 (read-only tools)
```

- **LiteLLM** handles keys, model names, and both the OpenAI and Anthropic API formats.
- **llama-swap** starts a model on its first request and unloads it after 30 minutes idle.
- **llama.cpp** is built natively for sm_121.
- **`local_llm.py`** is an MCP server for admin tasks and for sending prompts to local models.

## Models

| Name | Notes |
|---|---|
| `gemma-4-26b-a4b` | Default. MoE, Q4_K_XL, vision. 65–93 tok/s with MTP speculative decoding. Thinking **off** by default. |
| `gemma-4-26b-a4b-think` | Same model and process, thinking on (LiteLLM alias). |
| `gemma-4-26b-a4b-q8` | Q8_0 (27 GB) + MTP: higher precision, ~25% slower (avg 59 vs 79 tok/s). |
| `qwen3.8-27b` | Dense, Q4_K_XL, vision. ~10 tok/s. |
| `gemma-4-26b-a4b-vllm` | vLLM (NGC `vllm:26.08-py3`, Docker) + NVFP4 weights. For concurrent load: 8 parallel requests → 252 tok/s aggregate (llama.cpp 149); single request 43 tok/s. Cold start ~5 min; reserves ~60 GB while loaded. |

llama-swap runs one model at a time: requesting another model unloads the current one. A cold start takes ~30 s for llama.cpp models and ~5 min for vLLM.

## Using the API

Base URL is `http://<host>:4000`. `<host>` is `LAN_HOST` or `TAILSCALE_HOST` from `local.env`. Auth is `Authorization: Bearer <LITELLM_MASTER_KEY>` (the key is in `.env`).

```bash
curl http://<host>:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"gemma-4-26b-a4b","messages":[{"role":"user","content":"Hi"}],
      }'
```

- **Gemma thinking:** off by default (a simple question takes ~10 tokens instead of ~280). Turn it on per request with `chat_template_kwargs: {enable_thinking: true}`, or use the `gemma-4-26b-a4b-think` model name.
- **OpenAI SDKs:** set `base_url="http://<host>:4000/v1"`.
- **Anthropic format:** use `POST /v1/messages`.

## Web UIs

Open the portal at `http://<host>:8000` (login `PORTAL_USER` / `PORTAL_PASSWORD` from `.env`). It links to:

| UI | URL | Login |
|---|---|---|
| Open WebUI (chat) | `:3000` | own accounts; the first sign-up becomes admin |
| LiteLLM admin (keys, spend, models) | `:4000/ui` | `admin` / `LITELLM_MASTER_KEY` |
| llama-swap (loaded models, logs) | `:8001/ui` | portal login |
| DGX Dashboard (GPU / system) | `:8002` | portal login |

Links are built from the address you opened the portal on, so they work on the LAN and over Tailscale.

## Claude Code on local models

```bash
./claude-local.sh                       # interactive, gemma-4-26b-a4b
LOCAL_MODEL=qwen3.8-27b ./claude-local.sh -p "…"
```

The script points `ANTHROPIC_BASE_URL` at LiteLLM, maps every model tier to the local model, and sets the context window to 64k tokens (`CLAUDE_CODE_MAX_CONTEXT_TOKENS`). Tool use works. Expect it to be much weaker than Claude on hard tasks.

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

1. Prerequisites: Docker with the NVIDIA runtime, [uv](https://docs.astral.sh/uv/), llama.cpp built with CUDA, `llama-swap` in `~/.local/bin/`, `jq` (for `bench.sh`).
2. Machine config: `cp local.env.example local.env` and edit it.

   | Variable | Used by | Meaning |
   |---|---|---|
   | `PROJECT_DIR` | systemd units | absolute path of this checkout |
   | `LLAMA_SERVER` | llama-swap (`${env.LLAMA_SERVER}`) | path to the `llama-server` binary |
   | `MODELS_DIR` | llama-swap, `local_llm.py` | directory holding model weights |
   | `LAN_HOST`, `TAILSCALE_HOST` | docs / operator reference | how clients reach the box |

3. Secrets in `.env` (chmod 600, gitignored), read by docker compose:

   | Variable | Used by |
   |---|---|
   | `LITELLM_MASTER_KEY` | LiteLLM auth (`sk-…`); also Open WebUI's upstream key and the LiteLLM UI password |
   | `POSTGRES_PASSWORD` | Postgres + LiteLLM `DATABASE_URL` |
   | `PORTAL_USER`, `PORTAL_PASSWORD` | portal / proxied-UI login |
   | `PORTAL_PASSWORD_HASH` | base64 of the bcrypt hash of `PORTAL_PASSWORD` (Caddy `basic_auth`) |

   ```bash
   pw=$(openssl rand -base64 18)
   { echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)"
     echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
     echo "PORTAL_USER=admin"; echo "PORTAL_PASSWORD=$pw"
     echo "PORTAL_PASSWORD_HASH=$(docker run --rm caddy:2.11.4-alpine caddy hash-password -p "$pw" | base64 -w0)"
   } > .env && chmod 600 .env
   ```
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
| `docker-compose.yml` | LiteLLM, Postgres, Open WebUI, portal (all images pinned) |
| `portal/` | portal page (`www/index.html`) and Caddy config |
| `claude-local.sh` | run Claude Code on a local model |
| `bench.sh` | llama-server benchmark (3 prompts, temp 0, 512 tokens) |
| `litellm/config.yaml` | model routes + `mcp_servers` |
| `llama-swap/config.yaml` | model commands; reloaded automatically on save |
| `local_llm.py` | MCP server (uv inline deps) |
| `systemd/*.service` | `llama-swap`, `local-llm-mcp` user services (symlinked into `~/.config/systemd/user/`) |
| `.mcp.json`, `.claude/settings.json` | Claude Code MCP registration and tool permissions |
| `.env` | secrets (see Setup) — chmod 600, gitignored |
| `local.env` | machine-specific paths and hostnames — gitignored; template `local.env.example` |
| `HANDOVER.md` | design decisions, benchmarks, next steps |

Model weights are in `$MODELS_DIR`. The llama-server binary is `$LLAMA_SERVER` (both set in `local.env`).

## Benchmarking

```bash
curl -X POST 127.0.0.1:8080/api/models/unload     # free memory first
./bench.sh $MODELS_DIR/<dir>/<model>.gguf [llama-server args…]
```

Runs 3 fixed prompts (temp 0, 512 tokens, thinking off) on a throwaway llama-server at :5999 and prints generation/prompt tok/s and draft acceptance.

## Operations

```bash
docker compose up -d | restart                       # LiteLLM, Postgres, Open WebUI, portal
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

- Network-reachable: LiteLLM :4000 (API key), Open WebUI :3000 (own accounts), portal :8000–8002 (basic auth). llama-swap, the MCP backend, Postgres and the DGX Dashboard listen on loopback only; the portal proxies the two UIs behind its login.
- Images are pinned to exact versions.
- Remote access is through Tailscale. There are no router port forwards and no Funnel.
- The LiteLLM image is pinned to a specific version, because a compromised LiteLLM release was published to PyPI in 2026. Upgrade deliberately.
