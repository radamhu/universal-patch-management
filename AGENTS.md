# AGENTS.md

## Project

universal-patch-management (upm): a Python service that polls Proxmox/LXC/VM/Docker hosts over SSH, checks
installed vs latest-stable versions, and serves a status dashboard (`/status.json`, `/healthz`) that feeds
a Grafana dashboard via Alloy.

## Layout

- `src/upm/` — application code (`main.py`, `server.py`, `runner.py`, `ssh.py`, `versions.py`, `latest.py`,
  `store.py`, `models.py`, `config.py`)
- `tests/` — pytest suite, mirrors `src/upm/` module names (`fakes.py` holds shared test doubles)
- `components.json` — declares SSH `hosts` and version-check `probes`; see readme.md for the schema
- `Dockerfile`, `compose.yaml` — container build/run
- `secrets/id_ed25519` — SSH key mounted into the container; must exist with `chmod 600` before first build

## Running

```
cp .env.example .env   # fill in PVE01_* only; never reuse .env.dev
UPM_UID=$(id -u) docker compose up --build -d
```

## Testing

```
pip install -r requirements-dev.txt
pytest
```

Run `pytest` after any change under `src/upm/`; keep new tests colocated by module name under `tests/`.

## Docker

**Always prune after `docker build` (or `docker compose build` / `docker compose up --build`)** to reclaim
disk space from dangling images/layers/volumes left behind by the build — scoped to this project only, never
a blanket `docker system prune`:

```
docker image prune -f --filter label=com.docker.compose.project=universal-patch-management
docker volume prune -f --filter label=com.docker.compose.project=universal-patch-management
```

## Conventions

- Container runs as non-root user `upm` (`UPM_UID` build arg), filesystem is `read_only: true` — don't add
  code that writes outside `/data` or `/tmp`.
- Config path (`COMPONENTS_PATH`) and data dir (`DATA_DIR`) are set via env vars in the Dockerfile; don't
  hardcode paths in `src/upm/`.
