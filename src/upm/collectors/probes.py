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
                version = m.group(1)
                if version is None:
                    raise ValueError("regex group 1 did not match")
                latest = resolver.latest(p["latest_key"]) if p.get("latest_key") else None
                comps.append(version_component(
                    id=p["id"], group=p["group"], host=p["host"], kind=p["kind"],
                    name=p["name"], current=version, latest=latest, now=now))
            except Exception as exc:
                comps.append(error_component(
                    id=p.get("id", "probe:unknown"),
                    group=p.get("group", "app"),
                    host=p.get("host", "unknown"),
                    kind=p.get("kind", "unknown"),
                    name=p.get("name", p.get("id", "unknown")),
                    error=str(exc)[:200], now=now))
        return comps
