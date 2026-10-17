#!/usr/bin/env bash
# Benchmark a GGUF with llama-server: 3 fixed prompts, temp 0, 512 tokens, thinking off.
# Usage: ./bench.sh <model.gguf> [extra llama-server args...]
# Unload llama-swap models first (curl -X POST 127.0.0.1:8080/api/models/unload) to free memory.
set -euo pipefail
cd "$(dirname "$0")"; set -a; . ./local.env; set +a
PORT=5999; MODEL=$1; shift
"$LLAMA_SERVER" --host 127.0.0.1 --port $PORT -ngl 999 -fa on --jinja -c 8192 -m "$MODEL" "$@" >/tmp/bench-server.log 2>&1 &
PID=$!; trap 'kill $PID 2>/dev/null; wait $PID 2>/dev/null' EXIT
until curl -sf 127.0.0.1:$PORT/health >/dev/null; do kill -0 $PID || { tail -5 /tmp/bench-server.log; exit 1; }; sleep 1; done
for p in "Write a Python function that parses an ISO 8601 date string without using datetime.fromisoformat, with tests." \
         "Explain how TCP congestion control works, covering slow start, congestion avoidance and fast recovery." \
         "Write a short story about a lighthouse keeper who discovers a message in a bottle."; do
  curl -s 127.0.0.1:$PORT/v1/chat/completions -H 'Content-Type: application/json' -d "$(jq -n --arg p "$p" \
    '{messages:[{role:"user",content:$p}],max_tokens:512,temperature:0,chat_template_kwargs:{enable_thinking:false}}')" \
  | jq -r '.timings | "gen \(.predicted_per_second|floor) tok/s  prompt \(.prompt_per_second|floor) tok/s  draft \(.draft_n // "-")/\(.draft_n_accepted // "-") accepted"'
done
