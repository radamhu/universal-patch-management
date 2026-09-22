universal-patch-management

Core Infra Components:

- proxmox version AS-IS + latest stable
- LXC OS version AS-IS + latest stable
- VM OS version AS-IS + latest stable
- backup status latest

Application Components:

* LXC APP version AS-IS + latest stable
* VM APP version AS-IS + latest stable
* VM docker APP version AS-IS + latest stable
* backup status latest

Grafana Dashboard

* universal-patch-management send logs to grafana cloud through alloy

## Running

1. `cp .env.example .env` and fill in only the `PVE01_*` values. Never reuse `.env.dev`; it holds unrelated secrets.
2. Place an SSH key at `secrets/id_ed25519` and `chmod 600` it. Create it BEFORE the first `docker compose up`, otherwise Docker creates a root-owned directory at that path.
3. Start: `UPM_UID=$(id -u) docker compose up --build -d`
4. Open http://127.0.0.1:8080. Set `BIND` to a LAN IP to expose it; the page has no auth.

Non-pve SSH hosts (OS + docker app + backup checks) are defined in `components.json` under `"hosts"`. Each host has:

- `id` (used as the host label and component id prefix), `user`, `address`
- `docker` (bool, whether to enumerate `docker ps` containers as app components)
- optional `backup_cmd`, a command on the host that prints the last backup time as epoch seconds

Probes are defined in `components.json` under `"probes"`. Each probe has:

- `id` (must start with `probe:`), `name`, `kind`, `group` (`core` or `app`), `host` (label)
- `target`: `{"type":"pve_lxc","vmid":N}` or `{"type":"ssh","host":"..","user":".."}`
- `cmd` and `regex` (group 1 = version)
- optional `latest_key`, matching a key under `"sources"`

Endpoints: `/status.json` (data) and `/healthz` (liveness).
