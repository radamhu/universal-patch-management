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
