import re
import shlex
import time

import httpx

from ..models import backup_component, error_component, version_component
from .ssh_host import DOCKER_PS, split_image

_OS_ID_RE = re.compile(r'^ID=(\S+)', re.M)
_OS_VERSION_RE = re.compile(r'^VERSION_ID="?([\d.]+)', re.M)
_VZDUMP_START_RE = re.compile(r'^INFO: starting new backup job: vzdump ')
_VZDUMP_STORAGE_RE = re.compile(r'--storage (\S+)')


def _job_target_vmids(job, guests):
    excluded = {v for v in job.get("exclude", "").split(",") if v}
    if str(job.get("all", 0)) == "1":
        return {v for v in guests if v not in excluded}
    return {v for v in str(job.get("vmid", "")).split(",") if v and v not in excluded}


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
                              data={"command": ["/bin/sh", "-c", cmd]})
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

    def _docker_ps(self, guest_type, vmid, node):
        if guest_type == "lxc":
            if not (self._ssh and self._pve_hostname):
                raise RuntimeError("SSH not configured for pct exec")
            cmd = f"pct exec {vmid} -- sh -c {shlex.quote(DOCKER_PS)}"
            return self._ssh.run(self._pve_ssh_user, self._pve_hostname, cmd)
        if guest_type == "qemu":
            if not node:
                raise RuntimeError("no node for VM")
            return self._agent_exec(node, vmid, DOCKER_PS)
        raise ValueError(f"unknown guest type {guest_type}")

    def collect(self, resolver, now):
        try:
            real_host = self._get("/nodes")[0]["node"] or self.host
        except Exception:
            real_host = self.host

        comps = [version_component(
            id="pve01:proxmox", group="core", host=real_host, kind="proxmox",
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
                    id=f"pve01:{kind}:{vmid}", group="core", host=real_host, kind=kind,
                    name=name, current=version, latest=resolver.latest(f"os:{os_id}"), now=now))
            except Exception as exc:
                comps.append(error_component(
                    id=f"pve01:{kind}:{vmid}", group="core", host=real_host, kind=kind,
                    name=name, error=str(exc)[:200], now=now))

            try:
                for line in self._docker_ps(g.get("type"), vmid, g.get("node")).splitlines():
                    if "|" not in line:
                        continue
                    cname, image = line.split("|", 1)
                    repo, tag = split_image(image)
                    comps.append(version_component(
                        id=f"pve01:docker:{vmid}:{cname}", group="app", host=real_host,
                        kind="docker_app", name=f"{cname} ({repo}) [{label} {vmid}]",
                        current=tag, latest=resolver.latest(f"app:{repo}"), now=now))
            except Exception:
                pass  # guest has no docker (or agent/pct exec unavailable) - not an error

        jobs = [j for j in self._get("/cluster/backup") if str(j.get("enabled", 1)) != "0"]
        last_run = self._match_job_runs(jobs, guests)

        for job in jobs:
            job_id = job["id"]
            label = job.get("comment") or job.get("schedule") or job_id
            comps.append(backup_component(
                id=f"pve01:backupjob:{job_id}", group="core", host=real_host,
                name=f"Backup job {label}",
                last_ts=last_run.get(job_id), max_age_h=self._max_age_h, now=now))
        return comps

    def _match_job_runs(self, jobs, guests):
        """Match each vzdump job to its most recent completed task run.

        /cluster/backup carries no last-run info, and storage content
        listings can silently come back empty for storages the API can't
        index (seen in practice on a real pve01 despite successful daily
        runs) - so this correlates jobs to task history instead: each
        scheduled run's log opens with the exact vzdump invocation
        (target vmids + --storage), which is matched against each job's
        (storage, target vmids).
        """
        pending = {j["id"]: (j.get("storage"), _job_target_vmids(j, guests)) for j in jobs}
        results = {}
        for node in (n["node"] for n in self._get("/nodes")):
            if not pending:
                break
            tasks = self._get(f"/nodes/{node}/tasks", {"typefilter": "vzdump", "limit": 50})
            for t in tasks:
                if not pending or "endtime" not in t or not t.get("upid"):
                    continue
                try:
                    log = self._get(f"/nodes/{node}/tasks/{t['upid']}/log",
                                    {"start": 0, "limit": 1})
                except httpx.HTTPError:
                    continue
                if not log:
                    continue
                text = log[0].get("t", "")
                if not _VZDUMP_START_RE.match(text):
                    continue
                task_vmids = set(_VZDUMP_START_RE.sub("", text).split(" --", 1)[0].split())
                sm = _VZDUMP_STORAGE_RE.search(text)
                task_storage = sm.group(1) if sm else None
                for job_id, (storage, target_vmids) in list(pending.items()):
                    if task_storage == storage and task_vmids == target_vmids:
                        results[job_id] = t["endtime"]
                        del pending[job_id]
                        break
        return results
