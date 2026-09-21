import json

import pytest
from upm.config import Settings, load_components, normalize_pve_host


@pytest.mark.parametrize("raw,url,host", [
    ("10.0.0.5", "https://10.0.0.5:8006", "10.0.0.5"),
    ("pve01.lan:8007", "https://pve01.lan:8007", "pve01.lan"),
    ("https://pve01.lan", "https://pve01.lan:8006", "pve01.lan"),
])
def test_normalize_pve_host(raw, url, host):
    assert normalize_pve_host(raw) == (url, host)


def test_settings_from_env_full():
    s = Settings.from_env({
        "PVE01_HOST": "10.0.0.5", "PVE01_HOST_USER": "root@pam",
        "PVE01_HOST_TOKEN": "upm", "PVE01_HOST_TOKEN_SECRET": "sekret",
        "ORACLE_SSH_HOST": "1.2.3.4", "ORACLE_SSH_USER": "ubuntu",
        "POLL_INTERVAL": "5",
    })
    assert s.pve_base_url == "https://10.0.0.5:8006"
    assert s.pve_auth == "PVEAPIToken=root@pam!upm=sekret"
    assert s.pve_verify_tls is False
    assert s.oracle_host == "1.2.3.4" and s.oracle_user == "ubuntu"
    assert s.poll_interval_min == 5 and s.pve_ssh_user == "root"


def test_settings_token_with_full_id_and_empty_env():
    s = Settings.from_env({
        "PVE01_HOST": "h", "PVE01_HOST_USER": "root@pam",
        "PVE01_HOST_TOKEN": "root@pam!full", "PVE01_HOST_TOKEN_SECRET": "x",
    })
    assert s.pve_auth == "PVEAPIToken=root@pam!full=x"
    empty = Settings.from_env({})
    assert empty.pve_base_url is None and empty.oracle_host is None
    assert empty.poll_interval_min == 30 and empty.port == 8080


def test_load_components(tmp_path):
    assert load_components(tmp_path / "missing.json").probes == []
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "sources": {"proxmox": {"source": "manual", "version": "9"}},
        "probes": [{"id": "probe:a", "name": "A", "kind": "lxc_app", "group": "app",
                    "host": "pve01", "target": {"type": "pve_lxc", "vmid": 1},
                    "cmd": "x", "regex": "(\\d+)"}],
    }))
    c = load_components(p)
    assert c.sources["proxmox"]["version"] == "9" and len(c.probes) == 1


def test_load_components_rejects_bad_probe(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"probes": [{"id": "nope:a"}]}))
    with pytest.raises(ValueError):
        load_components(p)


def test_pve_auth_not_in_repr():
    s = Settings.from_env({"PVE01_HOST": "10.0.0.5", "PVE01_HOST_USER": "root@pam",
                           "PVE01_HOST_TOKEN": "upm", "PVE01_HOST_TOKEN_SECRET": "sekret"})
    assert "sekret" not in repr(s)


@pytest.mark.parametrize("key,attr,default", [
    ("POLL_INTERVAL", "poll_interval_min", 30),
    ("BACKUP_MAX_AGE_H", "backup_max_age_h", 36),
    ("PORT", "port", 8080),
])
def test_numeric_env_safe(key, attr, default):
    assert getattr(Settings.from_env({key: ""}), attr) == default
    assert getattr(Settings.from_env({key: "abc"}), attr) == default
    assert getattr(Settings.from_env({key: "0"}), attr) == 1
    assert getattr(Settings.from_env({key: "-5"}), attr) == 1
    assert getattr(Settings.from_env({key: "7"}), attr) == 7
