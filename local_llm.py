#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=1.2,<2", "ruamel.yaml>=0.18"]
# ///
"""Admin + delegation for the local LLM stack (llama-swap + LiteLLM).

Plain functions hold the logic; `serve()` wraps them as a stdio MCP server.
  uv run --script local_llm.py           # MCP server (stdio)
  uv run --script local_llm.py --http    # MCP server, streamable HTTP on 127.0.0.1:8090/mcp
  uv run --script local_llm.py --check   # self-check
"""
import json, os, re, shlex, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

HERE = Path(__file__).resolve().parent
_env = HERE / "local.env"  # machine-specific settings (gitignored); see local.env.example
for line in _env.read_text().splitlines() if _env.exists() else []:
    k, sep, v = line.partition("=")
    if sep and not k.lstrip().startswith("#"):
        os.environ.setdefault(k.strip(), v.strip())
SWAP_CFG = HERE / "llama-swap/config.yaml"
LITELLM_CFG = HERE / "litellm/config.yaml"
MODELS_DIR = Path(os.environ.get("MODELS_DIR", Path.home() / "models"))
SWAP = "http://127.0.0.1:8080"
LITELLM = "http://127.0.0.1:4000"
CONTAINER = "litellm-proxy-litellm-1"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ENGINES = {"llama-server"}  # ponytail: vllm entries hand-written until one exists

yaml = YAML()  # round-trip: keeps comments and | blocks
yaml.width = 4096
yaml.indent(mapping=2, sequence=4, offset=2)  # matches the hand-written files


def _http(path, body=None, method=None, timeout=30, base=SWAP):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except OSError as e:
        return 0, str(e)


def _run(cmd, docker=False):
    # shell may predate docker group membership; sg picks it up without re-login
    if docker and "docker" not in subprocess.run(["id", "-nG"], capture_output=True, text=True).stdout.split():
        cmd = ["sg", "docker", "-c", shlex.join(cmd)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return (p.stdout + p.stderr).strip()


def _model(name):
    if not NAME_RE.match(name or ""):
        raise ValueError(f"bad model name {name!r}")
    return name


# ---- read-only ----

def status():
    """Health of llama-swap service, loaded models, LiteLLM container/HTTP (401 = up with auth)."""
    code, body = _http("/running")
    running = [m.get("model") for m in json.loads(body).get("running", [])] if code == 200 else f"llama-swap down: {body}"
    return {
        "llama_swap_service": _run(["systemctl", "--user", "is-active", "llama-swap"]),
        "running_models": running,
        "litellm_container": _run(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER], docker=True),
        "litellm_http": _http("/v1/models", base=LITELLM, timeout=5)[0],  # 401 = up, auth on
    }


def list_models():
    """Models llama-swap knows, with load status and context size."""
    code, body = _http("/v1/models")
    if code != 200:
        raise RuntimeError(f"llama-swap {code}: {body}")
    return [{"id": m["id"], "status": m.get("status", {}).get("value"),
             "context": m.get("meta", {}).get("n_ctx")} for m in json.loads(body)["data"]]


def logs(service="llama-swap", lines=100):
    """Tail logs. service: 'llama-swap' or 'litellm'."""
    lines = max(1, min(int(lines), 2000))
    if service == "llama-swap":
        return _run(["journalctl", "--user", "-u", "llama-swap", "-n", str(lines), "--no-pager"])
    if service == "litellm":
        return _run(["docker", "logs", "--tail", str(lines), CONTAINER], docker=True)
    raise ValueError("service must be 'llama-swap' or 'litellm'")


def ask_local(model, prompt, max_tokens=1024, think=False):
    """One-shot prompt to a local model (loads it if needed). think=True enables reasoning."""
    body = {"model": _model(model), "max_tokens": int(max_tokens),
            "messages": [{"role": "user", "content": prompt}],
            "chat_template_kwargs": {"enable_thinking": bool(think)}}
    code, resp = _http("/v1/chat/completions", body, timeout=900)  # cold load can take minutes
    if code != 200:
        raise RuntimeError(f"{code}: {resp[:500]}")
    r = json.loads(resp)
    return {"content": r["choices"][0]["message"].get("content"),
            "finish_reason": r["choices"][0].get("finish_reason"),
            "usage": r.get("usage"), "tok_per_s": r.get("timings", {}).get("predicted_per_second")}


# ---- mutating ----

def load(model):
    """Load a model now (blocks until healthy)."""
    code, body = _http(f"/upstream/{_model(model)}/health", timeout=900)
    return {"model": model, "http": code, "body": body[:200]}


def unload(model=None):
    """Unload one model, or all if model is omitted."""
    path = f"/api/models/unload/{_model(model)}" if model else "/api/models/unload"
    return {"http": _http(path, method="POST")[0], "running": status()["running_models"]}


def pull(hf_repo, file):
    """Download a file from Hugging Face into $MODELS_DIR/<repo-name>/."""
    if not re.match(r"^[\w.-]+/[\w.-]+$", hf_repo) or ".." in file or file.startswith("/"):
        raise ValueError("bad repo or file")
    dest = MODELS_DIR / hf_repo.split("/")[1]
    p = subprocess.run(["hf", "download", hf_repo, file, "--local-dir", str(dest)],
                       capture_output=True, text=True, timeout=6 * 3600)
    if p.returncode:
        raise RuntimeError(p.stderr[-1000:])
    return {"path": str(dest / file)}


def _edit(path, fn):
    """Backup, apply fn(data), write atomically."""
    data = yaml.load(path.read_text())
    fn(data)
    shutil.copy2(path, path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}.{time.time_ns() // 10**6 % 1000:03d}"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        yaml.dump(data, f)
    os.chmod(tmp, path.stat().st_mode)
    os.replace(tmp, path)


def add_model(name, engine, path, args="", ttl=1800):
    """Add a llama-server model to llama-swap + LiteLLM configs (backed up). path: .gguf under $MODELS_DIR; args: extra flags, one line."""
    _model(name)
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {sorted(ENGINES)}")
    gguf = Path(path).resolve()
    if not gguf.is_relative_to(MODELS_DIR) or not gguf.is_file():
        raise ValueError(f"{gguf} is not a file under {MODELS_DIR}")
    if "\n" in args or "${" in args:
        raise ValueError("args must be one line, no macros")
    cmd = f"${{llama-server}}\n-m ${{models}}/{gguf.relative_to(MODELS_DIR)}\n" + (f"{args}\n" if args else "")

    def swap(d):
        if name in d["models"]:
            raise ValueError(f"{name} already exists")
        d["models"][name] = {"cmd": LiteralScalarString(cmd), "ttl": int(ttl)}

    def lite(d):
        d["model_list"] = [m for m in d["model_list"] if m["model_name"] != name]
        d["model_list"].append({"model_name": name, "litellm_params": {
            "model": f"openai/{name}", "api_base": f"{SWAP}/v1", "api_key": "none"}})

    _edit(SWAP_CFG, swap)  # llama-swap reloads itself (--watch-config)
    _edit(LITELLM_CFG, lite)
    return {"added": name, "note": f"restart LiteLLM to expose it: docker restart {CONTAINER}"}


def remove_model(name):
    """Remove a model from llama-swap + LiteLLM configs (backed up)."""
    _model(name)

    def swap(d):
        if name not in d["models"]:
            raise ValueError(f"{name} not in llama-swap config")
        del d["models"][name]

    _edit(SWAP_CFG, swap)
    _edit(LITELLM_CFG, lambda d: d.__setitem__(
        "model_list", [m for m in d["model_list"] if m["model_name"] != name]))
    return {"removed": name, "note": f"restart LiteLLM to drop it: docker restart {CONTAINER}"}


# ---- MCP wrapper ----

def serve(http=False):
    from mcp.server.fastmcp import FastMCP
    # loopback only; LiteLLM :4000/mcp is the authenticated network front door
    mcp = FastMCP("local-llm", host="127.0.0.1", port=8090)
    for fn in (status, list_models, logs, ask_local, load, unload, pull, add_model, remove_model):
        mcp.tool()(fn)
    mcp.run("streamable-http" if http else "stdio")


def check():
    """Config edits on temp copies, then read-only live calls."""
    global SWAP_CFG, LITELLM_CFG
    real = SWAP_CFG
    with tempfile.TemporaryDirectory() as t:
        SWAP_CFG, LITELLM_CFG = (Path(shutil.copy(p, t + "/" + p.parent.name + ".yaml"))
                                 for p in (SWAP_CFG, LITELLM_CFG))
        before = SWAP_CFG.read_text(), LITELLM_CFG.read_text()
        gguf = next(MODELS_DIR.rglob("*.gguf"))
        add_model("selfcheck-x", "llama-server", str(gguf), "-c 4096")
        assert "selfcheck-x" in yaml.load(SWAP_CFG.read_text())["models"]
        assert "# default. MTP" in SWAP_CFG.read_text()  # comments survive
        assert any(m["model_name"] == "selfcheck-x" for m in yaml.load(LITELLM_CFG.read_text())["model_list"])
        for bad in [("Bad Name", "llama-server", str(gguf)), ("x", "vllm", str(gguf)),
                    ("x", "llama-server", "/etc/passwd"), ("selfcheck-x", "llama-server", str(gguf))]:
            try:
                add_model(*bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass
        remove_model("selfcheck-x")
        assert (SWAP_CFG.read_text(), LITELLM_CFG.read_text()) == before, "add+remove must round-trip"
        assert len(list(Path(t).glob("*.bak-*"))) == 4
    SWAP_CFG, LITELLM_CFG = real, real.parent.parent / "litellm/config.yaml"
    print("config edits ok")
    print(json.dumps(status(), indent=1))
    print(json.dumps(list_models(), indent=1))
    print(logs("llama-swap", 3))


if __name__ == "__main__":
    check() if "--check" in sys.argv else serve(http="--http" in sys.argv)
