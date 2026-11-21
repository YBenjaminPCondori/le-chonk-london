# ChonkVid on Cloudflare

## Live URL (demo)

**https://chonkvid-app.kiritokris.workers.dev**

A Workers proxy forwards to the FastAPI app via **Cloudflare Tunnel**. The Mac must keep **uvicorn** and **cloudflared** running during the demo.

## One-command demo (quick tunnel)

```bash
./cloudflare/scripts/deploy-tunnel.sh
```

## Manual

```bash
.venv/bin/python -m chonkvid serve --host 127.0.0.1 --port 8810
cloudflared tunnel --url http://127.0.0.1:8810
# Copy the https://….trycloudflare.com URL, then:
cd cloudflare
printf '%s' 'https://YOUR.trycloudflare.com' | npx wrangler secret put BACKEND_URL
npx wrangler deploy
```

## Full edge deploy (FastAPI in a container)

`Dockerfile` + `wrangler.toml` containers config are in this repo. **`wrangler deploy` with containers needs a Workers Paid plan** on the account (API error 401 otherwise). After upgrading:

1. Restore the container-based `wrangler.toml` and `src/index.ts` from git history (or use the `ChonkVidApp` container worker).
2. `npx wrangler secret put MISTRAL_API_KEY` (and optional `DATABASE_URL`).
3. `npx wrangler deploy --containers-rollout=immediate` (Docker must be running).

## Secrets

| Secret | Purpose |
|--------|---------|
| `BACKEND_URL` | Tunnel or origin URL for the proxy Worker (`chonkvid-app`) |
| `MISTRAL_API_KEY` | Only needed for container deploy (not stored on the proxy Worker today) |