from ..models import backup_component, error_component, version_component
from ..ssh import SshError

DOCKER_PS = "docker ps --format '{{.Names}}|{{.Image}}'"


def parse_os_release(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"')
    return out


def split_image(image):
    name = image.partition("@")[0]
    if ":" in name.rsplit("/", 1)[-1]:
        repo, tag = name.rsplit(":", 1)
        return repo, tag
    return name, "latest"


class OracleCollector:
    host = "oracle"

    def __init__(self, ssh, user, address, backup_cmd, max_age_h):
        self._ssh, self._user, self._addr = ssh, user, address
        self._backup_cmd, self._max_age_h = backup_cmd, max_age_h

    def _run(self, cmd):
        return self._ssh.run(self._user, self._addr, cmd)

    def collect(self, resolver, now):
        osr = parse_os_release(self._run("cat /etc/os-release"))
        comps = [version_component(
            id="oracle:os", group="app", host=self.host, kind="vm_os",
            name=f"OS {osr.get('PRETTY_NAME', osr.get('ID', 'unknown'))}",
            current=osr.get("VERSION_ID"),
            latest=resolver.latest(f"os:{osr.get('ID', '')}"), now=now)]

        for line in self._run(DOCKER_PS).splitlines():
            if "|" not in line:
                continue
            name, image = line.split("|", 1)
            repo, tag = split_image(image)
            comps.append(version_component(
                id=f"oracle:docker:{name}", group="app", host=self.host,
                kind="docker_app", name=f"{name} ({repo})", current=tag,
                latest=resolver.latest(f"app:{repo}"), now=now))

        if self._backup_cmd:
            try:
                ts = int(self._run(self._backup_cmd).strip())
                comps.append(backup_component(
                    id="oracle:backup", group="app", host=self.host, name="Backup oracle",
                    last_ts=ts, max_age_h=self._max_age_h, now=now))
            except (SshError, ValueError) as exc:
                comps.append(error_component(
                    id="oracle:backup", group="app", host=self.host, kind="backup",
                    name="Backup oracle", error=str(exc)[:200], now=now))
        return comps
