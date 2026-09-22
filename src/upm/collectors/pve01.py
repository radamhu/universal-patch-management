import re
import shlex
import time

import httpx

from ..models import backup_component, error_component, version_component

_OS_ID_RE = re.compile(r'^ID=(\S+)', re.M)
_OS_VERSION_RE = re.compile(r'^VERSION_ID="?([\d.]+)', re.M)


def _parse_os_release(text):
    id_m, ver_m = _OS_ID_RE.search(text), _OS_VERSION_RE.search(text)
    if not id_m or not ver_m:
        raise ValueError("could not parse /etc/os-release")
    return id_m.group(1).strip('"'), ver_m.group(1)


class Pve01Collector:
    host = "pve01"

    def __init__(self, client, max_age_h, ssh=None, pve_ssh_user=None, pve_hostname=None):
        self._client, self._max_age_h = client, max_age_h
        self._ssh, self._pve_ssh_user, self._pve_hostname = ssh, pve_ssh_user, pve_hostname

    def _get(self, path, params=None):
        r = self._client.get("/api2/json" + path, params=params)
        r.raise_for_status()
        return r.json()["data"]

    def _agent_exec(self, node, vmid, cmd):
        r = self._client.post(f"/api2/json/nodes/{node}/qemu/{vmid}/agent/exec",
                              data={"command": cmd})
        r.raise_for_status()
        pid = r.json()["data"]["pid"]
        for _ in range(25):
            r = self._client.get(f"/api2/json/nodes/{node}/qemu/{vmid}/agent/exec-status",
                                 params={"pid": pid})
            r.raise_for_status()
            data = r.json()["data"]
            if data.get("exited"):
                return data.get("out-data", "")
            time.sleep(0.2)
        raise TimeoutError("guest agent exec timed out")

    def _os_release(self, guest_type, vmid, node):
        if guest_type == "lxc":
            if not (self._ssh and self._pve_hostname):
                raise RuntimeError("SSH not configured for pct exec")
            cmd = f"pct exec {vmid} -- sh -c {shlex.quote('cat /etc/os-release')}"
            return self._ssh.run(self._pve_ssh_user, self._pve_hostname, cmd)
        if guest_type == "qemu":
            if not node:
                raise RuntimeError("no node for VM")
            return self._agent_exec(node, vmid, "cat /etc/os-release")
        raise ValueError(f"unknown guest type {guest_type}")

    def collect(self, resolver, now):
        comps = [version_component(
            id="pve01:proxmox", group="core", host=self.host, kind="proxmox",
            name="Proxmox VE", current=self._get("/version")["version"],
            latest=resolver.latest("proxmox"), now=now)]

        guests = {str(r["vmid"]): r for r in self._get("/cluster/resources", {"type": "vm"})
                  if not r.get("template")}

        for vmid, g in sorted(guests.items()):
            is_lxc = g.get("type") == "lxc"
            kind = "lxc_os" if is_lxc else "vm_os"
            label = "LXC" if is_lxc else "VM"
            name = f"{label} {g.get('name', vmid)} OS ({vmid})"
            try:
                text = self._os_release(g.get("type"), vmid, g.get("node"))
                os_id, version = _parse_os_release(text)
                comps.append(version_component(
                    id=f"pve01:{kind}:{vmid}", group="core", host=self.host, kind=kind,
                    name=name, current=version, latest=resolver.latest(f"os:{os_id}"), now=now))
            except Exception as exc:
                comps.append(error_component(
                    id=f"pve01:{kind}:{vmid}", group="core", host=self.host, kind=kind,
                    name=name, error=str(exc)[:200], now=now))

        newest = {}
        for node in (n["node"] for n in self._get("/nodes")):
            for st in self._get(f"/nodes/{node}/storage", {"content": "backup"}):
                try:
                    items = self._get(f"/nodes/{node}/storage/{st['storage']}/content",
                                      {"content": "backup"})
                except httpx.HTTPError:
                    continue
                for it in items:
                    vmid = it.get("vmid")
                    ctime = it.get("ctime")
                    if vmid is not None and ctime is not None:
                        vmid = str(vmid)
                        newest[vmid] = max(newest.get(vmid, 0), ctime)

        for vmid in sorted(guests):
            comps.append(backup_component(
                id=f"pve01:backup:{vmid}", group="core", host=self.host,
                name=f"Backup {guests[vmid].get('name', vmid)} ({vmid})",
                last_ts=newest.get(vmid), max_age_h=self._max_age_h, now=now))
        return comps
