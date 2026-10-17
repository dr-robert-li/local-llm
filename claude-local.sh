#!/usr/bin/env bash
# Run Claude Code against a local model through LiteLLM's Anthropic-format endpoint.
# Usage: ./claude-local.sh [claude args...]
# Env: LOCAL_MODEL (default gemma-4-26b-a4b), LOCAL_LLM_URL (default http://127.0.0.1:4000)
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
M=${LOCAL_MODEL:-gemma-4-26b-a4b}
unset ANTHROPIC_CUSTOM_HEADERS ANTHROPIC_AUTH_TOKEN  # do not inherit another gateway setup
export ANTHROPIC_BASE_URL=${LOCAL_LLM_URL:-http://127.0.0.1:4000}
export ANTHROPIC_API_KEY=$(grep '^LITELLM_MASTER_KEY=' "$DIR/.env" | cut -d= -f2-)
export ANTHROPIC_MODEL=$M ANTHROPIC_DEFAULT_OPUS_MODEL=$M ANTHROPIC_DEFAULT_SONNET_MODEL=$M ANTHROPIC_DEFAULT_HAIKU_MODEL=$M
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=${CLAUDE_CODE_MAX_CONTEXT_TOKENS:-65536}  # llama-swap -c
exec claude "$@"
