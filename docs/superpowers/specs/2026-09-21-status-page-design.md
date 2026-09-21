# Universal Patch Management – Status Page Design

Date: 2026-09-21

## Goal

Lightweight Docker app that shows a frontend status page of component versions (AS-IS vs latest stable) and backup status, for the components listed in `readme.md`. Data is collected from the pve01 Proxmox host (host, LXC, VM) and the Oracle Cloud prod host, and stored as JSON.

## Decisions

- Single container: collector + static page in one process (Python, python:3-alpine, ~70MB).
- Latest-stable versions from public sources per component, mapped in `components.json`.
- Oracle host read over SSH (read-only key) from the container.
- Page access: none, LAN-bind only. No secrets are shown on the page.
- Playwright: dev/CI-only, separate container, never in the runtime image.

## Architecture

- **Web**: FastAPI (or stdlib `http.server`). Serves static `index.html` and `GET /status.json`.
- **Scheduler**: asyncio loop every `POLL_INTERVAL` minutes (default 30). Collectors run in parallel, each with its own timeout and error capture.
- **Storage**: `/data/status.json` on a mounted volume, written atomically (tmp file + rename). `/data/history.json` optional later.

### Collectors

Common interface: `collect() -> list[Component]`.

| Collector | Source | Yields |
|---|---|---|
| `pve01` | PVE API token: `/version`, `/nodes/*/lxc`, `/qemu`, storage backups, vzdump tasks | Proxmox version, LXC OS, VM info, last backup |
| `oracle` | SSH read-only: `docker ps --format`, `/etc/os-release`, backup log/timestamp | Docker app versions, VM OS, backup |

### Latest-stable resolver

`components.json` maps each component to a source: `endoflife.date`, `github_release`, `dockerhub_tag`, or `manual`. Results are cached for 6h to avoid rate limits.

### Component record

```json
{"id":"pve01:proxmox","group":"core|app","host":"pve01",
 "kind":"proxmox|lxc_os|lxc_app|vm_os|vm_app|docker_app|backup",
 "name":"...","current":"8.2.4","latest":"8.3.1",
 "status":"ok|outdated|unknown|error","checked_at":"ISO"}
```

`status.json` = `{generated_at, hosts:{pve01:{ok,error}, oracle:{ok,error}}, components:[...]}`.

A failed host keeps its last-good data, marked stale.

## Frontend

Single static HTML + vanilla JS, no build step. Fetches `/status.json` and renders two groups from the readme:

- **Core Infra**: Proxmox, LXC OS, VM app, backup status.
- **Application**: LXC app, VM OS, VM docker app, backup status.

Each row: component, current, latest, status badge. Backup rows show age. Header shows per-host health and `generated_at`. Auto-refresh every 60s.

## Config and ops

- Env only: `PVE01_HOST`, `PVE01_HOST_USER`, `PVE01_HOST_TOKEN`, `PVE01_HOST_TOKEN_SECRET`, `ORACLE_SSH_HOST`, `ORACLE_SSH_USER`, `POLL_INTERVAL`.
- SSH key mounted `:ro`; `components.json` mounted for mappings.
- `Dockerfile` + `compose.yaml`: `/data` volume, read-only root fs, non-root user, healthcheck on `status.json` age.
- `.env*` in `.dockerignore`. `.env.dev` holds live secrets (PVE, Odoo, Grafana): keep out of image and VCS.

## Testing

Unit tests for parsers and version comparison against recorded fixtures (no live hosts). Playwright E2E added later in a separate container.

## Out of scope (v1)

- Grafana Cloud log shipping via Alloy (readme item, v2)
- Playwright tests
- History charts
- Auth
