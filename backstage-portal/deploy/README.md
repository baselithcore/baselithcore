# Running the Backstage portal as a service

`yarn start` runs a **development** server — fine for an internal portal, but it
must be supervised so it restarts on crash and survives reboots. This folder has
a `systemd` unit for that. (For a containerized stack, see *Docker* below.)

## Prerequisites (on the host)

- **node 22 or 24** — NOT 25. Backstage 1.49.x refuses node 25, and its native
  deps (`isolated-vm`, `better-sqlite3`) have no prebuild for it.
- `yarn install` run once in `backstage-portal/` on that host, so the native
  modules are compiled for its OS/ABI. If you ever change node major, re-run
  `npm rebuild` (or `yarn install`) to recompile them.

## Install (systemd)

```bash
# 1. Env: host URLs + BaselithCore API key
sudo mkdir -p /etc/baselith
sudo cp deploy/backstage-portal.env.example /etc/baselith/backstage-portal.env
sudo nano /etc/baselith/backstage-portal.env      # set APP_BASE_URL etc.
sudo chmod 600 /etc/baselith/backstage-portal.env # contains the API key

# 2. Unit — edit User=, WorkingDirectory=, and the node@22 PATH inside it
sudo cp deploy/backstage-portal.service /etc/systemd/system/
sudo nano /etc/systemd/system/backstage-portal.service

# 3. Enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now backstage-portal.service
sudo systemctl status backstage-portal.service
journalctl -u backstage-portal.service -f          # watch boot; expect
                                                   # "Discovered N plugins"

# 4. Firewall
sudo ufw allow 3010/tcp && sudo ufw allow 7007/tcp
```

The portal is then reachable at `http://<host>:3010` (frontend) with its API on
`:7007`. Set `BASELITH_BASE_URL` / `BASELITH_API_KEY` so the
`BaselithCoreEntityProvider` can sync your plugins into the catalog.

## Docker (alternative)

A container image pins node 22 and builds the native deps inside the image, so
the host needs no node at all. Backstage ships a backend Dockerfile pattern
(`yarn build:all` → `packages/backend/Dockerfile`). Add a compose service that
publishes `3010`/`7007` and passes the same `APP_BASE_URL` / `BACKEND_BASE_URL`
/ `BASELITH_BASE_URL` / `BASELITH_API_KEY` env. Production mode also needs
Postgres (`app-config.production.yaml`).

## Production hardening (optional)

For a single-port, no-dev-server deploy: `yarn build:all`, then run
`node packages/backend --config app-config.yaml --config app-config.production.yaml`.
The backend serves the built UI and the API on `:7007` (set `APP_BASE_URL` =
`BACKEND_BASE_URL` = `http://<host>:7007`), which also sidesteps the `:3000`
family entirely. Requires the `POSTGRES_*` env from `app-config.production.yaml`.
