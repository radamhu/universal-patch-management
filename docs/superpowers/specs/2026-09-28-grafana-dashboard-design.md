# Grafana dashboard: design

## Goal

Feed upm's existing status data into Grafana Cloud so it's visible as a 1-page dashboard, without
disturbing the existing `/status.json` / `/healthz` / web UI path.

## Context

- upm already computes a list of `Component` records (id, group, host, kind, name, current, latest,
  status, checked_at) and serves them as `/status.json`; `AGENTS.md` describes this as feeding Grafana
  "via Alloy," but no Alloy config, `/metrics` endpoint, or dashboard exists in the repo yet.
- Target stack: Grafana Cloud, instance `adaminfo` (`https://adaminfo.grafana.net/`). Prometheus/Mimir
  datasource `grafanacloud-adaminfo-prom` (uid `grafanacloud-prom`), push endpoint
  `https://prometheus-prod-24-prod-eu-west-2.grafana.net/api/prom/push`, basic-auth user `1551553`.
- The `GRAFANA_SERVICE_ACCOUNT_TOKEN` (`glsa_...`) already in `.env` authenticates the Grafana HTTP API
  (used here to provision the dashboard via MCP) — it does **not** work as the Mimir remote_write basic-auth
  password. That needs a separate Grafana Cloud Access Policy token scoped `metrics:write`, minted by the
  user outside this repo.
- Alloy runs inside the app's own docker-compose stack (sidecar container), converting metrics on-site
  rather than scraping from outside the network.

## Data flow

```
upm (poll loop) -> store -> /status.json (unchanged)
                          -> /metrics (new, Prometheus text format)
                                   |
                                   v
                        Alloy container (same compose network)
                          prometheus.scrape  ->  prometheus.remote_write
                                   |
                                   v
                     Grafana Cloud Mimir (grafanacloud-adaminfo-prom)
                                   |
                                   v
                          Grafana dashboard (1 page)
```

`/status.json`, `/healthz`, and the existing web UI are untouched. `/metrics` is additive.

## Metric schema

Implemented in a new `src/upm/metrics.py`, called from `server.py`'s `/metrics` route.

- `upm_component_status{id,group,host,kind,name,current,latest,status} 1`
  One series per component per scrape. `status` (ok|outdated|unknown|error) and the other component
  fields are labels; the value is always `1`. This lets Grafana do `count by (status)(...)` for status
  counts and a "labels to fields" table transform for the full component table, without needing a
  numeric status encoding.
- `upm_generated_timestamp_seconds <epoch>`
  Backs a freshness/staleness stat panel, using the same `max_age_s` threshold `/healthz` already uses.

Tradeoff: `current`/`latest` as label values means label cardinality grows with version string
diversity. Acceptable at this fleet's size (dozens of components); not addressed further.

## Components / files touched

- `src/upm/metrics.py` (new) — `render_prometheus(doc: dict) -> bytes`, pure function over the same doc
  shape `store.load()` returns. Unit-testable with a fixture doc, no network/IO.
- `src/upm/server.py` — add `GET /metrics` route returning `render_prometheus(store.load())`, content
  type `text/plain; version=0.0.4; charset=utf-8`. Mirrors `/status.json`'s empty-doc handling (empty body
  when `store.load()` is `None`, HTTP 200 — Alloy sees zero series that scrape, no special-casing needed).
- `alloy/config.alloy` (new) — `prometheus.scrape` component targeting `upm:8080` on the internal compose
  network (not Traefik-routed, not published), `prometheus.remote_write` with basic auth read from env.
- `docker-compose.yml` — new `alloy` service (`grafana/alloy` image), same network as `upm`, no published
  port, config file mounted, credentials injected via env vars.
- `.env.example` — add `GRAFANA_PROM_PUSH_URL`, `GRAFANA_PROM_USER`, `GRAFANA_PROM_TOKEN` with a comment
  that the token must be a Cloud Access Policy token (`metrics:write`), not the `glsa_...` service account
  token.
- `tests/test_metrics.py` (new) — mirrors `src/upm/metrics.py` per AGENTS.md's test-layout convention;
  reuses fixture patterns from `tests/fakes.py`.
- Grafana dashboard — provisioned directly into the `adaminfo` Grafana Cloud instance via the Grafana MCP
  tools (source of truth lives in Grafana Cloud, not as a file in this repo). JSON can be exported into
  `docs/` afterward for versioning if wanted, as a follow-up, not part of this plan.

## Dashboard (1 page)

- Top row: 4 stat panels, `count by (status)(upm_component_status)` filtered per status value —
  ok (green) / outdated (yellow) / unknown (grey) / error (red).
- Freshness stat: `time() - upm_generated_timestamp_seconds`, colored red above `max_age_s`.
- Main panel: table, instant query on `upm_component_status`, "labels to fields" transform producing
  columns id/group/host/kind/name/current/latest/status/checked_at. Cell coloring on the `status` column.
  Grafana's built-in table search/sort covers filtering by group/host without needing separate panels.

## Error handling

- `/metrics` returns an empty body (200) when there's no data yet, same posture as `/status.json`
  returning 503 — Alloy's scrape just yields nothing that cycle, no crash, no special-casing in Alloy
  config.
- Alloy container failing (bad token, network issue) does not affect `/status.json`, `/healthz`, or the
  existing web UI — fully decoupled via the sidecar boundary.

## Testing

- `pytest tests/test_metrics.py` — `render_prometheus` against a fixture doc (component list covering
  each status value, plus an empty-doc case).
- Manual: `docker compose up --build -d`, `curl localhost:8080/metrics` (or `docker exec` if unpublished),
  confirm well-formed Prometheus text; then `mcp__grafana__query_prometheus` against
  `grafanacloud-adaminfo-prom` to confirm series arrived; then screenshot the dashboard.

## Out of scope

- Historical/long-term retention tuning in Mimir (default Grafana Cloud retention applies).
- Alerting rules on top of these metrics (could be a follow-up, not part of this dashboard).
- Exporting/versioning the dashboard JSON into the repo (noted above as an optional follow-up).
