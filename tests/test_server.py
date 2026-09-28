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
    s.server_close()


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


def test_healthz_missing_generated_at(srv):
    store, base = srv
    store.save({"hosts": {}, "components": []})
    assert get(base + "/healthz")[0] == 503


def test_index_html_route(srv):
    _, base = srv
    status, body = get(base + "/index.html")
    assert status == 200 and b"Core Infra" in body


def test_traversal_attack(srv):
    _, base = srv
    assert get(base + "/../server.py")[0] == 404


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
