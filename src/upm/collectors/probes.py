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
                latest = resolver.latest(p["latest_key"]) if p.get("latest_key") else None
                comps.append(version_component(
                    id=p["id"], group=p["group"], host=p["host"], kind=p["kind"],
                    name=p["name"], current=m.group(1), latest=latest, now=now))
            except (SshError, ValueError, re.error, IndexError, KeyError) as exc:
                comps.append(error_component(
                    id=p["id"], group=p["group"], host=p["host"], kind=p["kind"],
                    name=p["name"], error=str(exc)[:200], now=now))
        return comps
