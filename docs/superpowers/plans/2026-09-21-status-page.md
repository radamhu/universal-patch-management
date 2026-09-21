# Universal Patch Management Status Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lightweight Docker app that collects component versions (current vs latest stable) and backup status from pve01 (Proxmox API, LXC, VM) and the Oracle prod host, stores them as JSON, and shows them on a status page.

**Architecture:** One Python process. A scheduler thread runs collectors (`pve01` via PVE API, `oracle` via SSH, `probe` via config-driven SSH commands), resolves "latest stable" from public sources, merges with last-good data, and atomically writes `/data/status.json`. A stdlib HTTP server in the main thread serves a static single-file page plus `/status.json` and `/healthz`.

**Tech Stack:** Python 3.12, httpx (only runtime dep), system `ssh` (openssh-client), stdlib `http.server`, pytest, Docker (python:3.12-alpine).

**Spec:** `docs/superpowers/specs/2026-09-21-status-page-design.md`

## Global Constraints

- Single container, python:3-alpine base, ~70MB target. Only runtime Python dep: `httpx`.
- Page access: none, LAN-bind only. No secrets shown on the page or in `status.json`.
- Playwright is dev/CI-only, separate container, never in the runtime image (not part of this plan).
- Storage: `/data/status.json` written atomically (tmp file + rename).
- Config is env only, plus a mounted `components.json`. Env names: `PVE01_HOST`, `PVE01_HOST_USER`, `PVE01_HOST_TOKEN`, `PVE01_HOST_TOKEN_SECRET`, `ORACLE_SSH_HOST`, `ORACLE_SSH_USER`, `POLL_INTERVAL` (minutes, default 30).
- Do NOT read `SSH_HOST` / `SSH_USER` from `.env.dev`; those belong to the Odoo host.
- Latest-stable cache TTL: 6h. A failed host keeps last-good data marked stale.
- `.env*` must be in `.dockerignore` and `.gitignore`. `.env.dev` holds live secrets: never copy it into the image or print its values.
- Out of scope (v1): Grafana/Alloy log shipping, Playwright tests, history charts, auth.

**Spec amendment (flag to user):** The spec listed LXC/VM inner versions under the `pve01` collector, but the PVE API does not expose in-guest OS/app versions. This plan adds a config-driven `probe` collector (SSH commands defined in `components.json`; LXC via `pct exec` on the PVE host). The `pve01` collector yields Proxmox version and backups only.

---

## File Structure

```
pyproject.toml              pytest config (pythonpath)
requirements.txt            httpx
requirements-dev.txt        + pytest
src/upm/__init__.py
src/upm/versions.py         version parsing/compare, backup age status
src/upm/models.py           Component dataclass + factory functions
src/upm/config.py           Settings.from_env, load_components
src/upm/latest.py           LatestResolver (endoflife/github/dockerhub/manual, cache)
src/upm/ssh.py              SshRunner (subprocess ssh) + SshError
src/upm/collectors/__init__.py
src/upm/collectors/pve01.py     PVE API collector
src/upm/collectors/oracle.py    Oracle SSH collector
src/upm/collectors/probes.py    config-driven probe collector
src/upm/store.py            atomic JSON store
src/upm/runner.py           build_status (run collectors, merge stale)
src/upm/server.py           HTTP handler
src/upm/static/index.html   status page
src/upm/main.py             entrypoint, scheduler loop
components.json             latest-source mapping + probes
Dockerfile, compose.yaml, .dockerignore, .gitignore, .env.example
tests/fakes.py, tests/test_*.py
```

---

### Task 1: Scaffold, versions, models

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `src/upm/__init__.py`, `src/upm/versions.py`, `src/upm/models.py`, `tests/fakes.py`, `tests/test_versions.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `versions.parse(v: str | None) -> tuple[int, ...] | None`
  - `versions.status_for(current: str | None, latest: str | None) -> str` (`"ok" | "outdated" | "unknown"`)
  - `versions.backup_status(last_ts: int | None, now_ts: int, max_age_h: int) -> str`
  - `models.Component` dataclass fields: `id, group, host, kind, name, current, latest, status, checked_at, stale=False, error=None`; methods `to_dict()`, classmethod `from_dict(d)`
  - `models.version_component(*, id, group, host, kind, name, current, latest, now) -> Component`
  - `models.backup_component(*, id, group, host, name, last_ts, max_age_h, now) -> Component` (kind `"backup"`, `current` = ISO time of last backup or None, `latest` None)
  - `models.error_component(*, id, group, host, kind, name, error, now) -> Component` (status `"error"`)
  - `tests/fakes.py`: `NOW` (aware datetime 2026-09-21 12:00 UTC), `FakeResolver(mapping)` with `.latest(key)`

- [ ] **Step 1: Init repo and scaffold**

```bash
cd /Users/ferko/development/universal-patch-management
git init
mkdir -p src/upm/collectors src/upm/static tests
touch src/upm/__init__.py src/upm/collectors/__init__.py
```

`pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["src", "tests"]
testpaths = ["tests"]
```

`requirements.txt`:
```
httpx==0.27.2
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest==8.3.3
```

`.gitignore`:
```
.env*
!.env.example
data/
secrets/
__pycache__/
.venv/
.pytest_cache/
```

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

`tests/fakes.py`:
```python
from datetime import datetime, timezone

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class FakeResolver:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def latest(self, key):
        return self.mapping.get(key)
```

- [ ] **Step 2: Write failing tests**

`tests/test_versions.py`:
```python
import pytest
from upm.versions import backup_status, parse, status_for


@pytest.mark.parametrize("cur,lat,exp", [
    ("8.2.4", "8.3.1", "outdated"),
    ("8.3.1", "8.3.1", "ok"),
    ("8.4", "8.3.1", "ok"),
    ("24.04", "24.04.3", "ok"),      # current less specific: compare on its precision
    ("24.04", "26.04", "outdated"),
    ("latest", "1.2.3", "unknown"),
    ("v1.2.3", "1.2.3", "ok"),
    ("1.2.3-alpine", "1.2.4", "outdated"),
    (None, "1.0", "unknown"),
    ("1.0", None, "unknown"),
])
def test_status_for(cur, lat, exp):
    assert status_for(cur, lat) == exp


def test_parse():
    assert parse("v10.2.1-x") == (10, 2, 1)
    assert parse("nope") is None
    assert parse(None) is None


def test_backup_status():
    now = 1_000_000
    assert backup_status(None, now, 36) == "unknown"
    assert backup_status(now - 3600 * 35, now, 36) == "ok"
    assert backup_status(now - 3600 * 37, now, 36) == "outdated"
```

`tests/test_models.py`:
```python
from fakes import NOW
from upm.models import Component, backup_component, error_component, version_component


def test_version_component_outdated():
    c = version_component(id="pve01:proxmox", group="core", host="pve01", kind="proxmox",
                          name="Proxmox VE", current="8.2.4", latest="8.3.1", now=NOW)
    assert c.status == "outdated"
    assert c.checked_at == "2026-09-21T12:00:00+00:00"
    assert c.stale is False and c.error is None


def test_backup_component_ok_and_missing():
    ts = int(NOW.timestamp()) - 3600
    c = backup_component(id="pve01:backup:100", group="core", host="pve01",
                         name="Backup web", last_ts=ts, max_age_h=36, now=NOW)
    assert c.kind == "backup" and c.status == "ok"
    assert c.current == "2026-09-21T11:00:00+00:00"
    m = backup_component(id="x", group="core", host="pve01", name="n",
                         last_ts=None, max_age_h=36, now=NOW)
    assert m.status == "unknown" and m.current is None


def test_error_component_roundtrip():
    c = error_component(id="probe:a", group="app", host="h", kind="lxc_app",
                        name="a", error="boom", now=NOW)
    assert c.status == "error" and c.error == "boom" and c.current is None
    assert Component.from_dict(c.to_dict()) == c
```

- [ ] **Step 3: Run tests, verify fail**

Run: `.venv/bin/pytest -q`
Expected: FAIL (ModuleNotFoundError: upm.versions)

- [ ] **Step 4: Implement**

`src/upm/versions.py`:
```python
import re

_NUM = re.compile(r"\d+(?:\.\d+)*")


def parse(v):
    if not v:
        return None
    m = _NUM.search(v)
    return tuple(int(p) for p in m.group().split(".")) if m else None


def status_for(current, latest):
    c, l = parse(current), parse(latest)
    if c is None or l is None:
        return "unknown"
    if len(c) < len(l):
        l = l[: len(c)]
    return "ok" if c >= l else "outdated"


def backup_status(last_ts, now_ts, max_age_h):
    if last_ts is None:
        return "unknown"
    return "ok" if now_ts - last_ts <= max_age_h * 3600 else "outdated"
```

`src/upm/models.py`:
```python
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .versions import backup_status, status_for


@dataclass
class Component:
    id: str
    group: str  # "core" | "app"
    host: str
    kind: str
    name: str
    current: str | None
    latest: str | None
    status: str  # ok | outdated | unknown | error
    checked_at: str
    stale: bool = False
    error: str | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def version_component(*, id, group, host, kind, name, current, latest, now):
    return Component(id=id, group=group, host=host, kind=kind, name=name,
                     current=current, latest=latest,
                     status=status_for(current, latest), checked_at=_iso(now))


def backup_component(*, id, group, host, name, last_ts, max_age_h, now):
    current = _iso(datetime.fromtimestamp(last_ts, timezone.utc)) if last_ts else None
    return Component(id=id, group=group, host=host, kind="backup", name=name,
                     current=current, latest=None,
                     status=backup_status(last_ts, int(now.timestamp()), max_age_h),
                     checked_at=_iso(now))


def error_component(*, id, group, host, kind, name, error, now):
    return Component(id=id, group=group, host=host, kind=kind, name=name,
                     current=None, latest=None, status="error",
                     checked_at=_iso(now), error=error)
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: scaffold, version comparison, component models"
```

---

### Task 2: Settings and components config

**Files:**
- Create: `src/upm/config.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `config.normalize_pve_host(raw: str) -> tuple[str, str]` returns `(base_url, hostname)`; default scheme `https`, default port 8006
  - `config.Settings` frozen dataclass: `pve_base_url: str|None, pve_hostname: str|None, pve_auth: str|None (full Authorization header value), pve_verify_tls: bool, pve_ssh_user: str, oracle_host: str|None, oracle_user: str|None, oracle_backup_cmd: str|None, ssh_key_path: str, known_hosts_path: str, poll_interval_min: int, backup_max_age_h: int, data_dir: Path, components_path: Path, port: int`
  - `Settings.from_env(env: Mapping[str,str]) -> Settings`
  - `config.Components` dataclass: `sources: dict[str, dict]`, `probes: list[dict]`
  - `config.load_components(path: Path) -> Components` (missing file gives empty; probes validated)

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:
```python
import json

import pytest
from upm.config import Settings, load_components, normalize_pve_host


@pytest.mark.parametrize("raw,url,host", [
    ("10.0.0.5", "https://10.0.0.5:8006", "10.0.0.5"),
    ("pve01.lan:8007", "https://pve01.lan:8007", "pve01.lan"),
    ("https://pve01.lan", "https://pve01.lan:8006", "pve01.lan"),
])
def test_normalize_pve_host(raw, url, host):
    assert normalize_pve_host(raw) == (url, host)


def test_settings_from_env_full():
    s = Settings.from_env({
        "PVE01_HOST": "10.0.0.5", "PVE01_HOST_USER": "root@pam",
        "PVE01_HOST_TOKEN": "upm", "PVE01_HOST_TOKEN_SECRET": "sekret",
        "ORACLE_SSH_HOST": "1.2.3.4", "ORACLE_SSH_USER": "ubuntu",
        "POLL_INTERVAL": "5",
    })
    assert s.pve_base_url == "https://10.0.0.5:8006"
    assert s.pve_auth == "PVEAPIToken=root@pam!upm=sekret"
    assert s.pve_verify_tls is False
    assert s.oracle_host == "1.2.3.4" and s.oracle_user == "ubuntu"
    assert s.poll_interval_min == 5 and s.pve_ssh_user == "root"


def test_settings_token_with_full_id_and_empty_env():
    s = Settings.from_env({
        "PVE01_HOST": "h", "PVE01_HOST_USER": "root@pam",
        "PVE01_HOST_TOKEN": "root@pam!full", "PVE01_HOST_TOKEN_SECRET": "x",
    })
    assert s.pve_auth == "PVEAPIToken=root@pam!full=x"
    empty = Settings.from_env({})
    assert empty.pve_base_url is None and empty.oracle_host is None
    assert empty.poll_interval_min == 30 and empty.port == 8080


def test_load_components(tmp_path):
    assert load_components(tmp_path / "missing.json").probes == []
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "sources": {"proxmox": {"source": "manual", "version": "9"}},
        "probes": [{"id": "probe:a", "name": "A", "kind": "lxc_app", "group": "app",
                    "host": "pve01", "target": {"type": "pve_lxc", "vmid": 1},
                    "cmd": "x", "regex": "(\\d+)"}],
    }))
    c = load_components(p)
    assert c.sources["proxmox"]["version"] == "9" and len(c.probes) == 1


def test_load_components_rejects_bad_probe(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"probes": [{"id": "nope:a"}]}))
    with pytest.raises(ValueError):
        load_components(p)
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: FAIL (ModuleNotFoundError: upm.config)

- [ ] **Step 3: Implement**

`src/upm/config.py`:
```python
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PROBE_REQUIRED = ("id", "name", "kind", "group", "host", "target", "cmd", "regex")


def normalize_pve_host(raw):
    url = raw if "://" in raw else f"https://{raw}"
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}:{p.port or 8006}", p.hostname


@dataclass(frozen=True)
class Settings:
    pve_base_url: str | None
    pve_hostname: str | None
    pve_auth: str | None
    pve_verify_tls: bool
    pve_ssh_user: str
    oracle_host: str | None
    oracle_user: str | None
    oracle_backup_cmd: str | None
    ssh_key_path: str
    known_hosts_path: str
    poll_interval_min: int
    backup_max_age_h: int
    data_dir: Path
    components_path: Path
    port: int

    @classmethod
    def from_env(cls, env):
        base = hostname = auth = None
        if env.get("PVE01_HOST"):
            base, hostname = normalize_pve_host(env["PVE01_HOST"])
            user = env.get("PVE01_HOST_USER", "")
            token = env.get("PVE01_HOST_TOKEN", "")
            token_id = token if "!" in token else f"{user}!{token}"
            auth = f"PVEAPIToken={token_id}={env.get('PVE01_HOST_TOKEN_SECRET', '')}"
        data_dir = Path(env.get("DATA_DIR", "/data"))
        return cls(
            pve_base_url=base,
            pve_hostname=hostname,
            pve_auth=auth,
            pve_verify_tls=env.get("PVE_VERIFY_TLS", "false").lower() == "true",
            pve_ssh_user=env.get("PVE01_SSH_USER", "root"),
            oracle_host=env.get("ORACLE_SSH_HOST") or None,
            oracle_user=env.get("ORACLE_SSH_USER") or None,
            oracle_backup_cmd=env.get("ORACLE_BACKUP_CMD") or None,
            ssh_key_path=env.get("SSH_KEY_PATH", "/run/secrets/upm_ssh_key"),
            known_hosts_path=env.get("KNOWN_HOSTS_PATH", str(data_dir / "known_hosts")),
            poll_interval_min=int(env.get("POLL_INTERVAL", "30")),
            backup_max_age_h=int(env.get("BACKUP_MAX_AGE_H", "36")),
            data_dir=data_dir,
            components_path=Path(env.get("COMPONENTS_PATH", "/app/components.json")),
            port=int(env.get("PORT", "8080")),
        )


@dataclass
class Components:
    sources: dict = field(default_factory=dict)
    probes: list = field(default_factory=list)


def load_components(path):
    path = Path(path)
    if not path.exists():
        return Components()
    raw = json.loads(path.read_text())
    probes = raw.get("probes", [])
    for p in probes:
        missing = [k for k in PROBE_REQUIRED if k not in p]
        if missing:
            raise ValueError(f"probe {p.get('id', '?')}: missing {missing}")
        if not p["id"].startswith("probe:"):
            raise ValueError(f"probe id must start with 'probe:': {p['id']}")
    return Components(sources=raw.get("sources", {}), probes=probes)
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: env settings and components.json loader"
```

---

### Task 3: Latest-stable resolver

**Files:**
- Create: `src/upm/latest.py`, `tests/test_latest.py`

**Interfaces:**
- Consumes: `versions.parse`
- Produces: `latest.LatestResolver(client: httpx.Client, mapping: dict, ttl: float = 21600, clock=time.monotonic)` with `.latest(key: str) -> str | None`. Never raises. Unknown key gives None. On fetch failure returns the cached value if any, else None.
- Source config shapes (in `components.json` `sources`):
  - `{"source":"endoflife","product":"ubuntu","lts":true,"field":"cycle"}`, first row matching (optionally `lts`), value of `field` (default `latest`)
  - `{"source":"github_release","repo":"owner/name"}`, `tag_name` minus leading `v`
  - `{"source":"dockerhub_tag","repo":"library/nginx"}`, highest numeric tag
  - `{"source":"manual","version":"1.2.3"}`

- [ ] **Step 1: Write failing tests**

`tests/test_latest.py`:
```python
import httpx
from upm.latest import LatestResolver


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_endoflife_lts_cycle():
    def h(req):
        assert req.url.path == "/api/ubuntu.json"
        return httpx.Response(200, json=[
            {"cycle": "25.10", "lts": False, "latest": "25.10"},
            {"cycle": "24.04", "lts": True, "latest": "24.04.3"}])
    r = LatestResolver(client(h), {"os:ubuntu": {"source": "endoflife", "product": "ubuntu",
                                                 "lts": True, "field": "cycle"}})
    assert r.latest("os:ubuntu") == "24.04"


def test_endoflife_default_field():
    def h(req):
        return httpx.Response(200, json=[{"cycle": "8", "latest": "8.3.1"}])
    r = LatestResolver(client(h), {"proxmox": {"source": "endoflife", "product": "proxmox-ve"}})
    assert r.latest("proxmox") == "8.3.1"


def test_github_release_strips_v():
    def h(req):
        assert req.url.path == "/repos/grafana/alloy/releases/latest"
        return httpx.Response(200, json={"tag_name": "v1.4.2"})
    r = LatestResolver(client(h), {"app:grafana/alloy": {"source": "github_release",
                                                         "repo": "grafana/alloy"}})
    assert r.latest("app:grafana/alloy") == "1.4.2"


def test_dockerhub_picks_highest_numeric_tag():
    def h(req):
        return httpx.Response(200, json={"results": [
            {"name": "latest"}, {"name": "1.9.0"}, {"name": "1.10.2"}, {"name": "1.10.2-alpine"}]})
    r = LatestResolver(client(h), {"app:nginx": {"source": "dockerhub_tag", "repo": "library/nginx"}})
    assert r.latest("app:nginx") == "1.10.2"


def test_manual_and_unknown():
    r = LatestResolver(client(lambda q: httpx.Response(500)),
                       {"x": {"source": "manual", "version": "3.1"}})
    assert r.latest("x") == "3.1"
    assert r.latest("nope") is None


def test_cache_and_stale_fallback():
    calls = []
    now = [0.0]

    def h(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=[{"cycle": "1", "latest": "1.0"}])
        return httpx.Response(500)

    r = LatestResolver(client(h), {"k": {"source": "endoflife", "product": "p"}},
                       ttl=100, clock=lambda: now[0])
    assert r.latest("k") == "1.0"
    now[0] = 50
    assert r.latest("k") == "1.0" and len(calls) == 1   # cached
    now[0] = 500
    assert r.latest("k") == "1.0" and len(calls) == 2   # refresh failed, stale value kept


def test_failure_without_cache_returns_none():
    r = LatestResolver(client(lambda q: httpx.Response(500)),
                       {"k": {"source": "github_release", "repo": "a/b"}})
    assert r.latest("k") is None
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_latest.py -q`
Expected: FAIL (ModuleNotFoundError: upm.latest)

- [ ] **Step 3: Implement**

`src/upm/latest.py`:
```python
import logging
import re
import time

from .versions import parse

log = logging.getLogger(__name__)
_TAG = re.compile(r"^v?\d+(\.\d+)*$")


def _endoflife(client, cfg):
    r = client.get(f"https://endoflife.date/api/{cfg['product']}.json")
    r.raise_for_status()
    for row in r.json():
        if cfg.get("lts") and not row.get("lts"):
            continue
        return str(row[cfg.get("field", "latest")])
    return None


def _github_release(client, cfg):
    r = client.get(f"https://api.github.com/repos/{cfg['repo']}/releases/latest",
                   headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()["tag_name"].lstrip("v")


def _dockerhub_tag(client, cfg):
    r = client.get(f"https://hub.docker.com/v2/repositories/{cfg['repo']}/tags",
                   params={"page_size": 100, "ordering": "last_updated"})
    r.raise_for_status()
    names = [t["name"] for t in r.json()["results"] if _TAG.match(t["name"])]
    return max(names, key=parse).lstrip("v") if names else None


def _manual(client, cfg):
    return cfg["version"]


SOURCES = {"endoflife": _endoflife, "github_release": _github_release,
           "dockerhub_tag": _dockerhub_tag, "manual": _manual}


class LatestResolver:
    def __init__(self, client, mapping, ttl=21600, clock=time.monotonic):
        self._client, self._mapping = client, mapping
        self._ttl, self._clock = ttl, clock
        self._cache = {}

    def latest(self, key):
        cfg = self._mapping.get(key)
        if not cfg:
            return None
        entry = self._cache.get(key)
        if entry and self._clock() - entry[0] < self._ttl:
            return entry[1]
        try:
            value = SOURCES[cfg["source"]](self._client, cfg)
        except Exception as exc:
            log.warning("latest lookup failed for %s: %s", key, exc)
            return entry[1] if entry else None
        self._cache[key] = (self._clock(), value)
        return value
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: latest-stable resolver with cache"
```

---

### Task 4: SSH runner

**Files:**
- Create: `src/upm/ssh.py`, `tests/test_ssh.py`

**Interfaces:**
- Produces: `ssh.SshError(Exception)`; `ssh.SshRunner(key_path: str, known_hosts: str, run=subprocess.run, timeout: int = 20)` with `.run(user: str, host: str, command: str) -> str` (stdout). Raises `SshError` on non-zero exit or timeout.

- [ ] **Step 1: Write failing tests**

`tests/test_ssh.py`:
```python
import subprocess

import pytest
from upm.ssh import SshError, SshRunner


def test_run_builds_command_and_returns_stdout():
    seen = {}

    def fake(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout="hello\n", stderr="")

    out = SshRunner("/k", "/kh", run=fake).run("ubuntu", "1.2.3.4", "uname -a")
    assert out == "hello\n"
    a = seen["args"]
    assert a[0] == "ssh" and "ubuntu@1.2.3.4" in a and a[-1] == "uname -a"
    assert "BatchMode=yes" in a and "UserKnownHostsFile=/kh" in a and "/k" in a
    assert seen["kw"]["timeout"] == 20


def test_nonzero_exit_raises():
    def fake(args, **kw):
        return subprocess.CompletedProcess(args, 255, stdout="", stderr="Permission denied")
    with pytest.raises(SshError, match="Permission denied"):
        SshRunner("/k", "/kh", run=fake).run("u", "h", "x")


def test_timeout_raises():
    def fake(args, **kw):
        raise subprocess.TimeoutExpired(args, 20)
    with pytest.raises(SshError, match="timed out"):
        SshRunner("/k", "/kh", run=fake).run("u", "h", "x")
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_ssh.py -q`
Expected: FAIL (ModuleNotFoundError: upm.ssh)

- [ ] **Step 3: Implement**

`src/upm/ssh.py`:
```python
import subprocess


class SshError(Exception):
    pass


class SshRunner:
    def __init__(self, key_path, known_hosts, run=subprocess.run, timeout=20):
        self._key, self._known_hosts = key_path, known_hosts
        self._run, self._timeout = run, timeout

    def run(self, user, host, command):
        args = ["ssh", "-i", self._key,
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={self._known_hosts}",
                "-o", "ConnectTimeout=10",
                f"{user}@{host}", command]
        try:
            p = self._run(args, capture_output=True, text=True, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            raise SshError(f"ssh {host} timed out")
        if p.returncode != 0:
            raise SshError(f"ssh {host} exit {p.returncode}: {p.stderr.strip()[:200]}")
        return p.stdout
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: ssh runner"
```

---

### Task 5: pve01 collector (Proxmox version + backups)

**Files:**
- Create: `src/upm/collectors/pve01.py`, `tests/test_pve01.py`

**Interfaces:**
- Consumes: `models.version_component`, `models.backup_component`; a resolver with `.latest(key)`.
- Produces: `Pve01Collector(client: httpx.Client, max_age_h: int)` with `host = "pve01"` and `.collect(resolver, now) -> list[Component]`. The client must already have `base_url` and the `Authorization` header. Component ids: `pve01:proxmox` (group `core`, kind `proxmox`, latest key `"proxmox"`), `pve01:backup:<vmid>` (group `core`). Raises on HTTP errors (runner handles).

- [ ] **Step 1: Write failing test**

`tests/test_pve01.py`:
```python
import httpx
from fakes import NOW, FakeResolver
from upm.collectors.pve01 import Pve01Collector


def make_client():
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"},
            {"vmid": 101, "name": "db", "type": "qemu"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [{"storage": "local"}],
        "/api2/json/nodes/pve01/storage/local/content": [
            {"vmid": 100, "ctime": ts - 7200}, {"vmid": 100, "ctime": ts - 3600}],
    }

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    return httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                        transport=httpx.MockTransport(h))


def test_collect():
    comps = Pve01Collector(make_client(), 36).collect(FakeResolver({"proxmox": "8.3.1"}), NOW)
    by_id = {c.id: c for c in comps}
    assert set(by_id) == {"pve01:proxmox", "pve01:backup:100", "pve01:backup:101"}
    px = by_id["pve01:proxmox"]
    assert (px.current, px.latest, px.status, px.group) == ("8.2.4", "8.3.1", "outdated", "core")
    assert by_id["pve01:backup:100"].status == "ok"
    assert by_id["pve01:backup:100"].current == "2026-09-21T11:00:00+00:00"  # newest ctime
    assert by_id["pve01:backup:101"].status == "unknown"                     # no backups


def test_http_error_raises():
    import pytest
    c = httpx.Client(base_url="https://pve", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(httpx.HTTPStatusError):
        Pve01Collector(c, 36).collect(FakeResolver(), NOW)
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_pve01.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

`src/upm/collectors/pve01.py`:
```python
from ..models import backup_component, version_component


class Pve01Collector:
    host = "pve01"

    def __init__(self, client, max_age_h):
        self._client, self._max_age_h = client, max_age_h

    def _get(self, path, params=None):
        r = self._client.get("/api2/json" + path, params=params)
        r.raise_for_status()
        return r.json()["data"]

    def collect(self, resolver, now):
        comps = [version_component(
            id="pve01:proxmox", group="core", host=self.host, kind="proxmox",
            name="Proxmox VE", current=self._get("/version")["version"],
            latest=resolver.latest("proxmox"), now=now)]

        guests = {str(r["vmid"]): r["name"]
                  for r in self._get("/cluster/resources", {"type": "vm"})}
        newest = {}
        for node in (n["node"] for n in self._get("/nodes")):
            for st in self._get(f"/nodes/{node}/storage", {"content": "backup"}):
                items = self._get(f"/nodes/{node}/storage/{st['storage']}/content",
                                  {"content": "backup"})
                for it in items:
                    vmid = str(it.get("vmid"))
                    newest[vmid] = max(newest.get(vmid, 0), it["ctime"])

        for vmid in sorted(guests):
            comps.append(backup_component(
                id=f"pve01:backup:{vmid}", group="core", host=self.host,
                name=f"Backup {guests[vmid]} ({vmid})",
                last_ts=newest.get(vmid), max_age_h=self._max_age_h, now=now))
        return comps
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: pve01 collector (version + backups)"
```

---

### Task 6: Oracle collector

**Files:**
- Create: `src/upm/collectors/oracle.py`, `tests/test_oracle.py`

**Interfaces:**
- Consumes: `SshRunner.run(user, host, cmd) -> str`, `models.*`, resolver `.latest(key)`.
- Produces: `OracleCollector(ssh, user: str, address: str, backup_cmd: str | None, max_age_h: int)` with `host = "oracle"`, `.collect(resolver, now)`. Ids: `oracle:os` (kind `vm_os`, latest key `os:<ID>`), `oracle:docker:<container>` (kind `docker_app`, latest key `app:<repo>`), `oracle:backup` (only if `backup_cmd`; the command prints epoch seconds). Group `app` for all. Also exports `parse_os_release(text) -> dict`, `split_image(image) -> tuple[str, str]`.

- [ ] **Step 1: Write failing tests**

`tests/test_oracle.py`:
```python
from fakes import NOW, FakeResolver
from upm.collectors.oracle import OracleCollector, parse_os_release, split_image
from upm.ssh import SshError

OS = 'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.4 LTS"\n'
PS = "alloy|grafana/alloy:v1.3.0\nweb|registry.local:5000/team/web:2.0.1\nedge|nginx\n"


class FakeSsh:
    def __init__(self, backup="1790000000\n"):
        self.backup = backup
        self.calls = []

    def run(self, user, host, cmd):
        self.calls.append((user, host, cmd))
        if "os-release" in cmd:
            return OS
        if "docker ps" in cmd:
            return PS
        if cmd == "backup-cmd":
            if isinstance(self.backup, Exception):
                raise self.backup
            return self.backup
        raise AssertionError(cmd)


def test_helpers():
    assert parse_os_release(OS)["VERSION_ID"] == "22.04"
    assert split_image("grafana/alloy:v1.3.0") == ("grafana/alloy", "v1.3.0")
    assert split_image("registry.local:5000/team/web:2.0.1") == ("registry.local:5000/team/web", "2.0.1")
    assert split_image("nginx") == ("nginx", "latest")


def test_collect_all():
    ts = int(NOW.timestamp()) - 3600
    ssh = FakeSsh(backup=f"{ts}\n")
    resolver = FakeResolver({"os:ubuntu": "24.04", "app:grafana/alloy": "1.4.2"})
    comps = OracleCollector(ssh, "ubuntu", "1.2.3.4", "backup-cmd", 36).collect(resolver, NOW)
    by_id = {c.id: c for c in comps}
    assert set(by_id) == {"oracle:os", "oracle:docker:alloy", "oracle:docker:web",
                          "oracle:docker:edge", "oracle:backup"}
    assert by_id["oracle:os"].current == "22.04" and by_id["oracle:os"].status == "outdated"
    assert by_id["oracle:docker:alloy"].current == "v1.3.0"
    assert by_id["oracle:docker:alloy"].status == "outdated"
    assert by_id["oracle:docker:edge"].status == "unknown"        # tag 'latest'
    assert by_id["oracle:backup"].status == "ok"
    assert all(c.group == "app" and c.host == "oracle" for c in comps)


def test_no_backup_cmd_skips_backup():
    comps = OracleCollector(FakeSsh(), "u", "h", None, 36).collect(FakeResolver(), NOW)
    assert "oracle:backup" not in {c.id for c in comps}


def test_bad_backup_output_becomes_error_component():
    for bad in ("garbage\n", SshError("boom")):
        comps = OracleCollector(FakeSsh(backup=bad), "u", "h", "backup-cmd", 36).collect(FakeResolver(), NOW)
        b = next(c for c in comps if c.id == "oracle:backup")
        assert b.status == "error"
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_oracle.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

`src/upm/collectors/oracle.py`:
```python
from ..models import backup_component, error_component, version_component
from ..ssh import SshError

DOCKER_PS = "docker ps --format '{{.Names}}|{{.Image}}'"


def parse_os_release(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"')
    return out


def split_image(image):
    name = image.partition("@")[0]
    if ":" in name.rsplit("/", 1)[-1]:
        repo, tag = name.rsplit(":", 1)
        return repo, tag
    return name, "latest"


class OracleCollector:
    host = "oracle"

    def __init__(self, ssh, user, address, backup_cmd, max_age_h):
        self._ssh, self._user, self._addr = ssh, user, address
        self._backup_cmd, self._max_age_h = backup_cmd, max_age_h

    def _run(self, cmd):
        return self._ssh.run(self._user, self._addr, cmd)

    def collect(self, resolver, now):
        osr = parse_os_release(self._run("cat /etc/os-release"))
        comps = [version_component(
            id="oracle:os", group="app", host=self.host, kind="vm_os",
            name=f"OS {osr.get('PRETTY_NAME', osr.get('ID', 'unknown'))}",
            current=osr.get("VERSION_ID"),
            latest=resolver.latest(f"os:{osr.get('ID', '')}"), now=now)]

        for line in self._run(DOCKER_PS).splitlines():
            if "|" not in line:
                continue
            name, image = line.split("|", 1)
            repo, tag = split_image(image)
            comps.append(version_component(
                id=f"oracle:docker:{name}", group="app", host=self.host,
                kind="docker_app", name=f"{name} ({repo})", current=tag,
                latest=resolver.latest(f"app:{repo}"), now=now))

        if self._backup_cmd:
            try:
                ts = int(self._run(self._backup_cmd).strip())
                comps.append(backup_component(
                    id="oracle:backup", group="app", host=self.host, name="Backup oracle",
                    last_ts=ts, max_age_h=self._max_age_h, now=now))
            except (SshError, ValueError) as exc:
                comps.append(error_component(
                    id="oracle:backup", group="app", host=self.host, kind="backup",
                    name="Backup oracle", error=str(exc)[:200], now=now))
        return comps
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: oracle ssh collector"
```

---

### Task 7: Probe collector

**Files:**
- Create: `src/upm/collectors/probes.py`, `tests/test_probes.py`

**Interfaces:**
- Consumes: probe dicts as validated by `config.load_components` (`id,name,kind,group,host,target,cmd,regex`, optional `latest_key`); `SshRunner.run`.
- Produces: `ProbesCollector(probes: list[dict], ssh, pve_ssh_user: str, pve_hostname: str | None)` with `host = "probe"`, `.collect(resolver, now)`. Never raises: per-probe failures become `error_component`s. Targets: `{"type":"pve_lxc","vmid":int}` runs `pct exec <vmid> -- sh -c <quoted cmd>` on the PVE host; `{"type":"ssh","host":str,"user":str}` runs `cmd` directly. Version = regex group 1.

- [ ] **Step 1: Write failing tests**

`tests/test_probes.py`:
```python
from fakes import NOW, FakeResolver
from upm.collectors.probes import ProbesCollector
from upm.ssh import SshError

LXC = {"id": "probe:lxc105:os", "name": "LXC 105 OS", "kind": "lxc_os", "group": "app",
       "host": "pve01", "target": {"type": "pve_lxc", "vmid": 105},
       "cmd": "cat /etc/os-release", "regex": r'VERSION_ID="?([\d.]+)', "latest_key": "os:debian"}
VM = {"id": "probe:vm110:app", "name": "VM 110 app", "kind": "vm_app", "group": "app",
      "host": "vm110", "target": {"type": "ssh", "host": "10.0.0.9", "user": "me"},
      "cmd": "app --version", "regex": r"(\d+\.\d+\.\d+)"}


class FakeSsh:
    def __init__(self):
        self.calls = []

    def run(self, user, host, cmd):
        self.calls.append((user, host, cmd))
        if "os-release" in cmd:
            return 'ID=debian\nVERSION_ID="12"\n'
        if host == "10.0.0.9":
            return "app v2.3.4\n"
        raise SshError("nope")


def test_lxc_and_ssh_probes():
    ssh = FakeSsh()
    comps = ProbesCollector([LXC, VM], ssh, "root", "10.0.0.5").collect(FakeResolver({"os:debian": "13"}), NOW)
    by_id = {c.id: c for c in comps}
    assert by_id["probe:lxc105:os"].current == "12" and by_id["probe:lxc105:os"].status == "outdated"
    assert by_id["probe:vm110:app"].current == "2.3.4" and by_id["probe:vm110:app"].status == "unknown"
    user, host, cmd = ssh.calls[0]
    assert (user, host) == ("root", "10.0.0.5")
    assert cmd == "pct exec 105 -- sh -c 'cat /etc/os-release'"
    assert by_id["probe:lxc105:os"].host == "pve01"


def test_failures_become_error_components():
    bad_regex = {**VM, "id": "probe:bad", "regex": r"(zzz)"}
    no_pve = ProbesCollector([LXC], FakeSsh(), "root", None).collect(FakeResolver(), NOW)
    assert no_pve[0].status == "error" and "PVE01" in no_pve[0].error
    c = ProbesCollector([bad_regex], FakeSsh(), "root", "h").collect(FakeResolver(), NOW)
    assert c[0].status == "error"
    unreachable = {**VM, "id": "probe:down", "target": {"type": "ssh", "host": "9.9.9.9", "user": "u"}}
    c = ProbesCollector([unreachable], FakeSsh(), "root", "h").collect(FakeResolver(), NOW)
    assert c[0].status == "error" and "nope" in c[0].error
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_probes.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

`src/upm/collectors/probes.py`:
```python
import re
import shlex

from ..models import error_component, version_component
from ..ssh import SshError


class ProbesCollector:
    host = "probe"

    def __init__(self, probes, ssh, pve_ssh_user, pve_hostname):
        self._probes, self._ssh = probes, ssh
        self._pve_user, self._pve_host = pve_ssh_user, pve_hostname

    def _exec(self, probe):
        t = probe["target"]
        if t["type"] == "pve_lxc":
            if not self._pve_host:
                raise SshError("PVE01 host not configured for pct exec")
            cmd = f"pct exec {int(t['vmid'])} -- sh -c {shlex.quote(probe['cmd'])}"
            return self._ssh.run(self._pve_user, self._pve_host, cmd)
        if t["type"] == "ssh":
            return self._ssh.run(t["user"], t["host"], probe["cmd"])
        raise ValueError(f"unknown target type {t['type']}")

    def collect(self, resolver, now):
        comps = []
        for p in self._probes:
            try:
                m = re.search(p["regex"], self._exec(p))
                if not m:
                    raise ValueError("regex did not match output")
                latest = resolver.latest(p["latest_key"]) if p.get("latest_key") else None
                comps.append(version_component(
                    id=p["id"], group=p["group"], host=p["host"], kind=p["kind"],
                    name=p["name"], current=m.group(1), latest=latest, now=now))
            except (SshError, ValueError, re.error, IndexError, KeyError) as exc:
                comps.append(error_component(
                    id=p["id"], group=p["group"], host=p["host"], kind=p["kind"],
                    name=p["name"], error=str(exc)[:200], now=now))
        return comps
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: config-driven probe collector"
```

---

### Task 8: Store and runner (merge, stale handling)

**Files:**
- Create: `src/upm/store.py`, `src/upm/runner.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: collectors (`.host: str`, `.collect(resolver, now) -> list[Component]`), `Component.to_dict()`.
- Produces:
  - `store.Store(data_dir: Path)` with `.load() -> dict | None`, `.save(doc: dict) -> None` (atomic; creates dir)
  - `runner.build_status(collectors, resolver, previous: dict | None, now: datetime) -> dict` returning `{"generated_at", "hosts": {host: {"ok","error","checked_at"}}, "components": [dict...]}`.
  - Rules: collector raises: host `ok=False`, previous components whose id starts with `"<host>:"` are kept with `stale=True, error=<msg>`. Component with `status == "error"` whose previous version exists and was not error: keep previous with `stale=True, error=<new error>`.

- [ ] **Step 1: Write failing tests**

`tests/test_runner.py`:
```python
from fakes import NOW, FakeResolver
from upm.models import error_component, version_component
from upm.runner import build_status
from upm.store import Store


def comp(id, host, cur="1.0", lat="1.0"):
    return version_component(id=id, group="core", host=host, kind="k", name=id,
                             current=cur, latest=lat, now=NOW)


class Ok:
    host = "a"

    def collect(self, resolver, now):
        return [comp("a:x", "a", "1.0", "2.0")]


class Boom:
    host = "b"

    def collect(self, resolver, now):
        raise RuntimeError("down")


class ProbeErr:
    host = "probe"

    def collect(self, resolver, now):
        return [error_component(id="probe:p", group="app", host="h", kind="k",
                                name="p", error="ssh fail", now=NOW)]


def test_ok_collector():
    doc = build_status([Ok()], FakeResolver(), None, NOW)
    assert doc["hosts"]["a"]["ok"] is True
    assert doc["generated_at"] == "2026-09-21T12:00:00+00:00"
    assert doc["components"][0]["status"] == "outdated"


def test_failed_host_keeps_previous_stale():
    prev = {"components": [comp("b:y", "b").to_dict(), comp("a:x", "a").to_dict()]}
    doc = build_status([Boom()], FakeResolver(), prev, NOW)
    assert doc["hosts"]["b"] == {"ok": False, "error": "down", "checked_at": "2026-09-21T12:00:00+00:00"}
    ids = {c["id"]: c for c in doc["components"]}
    assert set(ids) == {"b:y"}                      # only that host's ids are carried
    assert ids["b:y"]["stale"] is True and ids["b:y"]["error"] == "down"


def test_error_component_falls_back_to_previous():
    prev = {"components": [comp("probe:p", "h", "3.1", "3.1").to_dict()]}
    doc = build_status([ProbeErr()], FakeResolver(), prev, NOW)
    c = doc["components"][0]
    assert c["current"] == "3.1" and c["stale"] is True and c["error"] == "ssh fail"
    fresh = build_status([ProbeErr()], FakeResolver(), None, NOW)["components"][0]
    assert fresh["status"] == "error" and fresh["stale"] is False


def test_store_roundtrip_atomic(tmp_path):
    s = Store(tmp_path / "data")
    assert s.load() is None
    s.save({"generated_at": "x", "components": []})
    assert s.load() == {"generated_at": "x", "components": []}
    assert not list((tmp_path / "data").glob("*.tmp"))
    (tmp_path / "data" / "status.json").write_text("{corrupt")
    assert s.load() is None
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_runner.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

`src/upm/store.py`:
```python
import json
import os
from pathlib import Path


class Store:
    def __init__(self, data_dir):
        self._dir = Path(data_dir)
        self._path = self._dir / "status.json"

    def load(self):
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def save(self, doc):
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=2))
        os.replace(tmp, self._path)
```

`src/upm/runner.py`:
```python
import logging

log = logging.getLogger(__name__)


def build_status(collectors, resolver, previous, now):
    iso = now.isoformat(timespec="seconds")
    prev = {c["id"]: c for c in (previous or {}).get("components", [])}
    components, hosts = [], {}
    for col in collectors:
        try:
            fresh = col.collect(resolver, now)
        except Exception as exc:
            log.exception("collector %s failed", col.host)
            msg = str(exc)[:300]
            hosts[col.host] = {"ok": False, "error": msg, "checked_at": iso}
            components += [{**old, "stale": True, "error": msg}
                           for old in prev.values() if old["id"].startswith(f"{col.host}:")]
            continue
        hosts[col.host] = {"ok": True, "error": None, "checked_at": iso}
        for c in fresh:
            d = c.to_dict()
            old = prev.get(d["id"])
            if d["status"] == "error" and old and old["status"] != "error":
                d = {**old, "stale": True, "error": d["error"]}
            components.append(d)
    return {"generated_at": iso, "hosts": hosts, "components": components}
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: atomic store and status builder with stale handling"
```

---

### Task 9: HTTP server and status page

**Files:**
- Create: `src/upm/server.py`, `src/upm/static/index.html`, `tests/test_server.py`

**Interfaces:**
- Consumes: `Store.load()`.
- Produces: `server.make_server(store: Store, static_dir: Path, max_age_s: int, port: int, bind: str = "0.0.0.0") -> ThreadingHTTPServer`. Routes: `GET /` and `/index.html` return the page; `GET /status.json` returns 200 JSON or 503 `{"error":"no data yet"}`; `GET /healthz` returns 200 if `generated_at` is younger than `max_age_s`, else 503; anything else 404.

- [ ] **Step 1: Write failing tests**

`tests/test_server.py`:
```python
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from upm.server import make_server
from upm.store import Store

STATIC = Path(__file__).parent.parent / "src" / "upm" / "static"


@pytest.fixture
def srv(tmp_path):
    store = Store(tmp_path)
    s = make_server(store, STATIC, max_age_s=100, port=0, bind="127.0.0.1")
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield store, f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_index_served(srv):
    _, base = srv
    status, body = get(base + "/")
    assert status == 200 and b"Core Infra" in body and b"Application" in body


def test_status_and_health(srv):
    store, base = srv
    assert get(base + "/status.json")[0] == 503
    assert get(base + "/healthz")[0] == 503
    now = datetime.now(timezone.utc)
    store.save({"generated_at": now.isoformat(), "hosts": {}, "components": []})
    status, body = get(base + "/status.json")
    assert status == 200 and json.loads(body)["components"] == []
    assert get(base + "/healthz")[0] == 200
    store.save({"generated_at": (now - timedelta(seconds=500)).isoformat(),
                "hosts": {}, "components": []})
    assert get(base + "/healthz")[0] == 503


def test_404(srv):
    assert get(srv[1] + "/nope")[0] == 404
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_server.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement server**

`src/upm/server.py`:
```python
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_server(store, static_dir, max_age_s, port, bind="0.0.0.0"):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                body = (static_dir / "index.html").read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
            elif path == "/status.json":
                doc = store.load()
                if doc is None:
                    self._json(503, {"error": "no data yet"})
                else:
                    self._json(200, doc)
            elif path == "/healthz":
                doc = store.load()
                try:
                    age = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(doc["generated_at"])).total_seconds()
                except (TypeError, KeyError, ValueError):
                    age = None
                ok = age is not None and age <= max_age_s
                self._json(200 if ok else 503, {"ok": ok, "age_s": age})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((bind, port), Handler)
```

- [ ] **Step 4: Implement page**

`src/upm/static/index.html` (values inserted via `textContent` only, since docker image names are untrusted):
```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Patch Status</title>
<style>
  :root { --bg:#f6f7f9; --fg:#1b1f24; --card:#fff; --line:#dde1e6; --muted:#5b6672;
          --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; --unk:#57606a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0d1117; --fg:#e6edf3; --card:#161b22; --line:#30363d; --muted:#8b949e;
            --ok:#3fb950; --warn:#d29922; --bad:#f85149; --unk:#8b949e; } }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
         font:14px/1.45 system-ui, sans-serif; }
  main { max-width:960px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; }
  h2 { font-size:16px; margin:24px 0 8px; }
  #meta { color:var(--muted); margin-bottom:8px; }
  #hosts span { display:inline-block; margin-right:12px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; overflow-x:auto; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--muted); font-weight:600; }
  tr:last-child td { border-bottom:0; }
  .badge { font-weight:600; }
  .ok { color:var(--ok); } .outdated { color:var(--warn); }
  .error { color:var(--bad); } .unknown { color:var(--unk); }
  #banner { color:var(--bad); margin:8px 0; }
</style>
</head>
<body>
<main>
  <h1>Patch Status</h1>
  <div id="meta"></div>
  <div id="hosts"></div>
  <div id="banner"></div>
  <h2>Core Infra</h2>
  <div class="card"><table id="core"></table></div>
  <h2>Application</h2>
  <div class="card"><table id="app"></table></div>
</main>
<script>
const SYMBOL = { ok: "✔ ok", outdated: "▲ outdated", error: "✖ error", unknown: "? unknown" };
const COLS = ["Component", "Host", "Kind", "Current", "Latest", "Status"];

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function age(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 172800) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}

function renderTable(table, rows) {
  table.replaceChildren();
  const head = el("tr");
  COLS.forEach(c => head.append(el("th", c)));
  table.append(head);
  if (!rows.length) {
    const tr = el("tr"); const td = el("td", "No components"); td.colSpan = COLS.length;
    tr.append(td); table.append(tr); return;
  }
  for (const c of rows) {
    const tr = el("tr");
    const isBackup = c.kind === "backup";
    tr.append(el("td", c.name), el("td", c.host), el("td", c.kind));
    tr.append(el("td", isBackup && c.current ? age(c.current) : (c.current ?? "-")));
    tr.append(el("td", c.latest ?? "-"));
    const label = SYMBOL[c.status] + (c.stale ? " (stale)" : "");
    const td = el("td", label, "badge " + c.status);
    if (c.error) td.title = c.error;
    tr.append(td);
    table.append(tr);
  }
}

function render(doc) {
  document.getElementById("meta").textContent = "Updated " + age(doc.generated_at);
  const hosts = document.getElementById("hosts");
  hosts.replaceChildren();
  for (const [name, h] of Object.entries(doc.hosts)) {
    const s = el("span", name + ": " + (h.ok ? "✔ ok" : "✖ " + (h.error || "error")), h.ok ? "ok" : "error");
    hosts.append(s);
  }
  for (const g of ["core", "app"]) {
    renderTable(document.getElementById(g), doc.components.filter(c => c.group === g));
  }
  document.getElementById("banner").textContent = "";
}

async function load() {
  try {
    const r = await fetch("/status.json", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status === 503 ? "no data collected yet" : "HTTP " + r.status);
    render(await r.json());
  } catch (e) {
    document.getElementById("banner").textContent = "Cannot load status: " + e.message;
  }
}
load();
setInterval(load, 60000);
</script>
</body>
</html>
```

- [ ] **Step 5: Run, verify pass**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: http server and status page"
```

---

### Task 10: Entrypoint, components.json, Docker packaging

**Files:**
- Create: `src/upm/main.py`, `components.json`, `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`, `tests/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main.build_collectors(settings, components, ssh, pve_client) -> list`; `main.poll_once(collectors, resolver, store, now)`; `python -m upm.main` runs scheduler thread + server.

- [ ] **Step 1: Write failing test**

`tests/test_main.py`:
```python
from pathlib import Path

from fakes import NOW, FakeResolver
from upm.config import Components, Settings
from upm.main import build_collectors, poll_once
from upm.store import Store


def settings(env):
    return Settings.from_env(env)


def test_build_collectors_by_config():
    assert build_collectors(settings({}), Components(), None, None) == []
    env = {"PVE01_HOST": "h", "PVE01_HOST_USER": "root@pam", "PVE01_HOST_TOKEN": "t",
           "PVE01_HOST_TOKEN_SECRET": "s", "ORACLE_SSH_HOST": "o", "ORACLE_SSH_USER": "u"}
    probe = {"id": "probe:a"}
    cols = build_collectors(settings(env), Components(probes=[probe]), object(), object())
    assert [c.host for c in cols] == ["pve01", "oracle", "probe"]


def test_poll_once_saves_and_survives_failure(tmp_path):
    class Boom:
        host = "z"

        def collect(self, r, n):
            raise RuntimeError("x")

    store = Store(tmp_path)
    poll_once([Boom()], FakeResolver(), store, NOW)
    doc = store.load()
    assert doc["hosts"]["z"]["ok"] is False
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/pytest tests/test_main.py -q`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

`src/upm/main.py`:
```python
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .collectors.oracle import OracleCollector
from .collectors.probes import ProbesCollector
from .collectors.pve01 import Pve01Collector
from .config import Settings, load_components
from .latest import LatestResolver
from .runner import build_status
from .server import make_server
from .ssh import SshRunner
from .store import Store

log = logging.getLogger("upm")


def build_collectors(s, components, ssh, pve_client):
    cols = []
    if s.pve_base_url:
        cols.append(Pve01Collector(pve_client, s.backup_max_age_h))
    if s.oracle_host and s.oracle_user:
        cols.append(OracleCollector(ssh, s.oracle_user, s.oracle_host,
                                    s.oracle_backup_cmd, s.backup_max_age_h))
    if components.probes:
        cols.append(ProbesCollector(components.probes, ssh, s.pve_ssh_user, s.pve_hostname))
    return cols


def poll_once(collectors, resolver, store, now):
    doc = build_status(collectors, resolver, store.load(), now)
    store.save(doc)


def poll_loop(collectors, resolver, store, interval_s, stop):
    while not stop.is_set():
        try:
            poll_once(collectors, resolver, store, datetime.now(timezone.utc))
        except Exception:
            log.exception("poll failed")
        stop.wait(interval_s)


def main():
    logging.basicConfig(level=logging.INFO)
    s = Settings.from_env(os.environ)
    components = load_components(s.components_path)
    ssh = SshRunner(s.ssh_key_path, s.known_hosts_path)
    pve_client = httpx.Client(
        base_url=s.pve_base_url or "", verify=s.pve_verify_tls, timeout=15,
        headers={"Authorization": s.pve_auth or ""})
    resolver = LatestResolver(httpx.Client(timeout=15, follow_redirects=True), components.sources)
    store = Store(s.data_dir)
    collectors = build_collectors(s, components, ssh, pve_client)
    interval = s.poll_interval_min * 60
    stop = threading.Event()
    threading.Thread(target=poll_loop, args=(collectors, resolver, store, interval, stop),
                     daemon=True).start()
    static = Path(__file__).parent / "static"
    server = make_server(store, static, max_age_s=interval * 3, port=s.port)
    log.info("serving on :%d, collectors=%s", s.port, [c.host for c in collectors])
    try:
        server.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
```

`components.json` (starter; verify product slugs and adjust probes to the real guests):
```json
{
  "sources": {
    "proxmox": {"source": "endoflife", "product": "proxmox-ve"},
    "os:ubuntu": {"source": "endoflife", "product": "ubuntu", "lts": true, "field": "cycle"},
    "os:debian": {"source": "endoflife", "product": "debian", "field": "cycle"},
    "app:grafana/alloy": {"source": "github_release", "repo": "grafana/alloy"}
  },
  "probes": []
}
```

`.dockerignore`:
```
.env*
.git
.venv
tests
docs
secrets
data
__pycache__
```

`.env.example`:
```
PVE01_HOST=
PVE01_HOST_USER=root@pam
PVE01_HOST_TOKEN=
PVE01_HOST_TOKEN_SECRET=
# optional: user for `pct exec` probes over SSH on the PVE host
PVE01_SSH_USER=root
ORACLE_SSH_HOST=
ORACLE_SSH_USER=
# optional: command on the Oracle host that prints the last backup time as epoch seconds
ORACLE_BACKUP_CMD=
POLL_INTERVAL=30
```

`Dockerfile`:
```dockerfile
FROM python:3.12-alpine
ARG UPM_UID=1000
RUN apk add --no-cache openssh-client && adduser -D -u ${UPM_UID} upm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY components.json .
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 DATA_DIR=/data COMPONENTS_PATH=/app/components.json
RUN mkdir /data && chown upm /data
USER upm
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "upm.main"]
```

`compose.yaml`:
```yaml
services:
  upm:
    build:
      context: .
      args:
        UPM_UID: ${UPM_UID:-1000}   # must own secrets/id_ed25519 (chmod 600)
    ports:
      - "${BIND:-127.0.0.1}:8080:8080"   # set BIND to the LAN IP to expose
    env_file: .env
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - upm-data:/data
      - ./secrets/id_ed25519:/run/secrets/upm_ssh_key:ro
      - ./components.json:/app/components.json:ro
    restart: unless-stopped
volumes:
  upm-data: {}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 5: Build image and check size**

Run: `docker build -t upm . && docker images upm --format '{{.Size}}'`
Expected: builds; size roughly 80-100MB (alpine + openssh-client + httpx).

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: entrypoint, components mapping, docker packaging"
```

---

### Task 11: Live smoke test (manual, needs real hosts)

**Files:**
- Create (untracked, never commit): `.env`, `secrets/id_ed25519`

**Interfaces:** none.

- [ ] **Step 1: Create `.env`** from `.env.example` with only the keys the app needs. Copy values for the `PVE01_*` and `ORACLE_SSH_*` keys from `.env.dev` by hand. Do not copy Odoo or Grafana keys. If `PVE01_HOST_TOKEN` already contains `!`, leave `PVE01_HOST_USER` as is; the code handles both shapes.

- [ ] **Step 2: Provide an SSH key** at `secrets/id_ed25519`, `chmod 600`, owned by your uid. It must be authorized on the Oracle host (and on pve01 if you use `pve_lxc` probes).

- [ ] **Step 3: Run**

```bash
UPM_UID=$(id -u) docker compose up --build -d
sleep 20 && curl -s localhost:8080/healthz && curl -s localhost:8080/status.json | head -c 1500
```
Expected: `{"ok": true, ...}`; components include `pve01:proxmox`, `pve01:backup:*`, `oracle:os`, `oracle:docker:*`. If a host failed, check `docker compose logs upm` and the `hosts` block in `status.json` (an HTTP 401 means the token/user format is wrong; an SSH error means key/authorization).

- [ ] **Step 4: Verify latest sources.** Check that `pve01:proxmox` has a non-null `latest`. If not, the endoflife product slug in `components.json` is wrong: run `curl -s https://endoflife.date/api/all.json | tr ',' '\n' | grep -i proxmox` and fix it.

- [ ] **Step 5: Look at the page** at `http://127.0.0.1:8080/`. Confirm both tables render, the badges show text as well as colour, and a dark-mode toggle keeps it readable.

- [ ] **Step 6: Add real probes** to `components.json` for the LXC/VM apps you want tracked (LXC OS via `pct exec`, VM apps via `ssh` target), restart with `docker compose restart upm`, and confirm they appear or show an error with a readable message.

- [ ] **Step 7: Commit `components.json` changes only**

```bash
git add components.json && git commit -m "chore: real probes and sources"
```

---

## Self-Review Notes

- **Spec coverage:** single container, port 8080 (Task 10); page with Core/Application groups, badges, backup age, host health, 60s refresh (9); JSON storage, atomic (8); pve01 API host/backups (5); Oracle via SSH (6); resolver with 4 source types and 6h cache (3); stale handling (8); healthcheck on data age (9/10); read-only fs, non-root, `.env*` excluded (10); unit tests on fixtures (1-9). LXC/VM inner versions are covered by the probe collector (7), a flagged spec amendment.
- **Type consistency:** `Component` fields, `version_component`/`backup_component`/`error_component` kwargs, `collect(resolver, now)`, `.host`, `SshRunner.run(user, host, cmd)`, `Store.load/save`, `make_server(store, static_dir, max_age_s, port, bind)` match across tasks.
- **Known limits:** `known_hosts` lives in the `/data` volume (accept-new on first connect); `pct exec` probes need the container's key authorized on pve01; PVE TLS verification is off by default (`PVE_VERIFY_TLS=true` to enable); the Proxmox endoflife slug is verified in Task 11.
