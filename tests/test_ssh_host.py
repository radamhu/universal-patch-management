from fakes import NOW, FakeResolver
from upm.collectors.ssh_host import SshHostCollector, parse_os_release, split_image
from upm.ssh import SshError

OS = 'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.4 LTS"\n'
PS = "alloy|grafana/alloy:v1.3.0\nweb|registry.local:5000/team/web:2.0.1\nedge|nginx\n"


class FakeSsh:
    def __init__(self, backup="1790000000\n", os_release=OS, docker_ps=PS):
        self.backup = backup
        self.os_release = os_release
        self.docker_ps = docker_ps
        self.calls = []

    def run(self, user, host, cmd):
        self.calls.append((user, host, cmd))
        if "os-release" in cmd:
            if isinstance(self.os_release, Exception):
                raise self.os_release
            return self.os_release
        if "docker ps" in cmd:
            if isinstance(self.docker_ps, Exception):
                raise self.docker_ps
            return self.docker_ps
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
    comps = SshHostCollector("oracle", ssh, "ubuntu", "1.2.3.4", True, "backup-cmd", 36).collect(resolver, NOW)
    by_id = {c.id: c for c in comps}
    assert set(by_id) == {"oracle:os", "oracle:docker:alloy", "oracle:docker:web",
                          "oracle:docker:edge", "oracle:backup"}
    assert by_id["oracle:os"].current == "22.04" and by_id["oracle:os"].status == "outdated"
    assert by_id["oracle:os"].group == "core" and by_id["oracle:os"].kind == "host_os"
    assert by_id["oracle:docker:alloy"].current == "v1.3.0"
    assert by_id["oracle:docker:alloy"].status == "outdated"
    assert by_id["oracle:docker:edge"].status == "unknown"        # tag 'latest'
    assert by_id["oracle:backup"].status == "ok"
    assert all(c.host == "oracle" for c in comps)
    assert all(c.group == "app" for c in comps if c.id != "oracle:os")


def test_docker_false_skips_docker():
    comps = SshHostCollector("h", FakeSsh(), "u", "a", False, None, 36).collect(FakeResolver(), NOW)
    assert not any(c.id.startswith("h:docker") for c in comps)


def test_no_backup_cmd_skips_backup():
    comps = SshHostCollector("h", FakeSsh(), "u", "a", False, None, 36).collect(FakeResolver(), NOW)
    assert "h:backup" not in {c.id for c in comps}


def test_bad_backup_output_becomes_error_component():
    for bad in ("garbage\n", SshError("boom")):
        comps = SshHostCollector("h", FakeSsh(backup=bad), "u", "a", False, "backup-cmd", 36).collect(FakeResolver(), NOW)
        b = next(c for c in comps if c.id == "h:backup")
        assert b.status == "error"


def test_os_release_ssh_failure_becomes_error_component():
    comps = SshHostCollector("h", FakeSsh(os_release=SshError("connection failed")),
                             "u", "a", False, None, 36).collect(FakeResolver(), NOW)
    c = next(c for c in comps if c.id == "h:os")
    assert c.status == "error" and "connection failed" in c.error


def test_docker_ps_failure_becomes_error_component_without_killing_os():
    comps = SshHostCollector("h", FakeSsh(docker_ps=SshError("boom")),
                             "u", "a", True, None, 36).collect(FakeResolver(), NOW)
    by_id = {c.id: c for c in comps}
    assert by_id["h:os"].status != "error"
    assert by_id["h:docker"].status == "error" and "boom" in by_id["h:docker"].error
