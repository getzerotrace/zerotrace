#!/usr/bin/env bash
set -euo pipefail

MODEL="${ZEROTRACE_MODEL:-qwen2.5-coder:3b-instruct-q4_K_M}"

ollama serve &
SERVER_PID=$!

echo "zerotrace: waiting for ollama server..."
until ollama list > /dev/null 2>&1; do
  sleep 1
done

if ! ollama list | grep -q "${MODEL%%:*}"; then
  echo "zerotrace: pulling ${MODEL} (first run only, cached in the ollama-data volume)"
  ollama pull "${MODEL}"
else
  echo "zerotrace: ${MODEL} already present"
fi

wait "$SERVER_PID"
