import pytest
import httpx
from fakes import NOW, FakeResolver
from upm.collectors.pve01 import Pve01Collector


class FakeSsh:
    def __init__(self, output='ID=debian\nVERSION_ID="12"\n'):
        self.output = output
        self.calls = []

    def run(self, user, host, cmd):
        self.calls.append((user, host, cmd))
        return self.output


def make_client(extra=None):
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"},
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [{"storage": "local"}],
        "/api2/json/nodes/pve01/storage/local/content": [
            {"vmid": 100, "ctime": ts - 7200}, {"vmid": 100, "ctime": ts - 3600}],
    }
    if extra:
        data.update(extra)

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    return httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                        transport=httpx.MockTransport(h))


def make_agent_exec(out_data, exited=1):
    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if req.url.path.endswith("/agent/exec"):
            return httpx.Response(200, json={"data": {"pid": 1}})
        if req.url.path.endswith("/agent/exec-status"):
            return httpx.Response(200, json={"data": {"exited": exited, "out-data": out_data}})
        return httpx.Response(404)
    return h


def test_collect():
    ssh = FakeSsh()
    comps = Pve01Collector(make_client(), 36, ssh, "root", "10.0.0.5").collect(
        FakeResolver({"proxmox": "8.3.1", "os:debian": "13"}), NOW)
    by_id = {c.id: c for c in comps}
    assert set(by_id) == {"pve01:proxmox", "pve01:lxc_os:100", "pve01:vm_os:101",
                          "pve01:backup:100", "pve01:backup:101"}
    px = by_id["pve01:proxmox"]
    assert (px.current, px.latest, px.status, px.group) == ("8.2.4", "8.3.1", "outdated", "core")
    assert by_id["pve01:backup:100"].status == "ok"
    assert by_id["pve01:backup:100"].current == "2026-09-21T11:00:00+00:00"  # newest ctime
    assert by_id["pve01:backup:101"].status == "unknown"                     # no backups
    lxc_os = by_id["pve01:lxc_os:100"]
    assert (lxc_os.current, lxc_os.latest, lxc_os.status, lxc_os.group) == ("12", "13", "outdated", "core")
    assert ssh.calls[0][:2] == ("root", "10.0.0.5")
    # vm_os came from the guest agent, not ssh, so unknown latest (no os:vm-agent-distro source)
    assert by_id["pve01:vm_os:101"].status == "error"


def test_vm_os_via_guest_agent():
    """VM OS is collected through the QEMU guest agent, not SSH."""
    handler = make_agent_exec('ID=ubuntu\nVERSION_ID="24.04"\n')

    def combined(req):
        if "/agent/exec" in req.url.path:
            return handler(req)
        return make_client_handler(req)

    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [],
    }

    def make_client_handler(req):
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(combined))
    comps = Pve01Collector(c, 36).collect(FakeResolver({"os:ubuntu": "24.04"}), NOW)
    by_id = {comp.id: comp for comp in comps}
    vm_os = by_id["pve01:vm_os:101"]
    assert (vm_os.current, vm_os.latest, vm_os.status) == ("24.04", "24.04", "ok")


def test_lxc_os_without_ssh_becomes_error():
    """No SSH configured -> LXC OS check errors but rest of collect() still succeeds."""
    comps = Pve01Collector(make_client(), 36).collect(FakeResolver({"proxmox": "8.3.1"}), NOW)
    by_id = {c.id: c for c in comps}
    assert by_id["pve01:lxc_os:100"].status == "error"
    assert "pve01:proxmox" in by_id and "pve01:backup:100" in by_id


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
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/nodes/pve01/storage": [{"storage": "local"}],
        "/api2/json/nodes/pve01/storage/local/content": [
            {"vmid": 101, "ctime": ts - 3600}],
    }

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if "/agent/exec" in req.url.path:
            return httpx.Response(200, json={"data": {"pid": 1, "exited": 1, "out-data": ""}})
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {c.id: c for c in comps}
    # web (lxc) is a template and skipped entirely; db (qemu) still gets vm_os + backup
    assert set(by_id) == {"pve01:proxmox", "pve01:vm_os:101", "pve01:backup:101"}


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
