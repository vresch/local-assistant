#!/usr/bin/env bash
#
# Start the Ollama server only if it is not already running.
#
# Ollama listens on http://127.0.0.1:11434 by default. We probe that endpoint
# rather than grepping the process list so the check works regardless of how the
# server was started (CLI, the macOS app, systemd, etc.).
#
# Usage:
#   scripts/start-ollama.sh            # start if needed, wait until ready
#   OLLAMA_HOST=127.0.0.1:11434 scripts/start-ollama.sh
#
# Exit status:
#   0  server is running (already up, or started successfully)
#   1  ollama is not installed
#   2  server did not become ready within the timeout

set -euo pipefail

HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
URL="http://${HOST}"
READY_TIMEOUT="${OLLAMA_READY_TIMEOUT:-30}"

is_up() {
  curl --silent --fail --max-time 2 "${URL}/api/version" >/dev/null 2>&1
}

if is_up; then
  echo "Ollama already running at ${URL}"
  exit 0
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Error: 'ollama' is not installed or not on PATH." >&2
  echo "Install it from https://ollama.com/download" >&2
  exit 1
fi

echo "Starting Ollama server..."
# Detach fully so the server keeps running after this script exits.
nohup ollama serve >/dev/null 2>&1 &

# Wait for the server to accept connections.
for ((i = 0; i < READY_TIMEOUT; i++)); do
  if is_up; then
    echo "Ollama is ready at ${URL}"
    exit 0
  fi
  sleep 1
done

echo "Error: Ollama did not become ready within ${READY_TIMEOUT}s." >&2
exit 2
