import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PROBE_REQUIRED = ("id", "name", "kind", "group", "host", "target", "cmd", "regex")


def normalize_pve_host(raw):
    url = raw if "://" in raw else f"https://{raw}"
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}:{p.port or 8006}", p.hostname


def _int(env, key, default, minimum=1):
    try:
        return max(minimum, int(env.get(key) or default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    pve_base_url: str | None
    pve_hostname: str | None
    pve_auth: str | None = field(repr=False)
    pve_verify_tls: bool
    pve_ssh_user: str
    oracle_host: str | None
    oracle_user: str | None
    oracle_backup_cmd: str | None
    ssh_key_path: str
    known_hosts_path: str
    poll_interval_min: int
    backup_max_age_h: int
    data_dir: Path
    components_path: Path
    port: int

    @classmethod
    def from_env(cls, env):
        base = hostname = auth = None
        if env.get("PVE01_HOST"):
            base, hostname = normalize_pve_host(env["PVE01_HOST"])
            user = env.get("PVE01_HOST_USER", "")
            token = env.get("PVE01_HOST_TOKEN", "")
            token_id = token if "!" in token else f"{user}!{token}"
            auth = f"PVEAPIToken={token_id}={env.get('PVE01_HOST_TOKEN_SECRET', '')}"
        data_dir = Path(env.get("DATA_DIR", "/data"))
        return cls(
            pve_base_url=base,
            pve_hostname=hostname,
            pve_auth=auth,
            pve_verify_tls=env.get("PVE_VERIFY_TLS", "false").lower() == "true",
            pve_ssh_user=env.get("PVE01_SSH_USER", "root"),
            oracle_host=env.get("ORACLE_SSH_HOST") or None,
            oracle_user=env.get("ORACLE_SSH_USER") or None,
            oracle_backup_cmd=env.get("ORACLE_BACKUP_CMD") or None,
            ssh_key_path=env.get("SSH_KEY_PATH", "/run/secrets/upm_ssh_key"),
            known_hosts_path=env.get("KNOWN_HOSTS_PATH", str(data_dir / "known_hosts")),
            poll_interval_min=_int(env, "POLL_INTERVAL", 30),
            backup_max_age_h=_int(env, "BACKUP_MAX_AGE_H", 36),
            data_dir=data_dir,
            components_path=Path(env.get("COMPONENTS_PATH", "/app/components.json")),
            port=_int(env, "PORT", 8080),
        )


@dataclass
class Components:
    sources: dict = field(default_factory=dict)
    probes: list = field(default_factory=list)


def load_components(path):
    path = Path(path)
    if not path.exists():
        return Components()
    raw = json.loads(path.read_text())
    probes = raw.get("probes", [])
    for p in probes:
        missing = [k for k in PROBE_REQUIRED if k not in p]
        if missing:
            raise ValueError(f"probe {p.get('id', '?')}: missing {missing}")
        if not p["id"].startswith("probe:"):
            raise ValueError(f"probe id must start with 'probe:': {p['id']}")
    return Components(sources=raw.get("sources", {}), probes=probes)
