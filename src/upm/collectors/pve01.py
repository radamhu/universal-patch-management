import httpx

from ..models import backup_component, version_component


class Pve01Collector:
    host = "pve01"

    def __init__(self, client, max_age_h):
        self._client, self._max_age_h = client, max_age_h

    def _get(self, path, params=None):
        r = self._client.get("/api2/json" + path, params=params)
        r.raise_for_status()
        return r.json()["data"]

    def collect(self, resolver, now):
        comps = [version_component(
            id="pve01:proxmox", group="core", host=self.host, kind="proxmox",
            name="Proxmox VE", current=self._get("/version")["version"],
            latest=resolver.latest("proxmox"), now=now)]

        guests = {str(r["vmid"]): r["name"]
                  for r in self._get("/cluster/resources", {"type": "vm"})
                  if not r.get("template")}
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
                name=f"Backup {guests[vmid]} ({vmid})",
                last_ts=newest.get(vmid), max_age_h=self._max_age_h, now=now))
        return comps
