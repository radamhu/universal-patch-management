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


def test_vmid_none_becomes_error():
    """Probe with vmid: None on pve_lxc target -> error component."""
    bad_vmid = {**LXC, "id": "probe:bad_vmid", "target": {"type": "pve_lxc", "vmid": None}}
    c = ProbesCollector([bad_vmid], FakeSsh(), "root", "h").collect(FakeResolver(), NOW)
    assert c[0].status == "error"
    assert c[0].id == "probe:bad_vmid"


def test_missing_group_becomes_error():
    """Probe dict missing group -> error component with default."""
    no_group = {**VM, "id": "probe:no_group"}
    del no_group["group"]
    c = ProbesCollector([no_group], FakeSsh(), "root", "h").collect(FakeResolver(), NOW)
    assert c[0].status == "error"
    assert c[0].group == "app"  # default value


def test_optional_unmatched_group():
    """Regex with optional unmatched group r'(x)?y' against 'y' -> error component."""
    class FakeSshOptional(FakeSsh):
        def run(self, user, host, cmd):
            self.calls.append((user, host, cmd))
            return "version: y\n"  # matches r"(x)?y" but group 1 is None

    optional_group = {**VM, "id": "probe:optional", "regex": r"(x)?y"}
    c = ProbesCollector([optional_group], FakeSshOptional(), "root", "h").collect(FakeResolver(), NOW)
    assert c[0].status == "error"
    assert "group 1 did not match" in c[0].error


def test_mixed_list_failing_and_good():
    """Mixed list [failing probe, good probe] -> both results returned."""
    bad_regex = {**VM, "id": "probe:bad", "regex": r"(zzz)"}
    good_vm = {**VM, "id": "probe:good"}
    c = ProbesCollector([bad_regex, good_vm], FakeSsh(), "root", "h").collect(FakeResolver(), NOW)
    assert len(c) == 2
    by_id = {comp.id: comp for comp in c}
    assert by_id["probe:bad"].status == "error"
    assert by_id["probe:good"].status == "unknown"
    assert by_id["probe:good"].current == "2.3.4"
