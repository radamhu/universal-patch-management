# Grafana Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose upm's status data as Prometheus metrics, ship them to Grafana Cloud via an Alloy sidecar, and provision a 1-page Grafana dashboard on top of them.

**Architecture:** New `/metrics` endpoint renders the existing status doc as Prometheus text (one gauge series per component, `status` as a label). A new `alloy` service in docker-compose scrapes it over the internal compose network and remote_writes to Grafana Cloud Mimir. A dashboard is provisioned directly into the `adaminfo` Grafana Cloud instance via the Grafana MCP tools. `/status.json`, `/healthz`, and the web UI are untouched.

**Tech Stack:** Python 3.12 stdlib (`http.server`, already in use), `grafana/alloy` Docker image, Grafana Cloud (Mimir + Grafana), pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-grafana-dashboard-design.md`

## Global Constraints

- Container `app` runs as non-root `upm`, filesystem `read_only: true` — new code must not write outside `/data` or `/tmp` (AGENTS.md).
- Don't hardcode paths in `src/upm/` — config comes from env vars (AGENTS.md).
- Run `pytest` after any change under `src/upm/`; new tests colocated by module name under `tests/` (AGENTS.md).
- Compose service serving upm is named `app` (not `upm`), listening on port `8080` — Alloy's scrape target is `app:8080`, not `upm:8080`.
- Mimir push endpoint: `https://prometheus-prod-24-prod-eu-west-2.grafana.net/api/prom/push`, basic-auth user `1551553`. Password is a Cloud Access Policy token (`metrics:write` scope) the user mints separately — never the `glsa_...` service-account token from `.env`.
- Grafana datasource to query from the dashboard: uid `grafanacloud-prom` (name `grafanacloud-adaminfo-prom`), type Prometheus/Mimir.

## Review Focus

- `store.load()` returns `None` (no poll has run yet) — `/metrics` must return an empty 200 body, not throw or 500.
- Status doc exists but `components` is `[]` (no hosts/probes configured) — must not crash, and the freshness metric should still emit.
- `generated_at` missing or malformed in the doc (mirrors the existing defensive handling in `/healthz`) — metrics render must not crash, just omit the freshness series.
- `current`/`latest` are `None` on a component (real case: brand-new component, no version parsed yet) — must render as empty label value, never the literal string `"None"`.
- Label values containing characters that break Prometheus exposition format (`"`, `\`, newline) — must be escaped, not passed through raw.

---

## Task 1: Prometheus metrics rendering

**Files:**
- Create: `src/upm/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `render_prometheus(doc: dict | None) -> bytes` — pure function, no IO. `doc` is the same shape `Store.load()` returns / `None`: `{"generated_at": str, "hosts": dict, "components": list[dict]}` where each component dict has keys `id, group, host, kind, name, current, latest, status, checked_at` (per `src/upm/models.py`'s `Component`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_metrics.py
from upm.metrics import render_prometheus


def _doc(components, generated_at="2026-09-28T12:00:00+00:00"):
    return {"generated_at": generated_at, "hosts": {}, "components": components}


def test_none_doc_renders_empty():
    assert render_prometheus(None) == b""


def test_no_components_still_emits_freshness():
    body = render_prometheus(_doc([]))
    assert b"upm_component_status{" not in body
    assert b"upm_generated_timestamp_seconds 1790596800.0" in body


def test_component_series_rendered():
    components = [
        {"id": "pve01:os", "group": "core", "host": "pve01", "kind": "os",
         "name": "Proxmox VE", "current": "8.3.1", "latest": "8.3.1",
         "status": "ok", "checked_at": "2026-09-28T12:00:00+00:00"},
        {"id": "pve01:lxc100:nginx", "group": "app", "host": "pve01", "kind": "docker",
         "name": "nginx", "current": "1.24", "latest": "1.27", "status": "outdated",
         "checked_at": "2026-09-28T12:00:00+00:00"},
    ]
    body = render_prometheus(_doc(components)).decode()
    assert ('upm_component_status{id="pve01:os",group="core",host="pve01",kind="os",'
            'name="Proxmox VE",current="8.3.1",latest="8.3.1",status="ok"} 1') in body
    assert ('upm_component_status{id="pve01:lxc100:nginx",group="app",host="pve01",'
            'kind="docker",name="nginx",current="1.24",latest="1.27",status="outdated"} 1') in body


def test_none_current_latest_render_as_empty_string():
    components = [{"id": "x:y", "group": "app", "host": "x", "kind": "docker",
                   "name": "y", "current": None, "latest": None, "status": "unknown",
                   "checked_at": "2026-09-28T12:00:00+00:00"}]
    body = render_prometheus(_doc(components)).decode()
    assert 'current="",latest=""' in body
    assert "None" not in body


def test_label_values_escaped():
    components = [{"id": "x:y", "group": "app", "host": "x", "kind": "docker",
                   "name": 'weird "name"\\with\\backslash', "current": "1.0",
                   "latest": "1.0", "status": "ok", "checked_at": "2026-09-28T12:00:00+00:00"}]
    body = render_prometheus(_doc(components)).decode()
    assert 'name="weird \\"name\\"\\\\with\\\\backslash"' in body


def test_malformed_generated_at_omits_freshness_series():
    body = render_prometheus(_doc([], generated_at="not-a-date"))
    assert b"upm_generated_timestamp_seconds" not in body
    body2 = render_prometheus({"hosts": {}, "components": []})
    assert b"upm_generated_timestamp_seconds" not in body2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'upm.metrics'`

- [ ] **Step 3: Write the implementation**

```python
# src/upm/metrics.py
from datetime import datetime

_ESCAPES = str.maketrans({"\\": "\\\\", "\"": "\\\"", "\n": "\\n"})

_LABEL_KEYS = ("id", "group", "host", "kind", "name", "current", "latest", "status")


def _label_value(v):
    return (v or "").translate(_ESCAPES)


def _labels(component):
    pairs = ",".join(f'{k}="{_label_value(component.get(k))}"' for k in _LABEL_KEYS)
    return pairs


def render_prometheus(doc):
    if not doc:
        return b""
    lines = [
        "# HELP upm_component_status Component status, one series per component (value always 1).",
        "# TYPE upm_component_status gauge",
    ]
    for c in doc.get("components") or []:
        lines.append(f"upm_component_status{{{_labels(c)}}} 1")

    try:
        ts = datetime.fromisoformat(doc["generated_at"]).timestamp()
    except (TypeError, KeyError, ValueError):
        ts = None
    if ts is not None:
        lines.append("# HELP upm_generated_timestamp_seconds Unix time the status doc was generated.")
        lines.append("# TYPE upm_generated_timestamp_seconds gauge")
        lines.append(f"upm_generated_timestamp_seconds {ts}")

    return ("\n".join(lines) + "\n").encode()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_metrics.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/upm/metrics.py tests/test_metrics.py
git commit -m "feat: render status doc as Prometheus metrics"
```

---

## Task 2: `/metrics` HTTP route

**Files:**
- Modify: `src/upm/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `render_prometheus(doc)` from Task 1 (`from .metrics import render_prometheus`).
- Produces: `GET /metrics` route on the server `make_server()` returns — no new public function, existing `make_server(store, static_dir, max_age_s, port, bind="0.0.0.0")` signature unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server.py` (uses the existing `srv` fixture already in that file):

```python
def test_metrics_no_data(srv):
    _, base = srv
    status, body = get(base + "/metrics")
    assert status == 200 and body == b""


def test_metrics_with_data(srv):
    store, base = srv
    store.save({"generated_at": "2026-09-28T12:00:00+00:00", "hosts": {},
                "components": [{"id": "x:y", "group": "app", "host": "x", "kind": "docker",
                                 "name": "y", "current": "1.0", "latest": "1.0",
                                 "status": "ok", "checked_at": "2026-09-28T12:00:00+00:00"}]})
    status, body = get(base + "/metrics")
    assert status == 200
    assert b'upm_component_status{id="x:y"' in body
    assert b"upm_generated_timestamp_seconds" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_server.py -v -k metrics`
Expected: FAIL — `/metrics` currently falls through to the `404` branch, so `status == 200` assertions fail.

- [ ] **Step 3: Implement the route**

In `src/upm/server.py`, add the import and route:

```python
from .metrics import render_prometheus
```

```python
            elif path == "/metrics":
                self._send(200, render_prometheus(store.load()),
                          "text/plain; version=0.0.4; charset=utf-8")
```

Place this `elif` branch alongside the existing `/status.json` / `/healthz` branches in `do_GET`, before the final `else: self._json(404, ...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_server.py -v`
Expected: PASS (all tests in the file, including the two new ones)

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/upm/server.py tests/test_server.py
git commit -m "feat: add /metrics endpoint serving Prometheus text format"
```

---

## Task 3: Alloy sidecar wired into docker-compose

**Files:**
- Create: `alloy/config.alloy`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `GET /metrics` from Task 2, reachable inside the compose network at `app:8080/metrics` (compose service name is `app`, per `docker-compose.yml`).
- Produces: a running `alloy` container that remote_writes `upm_component_status` / `upm_generated_timestamp_seconds` series into Grafana Cloud Mimir — this is what Task 4's dashboard queries against.

- [ ] **Step 1: Write the Alloy config**

```river
// alloy/config.alloy
prometheus.scrape "upm" {
  targets = [{"__address__" = "app:8080"}]
  metrics_path    = "/metrics"
  scrape_interval = "60s"
  forward_to      = [prometheus.remote_write.grafana_cloud.receiver]
}

prometheus.remote_write "grafana_cloud" {
  endpoint {
    url = env("GRAFANA_PROM_PUSH_URL")
    basic_auth {
      username = env("GRAFANA_PROM_USER")
      password = env("GRAFANA_PROM_TOKEN")
    }
  }
}
```

- [ ] **Step 2: Add the `alloy` service to `docker-compose.yml`**

Add a new top-level service alongside the existing `app` service (do not touch `app`'s existing config):

```yaml
  alloy:
    image: grafana/alloy:latest
    restart: unless-stopped
    read_only: true
    tmpfs:
      - /tmp
      - /var/lib/alloy/data
    env_file: .env
    volumes:
      - ./alloy/config.alloy:/etc/alloy/config.alloy:ro
    depends_on:
      - app
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
        reservations:
          cpus: "0.1"
          memory: 64M
```

No `ports:` and no `traefik.*` labels — this container is internal-only, it pushes out, nothing scrapes it.

- [ ] **Step 3: Add the new env vars to `.env.example`**

Append:

```
# --- Grafana Cloud metrics (Alloy remote_write) ---
# Push endpoint for your Grafana Cloud Mimir/Prometheus instance (Grafana Cloud -> your stack ->
# "Send Metrics" -> Prometheus, use the url ending in /api/prom/push).
GRAFANA_PROM_PUSH_URL=
# Basic auth user = the numeric instance ID shown on that same page.
GRAFANA_PROM_USER=
# Basic auth password: a Grafana Cloud Access Policy token scoped "metrics:write".
# NOT the same as a Grafana service-account token (glsa_...) — that one only works
# against the Grafana HTTP API, not the Mimir push endpoint.
GRAFANA_PROM_TOKEN=
```

- [ ] **Step 4: Fill in real values in `.env`**

Not scripted — the executor (or the user) sets `GRAFANA_PROM_PUSH_URL=https://prometheus-prod-24-prod-eu-west-2.grafana.net/api/prom/push`, `GRAFANA_PROM_USER=1551553`, and a freshly minted `GRAFANA_PROM_TOKEN` in the real `.env` (not `.env.example`).

- [ ] **Step 5: Bring the stack up and verify the scrape target locally**

Run: `UPM_UID=$(id -u) docker compose up --build -d`
Then: `docker compose exec app wget -qO- http://localhost:8080/metrics | head -5`
Expected: Prometheus text output, e.g. lines starting `# HELP upm_component_status ...` and (once the poll loop has run once) `upm_component_status{...} 1` series.

- [ ] **Step 6: Verify Alloy is scraping and pushing**

Run: `docker compose logs alloy --tail=50`
Expected: no `remote_write` or `scrape` error lines; a healthy Alloy log shows periodic scrape activity, no auth failures (401s would show `server returned HTTP status 401 Unauthorized` — if seen, `GRAFANA_PROM_TOKEN` is wrong/expired).

- [ ] **Step 7: Prune build artifacts (per AGENTS.md)**

```bash
docker image prune -f --filter label=com.docker.compose.project=universal-patch-management
docker volume prune -f --filter label=com.docker.compose.project=universal-patch-management
```

- [ ] **Step 8: Commit**

```bash
git add alloy/config.alloy docker-compose.yml .env.example
git commit -m "feat: add Alloy sidecar shipping status metrics to Grafana Cloud"
```

---

## Task 4: Grafana dashboard

**Files:** none in this repo — provisioned directly into the `adaminfo` Grafana Cloud instance via MCP tools. (No local dashboard JSON file per the design spec; exporting one into `docs/` afterward is explicitly out of scope for this plan.)

**Interfaces:**
- Consumes: `upm_component_status{id,group,host,kind,name,current,latest,status}` and `upm_generated_timestamp_seconds` series in datasource uid `grafanacloud-prom`, populated by Task 3.

- [ ] **Step 1: Create the dashboard**

Call `mcp__grafana__update_dashboard` with no `uid` (create mode) and this `dashboard` JSON:

```json
{
  "title": "UPM Patch Status",
  "uid": "upm-patch-status",
  "tags": ["upm"],
  "timezone": "browser",
  "schemaVersion": 39,
  "refresh": "1m",
  "time": {"from": "now-6h", "to": "now"},
  "panels": [
    {
      "id": 1, "title": "OK", "type": "stat",
      "gridPos": {"x": 0, "y": 0, "w": 4, "h": 4},
      "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
      "targets": [{"expr": "count(upm_component_status{status=\"ok\"}) or vector(0)", "refId": "A", "instant": true}],
      "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"}}, "overrides": []}
    },
    {
      "id": 2, "title": "Outdated", "type": "stat",
      "gridPos": {"x": 4, "y": 0, "w": 4, "h": 4},
      "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
      "targets": [{"expr": "count(upm_component_status{status=\"outdated\"}) or vector(0)", "refId": "A", "instant": true}],
      "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "orange"}}, "overrides": []}
    },
    {
      "id": 3, "title": "Unknown", "type": "stat",
      "gridPos": {"x": 8, "y": 0, "w": 4, "h": 4},
      "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
      "targets": [{"expr": "count(upm_component_status{status=\"unknown\"}) or vector(0)", "refId": "A", "instant": true}],
      "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "gray"}}, "overrides": []}
    },
    {
      "id": 4, "title": "Error", "type": "stat",
      "gridPos": {"x": 12, "y": 0, "w": 4, "h": 4},
      "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
      "targets": [{"expr": "count(upm_component_status{status=\"error\"}) or vector(0)", "refId": "A", "instant": true}],
      "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "red"}}, "overrides": []}
    },
    {
      "id": 5, "title": "Data age", "type": "stat",
      "gridPos": {"x": 16, "y": 0, "w": 8, "h": 4},
      "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
      "targets": [{"expr": "time() - upm_generated_timestamp_seconds", "refId": "A", "instant": true}],
      "fieldConfig": {"defaults": {"unit": "s", "thresholds": {"mode": "absolute", "steps": [
        {"color": "green", "value": null}, {"color": "red", "value": 5400}
      ]}}, "overrides": []}
    },
    {
      "id": 6, "title": "Components", "type": "table",
      "gridPos": {"x": 0, "y": 4, "w": 24, "h": 16},
      "datasource": {"type": "prometheus", "uid": "grafanacloud-prom"},
      "targets": [{"expr": "upm_component_status", "refId": "A", "instant": true, "format": "table"}],
      "transformations": [
        {"id": "labelsToFields", "options": {}},
        {"id": "organize", "options": {"excludeByName": {"Time": true, "__name__": true, "Value": true}}}
      ],
      "fieldConfig": {
        "defaults": {},
        "overrides": [
          {"matcher": {"id": "byName", "options": "status"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background"}},
            {"id": "mappings", "value": [{"type": "value", "options": {
              "ok": {"color": "green", "index": 0},
              "outdated": {"color": "orange", "index": 1},
              "unknown": {"color": "gray", "index": 2},
              "error": {"color": "red", "index": 3}
            }}]}
          ]}
        ]
      }
    }
  ]
}
```

- [ ] **Step 2: Verify the dashboard was created**

Call `mcp__grafana__get_dashboard_by_uid` with `uid: "upm-patch-status"`.
Expected: returns the dashboard with 6 panels and `meta.url` set — note the URL for the user.

- [ ] **Step 3: Verify panels return data**

Once Task 3's Alloy container has been running for at least one scrape+push cycle (a couple minutes), call `mcp__grafana__query_prometheus` against datasource uid `grafanacloud-prom` with query `upm_component_status` (instant).
Expected: non-empty result set, one series per real component from the running stack. If empty: check Task 3 Step 6 (Alloy logs) before touching the dashboard again — this is a data-pipeline problem, not a dashboard problem.

- [ ] **Step 4: Confirm in the UI**

Report the dashboard URL (from Step 2's `meta.url`, prefixed with the Grafana Cloud base URL) to the user for a visual check. No file to commit for this task.
