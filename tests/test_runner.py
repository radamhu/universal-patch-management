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
