# Running the Backstage portal as a service

The portal runs in **production serve** mode: the backend (via the `app-backend`
plugin) serves the pre-built frontend AND the API on a **single port (:7007)**.

Do **not** run `yarn start` (the rspack dev server) for a deployment — it crashes
at runtime with `TypeError: Cannot redefine property: default` from
`@material-ui/icons` (a known rspack ↔ MUI v4 interop bug). The production build
concatenates modules and does not hit it. This folder has a `systemd` unit for
the production serve.

## Prerequisites (on the host)

- **node 22 or 24** — NOT 25/20. Backstage 1.49.x requires 22||24, and its native
  deps (`isolated-vm`, `better-sqlite3`) have no prebuild outside that range.
- `yarn install` run once in `backstage-portal/` on that host (compiles native
  modules for its OS/ABI). After any node-major change, `npm rebuild`.

## Install

```bash
# 1. Build the app with the deploy's URL baked in (single port :7007).
#    Only the app needs building; the backend runs from source.
cd backstage-portal
export APP_BASE_URL=http://<host>:7007 BACKEND_BASE_URL=http://<host>:7007
node .yarn/releases/yarn-4.4.1.cjs workspace app build     # ~1-3 min (rspack, prod)

# 2. Env: host URLs + BaselithCore API key
sudo mkdir -p /etc/baselith
sudo cp deploy/backstage-portal.env.example /etc/baselith/backstage-portal.env
sudo nano /etc/baselith/backstage-portal.env      # APP_BASE_URL/BACKEND_BASE_URL = http://<host>:7007
sudo chmod 600 /etc/baselith/backstage-portal.env # contains the API key

# 3. Unit — edit User=, WorkingDirectory=, and the node@22 PATH inside it
sudo cp deploy/backstage-portal.service /etc/systemd/system/
sudo nano /etc/systemd/system/backstage-portal.service

# 4. Enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now backstage-portal.service
journalctl -u backstage-portal.service -f          # expect "Serving static app
                                                   # content" + "Discovered N plugins"

# 5. Firewall (single port)
sudo ufw allow 7007/tcp
```

The portal is then reachable at `http://<host>:7007` (UI + API, same origin —
no separate :3010, no CORS). Rebuild the app (step 1) whenever the app source or
`APP_BASE_URL` changes, then `systemctl restart backstage-portal.service`.

### Plain-HTTP deploys — CSP note

`app-config.yaml` sets `backend.csp.upgrade-insecure-requests: false`. Helmet
enables that directive by default, which makes the browser fetch every
subresource over HTTPS; on a plain-HTTP host that yields `ERR_SSL_PROTOCOL_ERROR`
and a blank page. Keep it `false` unless the portal is served over real HTTPS
(localhost is exempt, so the bug only shows on a non-localhost HTTP host).

### BaselithCore integration

Set `BASELITH_BASE_URL` / `BASELITH_API_KEY` so the `BaselithCoreEntityProvider`
can sync plugins into the catalog. The key must be listed in BaselithCore's
`API_KEYS_JOB` (or `API_KEYS_ADMIN`); adding a key requires a BaselithCore
restart.

## Docker (alternative)

For a container deploy, `yarn build:all` then build `packages/backend/Dockerfile`
(`yarn build-image`). Publish :7007 and pass the same env. Production mode with
Postgres uses `app-config.production.yaml`.
