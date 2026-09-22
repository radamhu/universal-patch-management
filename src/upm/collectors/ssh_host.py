from ..models import backup_component, error_component, version_component

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


class SshHostCollector:
    def __init__(self, host_id, ssh, user, address, docker, backup_cmd, max_age_h):
        self.host = host_id
        self._ssh, self._user, self._addr = ssh, user, address
        self._docker, self._backup_cmd, self._max_age_h = docker, backup_cmd, max_age_h

    def _run(self, cmd):
        return self._ssh.run(self._user, self._addr, cmd)

    def collect(self, resolver, now):
        comps = []

        try:
            real_host = self._run("hostname").strip() or self.host
        except Exception:
            real_host = self.host

        try:
            osr = parse_os_release(self._run("cat /etc/os-release"))
            comps.append(version_component(
                id=f"{self.host}:os", group="core", host=real_host, kind="host_os",
                name=f"OS {osr.get('PRETTY_NAME', osr.get('ID', 'unknown'))}",
                current=osr.get("VERSION_ID"),
                latest=resolver.latest(f"os:{osr.get('ID', '')}"), now=now))
        except Exception as exc:
            comps.append(error_component(
                id=f"{self.host}:os", group="core", host=real_host, kind="host_os",
                name="OS", error=str(exc)[:200], now=now))

        if self._docker:
            try:
                for line in self._run(DOCKER_PS).splitlines():
                    if "|" not in line:
                        continue
                    name, image = line.split("|", 1)
                    repo, tag = split_image(image)
                    comps.append(version_component(
                        id=f"{self.host}:docker:{name}", group="app", host=real_host,
                        kind="docker_app", name=f"{name} ({repo})", current=tag,
                        latest=resolver.latest(f"app:{repo}"), now=now))
            except Exception as exc:
                comps.append(error_component(
                    id=f"{self.host}:docker", group="app", host=real_host, kind="docker_app",
                    name="Docker", error=str(exc)[:200], now=now))

        if self._backup_cmd:
            try:
                ts = int(self._run(self._backup_cmd).strip())
                comps.append(backup_component(
                    id=f"{self.host}:backup", group="app", host=real_host, name=f"Backup {self.host}",
                    last_ts=ts, max_age_h=self._max_age_h, now=now))
            except Exception as exc:
                comps.append(error_component(
                    id=f"{self.host}:backup", group="app", host=real_host, kind="backup",
                    name=f"Backup {self.host}", error=str(exc)[:200], now=now))
        return comps
