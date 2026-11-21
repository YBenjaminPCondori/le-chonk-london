#!/usr/bin/env bash
# Run ChonkVid locally + Cloudflare quick tunnel + update Worker BACKEND_URL proxy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CF="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${CHONKVID_PORT:-8810}"

if ! curl -sf "http://127.0.0.1:${PORT}/api/status" >/dev/null 2>&1; then
  echo "Starting ChonkVid on 127.0.0.1:${PORT} ..."
  (cd "$ROOT" && .venv/bin/python -m chonkvid serve --host 127.0.0.1 --port "$PORT") &
  sleep 2
fi

LOG="$(mktemp)"
cloudflared tunnel --url "http://127.0.0.1:${PORT}" 2>&1 | tee "$LOG" &
TUN_PID=$!
trap 'kill $TUN_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [[ -n "$URL" ]] && break
  sleep 1
done
if [[ -z "$URL" ]]; then
  echo "Tunnel URL not found in cloudflared output." >&2
  exit 1
fi

echo "$URL" | npx wrangler --cwd "$CF" secret put BACKEND_URL
npx wrangler --cwd "$CF" deploy

echo ""
echo "Public (Worker proxy): https://chonkvid-app.kiritokris.workers.dev"
echo "Tunnel origin:         $URL"
echo "Keep this terminal open while judges use the demo."