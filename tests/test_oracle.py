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
    assert all(c.group == "app" and c.host == "oracle" for c in comps)


def test_no_backup_cmd_skips_backup():
    comps = OracleCollector(FakeSsh(), "u", "h", None, 36).collect(FakeResolver(), NOW)
    assert "oracle:backup" not in {c.id for c in comps}


def test_bad_backup_output_becomes_error_component():
    for bad in ("garbage\n", SshError("boom")):
        comps = OracleCollector(FakeSsh(backup=bad), "u", "h", "backup-cmd", 36).collect(FakeResolver(), NOW)
        b = next(c for c in comps if c.id == "oracle:backup")
        assert b.status == "error"
