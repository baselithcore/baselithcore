#!/usr/bin/env bash
set -euo pipefail

# Smoke test rapido per backend FastAPI: health e chat dummy.
# Usage: API_BASE_URL=http://localhost:8181 ./tests/smoke_chat.sh

API_BASE_URL="${API_BASE_URL:-http://localhost:8181}"

echo ">> Ping /status su ${API_BASE_URL}"
curl -fsSL "${API_BASE_URL}/status" >/dev/null

echo ">> Smoke /chat (prompt dummy)"
curl -fsSL -X POST \
  -H "Content-Type: application/json" \
  -d '{"query":"Smoke test: rispondi OK","conversation_id":"smoke-chat-1"}' \
  "${API_BASE_URL}/chat" >/dev/null

echo "✅ Smoke test completato"
