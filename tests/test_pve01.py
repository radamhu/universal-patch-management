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


def vzdump_task(upid, endtime):
    return {"upid": upid, "type": "vzdump", "status": "OK", "endtime": endtime}


def vzdump_log(vmids, storage):
    ids = " ".join(vmids)
    return [{"n": 1, "t": f"INFO: starting new backup job: vzdump {ids} "
                          f"--notes-template 'x' --mode snapshot --storage {storage}"}]


UPID_100 = "UPID:pve01:00000001:00000001:00000001:vzdump::root@pam:"


def make_client(extra=None):
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"},
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-100", "storage": "local", "vmid": "100", "schedule": "sat 00:00"},
            {"id": "job-101", "storage": "local", "vmid": "101", "schedule": "sun 00:00"}],
        "/api2/json/nodes/pve01/tasks": [vzdump_task(UPID_100, ts - 3600)],
        f"/api2/json/nodes/pve01/tasks/{UPID_100}/log": vzdump_log(["100"], "local"),
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
                          "pve01:backupjob:job-100", "pve01:backupjob:job-101"}
    px = by_id["pve01:proxmox"]
    assert (px.current, px.latest, px.status, px.group) == ("8.2.4", "8.3.1", "outdated", "core")
    assert by_id["pve01:backupjob:job-100"].status == "ok"
    assert by_id["pve01:backupjob:job-100"].current == "2026-09-21T11:00:00+00:00"  # task endtime
    assert by_id["pve01:backupjob:job-101"].status == "unknown"                     # no matching task
    lxc_os = by_id["pve01:lxc_os:100"]
    assert (lxc_os.current, lxc_os.latest, lxc_os.status, lxc_os.group) == ("12", "13", "outdated", "core")
    assert ssh.calls[0][:2] == ("root", "10.0.0.5")
    # vm_os came from the guest agent, not ssh, so unknown latest (no os:vm-agent-distro source)
    assert by_id["pve01:vm_os:101"].status == "error"


def test_backup_job_all_guests_respects_exclude():
    """A job with all=1 covers every guest except those in its exclude list."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"},
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-all", "storage": "local", "all": 1, "exclude": "101"}],
        "/api2/json/nodes/pve01/tasks": [vzdump_task(UPID_100, ts - 3600)],
        # job's real run only ever backs up 100 (101 is excluded), so the task log
        # must list just "100" for the vmid-set match to succeed
        f"/api2/json/nodes/pve01/tasks/{UPID_100}/log": vzdump_log(["100"], "local"),
    }

    def h(req):
        if "/agent/exec" in req.url.path:
            return httpx.Response(200, json={"data": {"pid": 1, "exited": 1, "out-data": ""}})
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {comp.id: comp for comp in comps}
    assert by_id["pve01:backupjob:job-all"].status == "ok"


def test_backup_job_storage_mismatch_stays_unknown():
    """A task on a different storage than the job's must not be mistaken for its run."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-100", "storage": "local", "vmid": "100"}],
        "/api2/json/nodes/pve01/tasks": [vzdump_task(UPID_100, ts - 3600)],
        f"/api2/json/nodes/pve01/tasks/{UPID_100}/log": vzdump_log(["100"], "other-storage"),
    }

    def h(req):
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {comp.id: comp for comp in comps}
    assert by_id["pve01:backupjob:job-100"].status == "unknown"


def test_disabled_backup_job_skipped():
    """A disabled job produces no component."""
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-off", "storage": "local", "vmid": "100", "enabled": 0}],
    }

    def h(req):
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    assert not any(comp.id == "pve01:backupjob:job-off" for comp in comps)


def test_vm_os_via_guest_agent():
    """VM OS is collected through the QEMU guest agent, not SSH."""
    handler = make_agent_exec('ID=ubuntu\nVERSION_ID="24.04"\n')

    def combined(req):
        if "/agent/exec" in req.url.path:
            return handler(req)
        return make_client_handler(req)

    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [],
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
    assert "pve01:proxmox" in by_id and "pve01:backupjob:job-100" in by_id


def test_http_error_raises():
    c = httpx.Client(base_url="https://pve", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(httpx.HTTPStatusError):
        Pve01Collector(c, 36).collect(FakeResolver(), NOW)


def test_task_log_error_continues():
    """One task's log 500s, another matches - collect() still succeeds using the match."""
    ts = int(NOW.timestamp())
    bad_upid = "UPID:pve01:00000002:00000002:00000002:vzdump::root@pam:"
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-100", "storage": "local", "vmid": "100"}],
        "/api2/json/nodes/pve01/tasks": [
            vzdump_task(bad_upid, ts - 1800), vzdump_task(UPID_100, ts - 3600)],
        f"/api2/json/nodes/pve01/tasks/{UPID_100}/log": vzdump_log(["100"], "local"),
    }

    def h(req):
        assert req.headers["Authorization"] == "PVEAPIToken=t"
        if req.url.path == f"/api2/json/nodes/pve01/tasks/{bad_upid}/log":
            return httpx.Response(500)
        if req.url.path not in data:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data[req.url.path]})

    c = httpx.Client(base_url="https://pve", headers={"Authorization": "PVEAPIToken=t"},
                     transport=httpx.MockTransport(h))
    comps = Pve01Collector(c, 36).collect(FakeResolver(), NOW)
    by_id = {c.id: c for c in comps}
    assert "pve01:backupjob:job-100" in by_id
    assert by_id["pve01:backupjob:job-100"].status == "ok"


def test_skip_template_guests():
    """Template guests are skipped."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc", "template": 1},
            {"vmid": 101, "name": "db", "type": "qemu", "node": "pve01"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-101", "storage": "local", "vmid": "101"}],
        "/api2/json/nodes/pve01/tasks": [vzdump_task(UPID_100, ts - 3600)],
        f"/api2/json/nodes/pve01/tasks/{UPID_100}/log": vzdump_log(["101"], "local"),
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
    assert set(by_id) == {"pve01:proxmox", "pve01:vm_os:101", "pve01:backupjob:job-101"}


def test_unparseable_task_log_ignored():
    """A vzdump task whose log doesn't start with the expected line is skipped."""
    ts = int(NOW.timestamp())
    data = {
        "/api2/json/version": {"version": "8.2.4"},
        "/api2/json/cluster/resources": [
            {"vmid": 100, "name": "web", "type": "lxc"}],
        "/api2/json/nodes": [{"node": "pve01"}],
        "/api2/json/cluster/backup": [
            {"id": "job-100", "storage": "local", "vmid": "100"}],
        "/api2/json/nodes/pve01/tasks": [vzdump_task(UPID_100, ts - 3600)],
        f"/api2/json/nodes/pve01/tasks/{UPID_100}/log": [{"n": 1, "t": "INFO: something else"}],
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
    assert by_id["pve01:backupjob:job-100"].status == "unknown"
