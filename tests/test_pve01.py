import pytest
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
    c = httpx.Client(base_url="https://pve", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(httpx.HTTPStatusError):
        Pve01Collector(c, 36).collect(FakeResolver(), NOW)


def test_storage_error_continues():
    """One storage returns 500, another returns data - collect() still succeeds."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [{"storage": "bad"}, {"storage": "good"}],
        "/api2/json/nodes/pve01/storage/good/content": [
            {"vmid": 100, "ctime": ts - 3600}],
    }

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        # bad storage returns 500, good storage returns data
        if req.url.path == "/api2/json/nodes/pve01/storage/bad/content":
            return httpx.Response(500)
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {c.id: c for c in comps}
    assert "pve01:backup:100" in by_id
    assert by_id["pve01:backup:100"].status == "ok"


def test_skip_template_guests():
    """Template guests are skipped."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc", "template": 1},
            {"vmid": 101, "name": "db", "type": "qemu"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [{"storage": "local"}],
        "/api2/json/nodes/pve01/storage/local/content": [
            {"vmid": 101, "ctime": ts - 3600}],
    }

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {c.id: c for c in comps}
    # Only 2 components: proxmox version + db backup (web is template, skipped)
    assert set(by_id) == {"pve01:proxmox", "pve01:backup:101"}


def test_skip_items_without_vmid_ctime():
    """Backup items lacking vmid or ctime are ignored."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [{"storage": "local"}],
        "/api2/json/nodes/pve01/storage/local/content": [
            {"vmid": 100, "ctime": ts - 3600},
            {"vmid": 100},  # missing ctime
            {"ctime": ts - 1800},  # missing vmid
            {"vmid": 100, "ctime": ts - 7200}],
    }

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {c.id: c for c in comps}
    assert "pve01:backup:100" in by_id
    # Should use the newest valid ctime (ts - 3600, not ts - 7200)
    assert by_id["pve01:backup:100"].current == "2026-09-21T11:00:00+00:00"
