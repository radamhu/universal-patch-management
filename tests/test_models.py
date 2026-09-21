from fakes import NOW
from upm.models import Component, backup_component, error_component, version_component


def test_version_component_outdated():
    c = version_component(id="pve01:proxmox", group="core", host="pve01", kind="proxmox",
                          name="Proxmox VE", current="8.2.4", latest="8.3.1", now=NOW)
    assert c.status == "outdated"
    assert c.checked_at == "2026-09-21T12:00:00+00:00"
    assert c.stale is False and c.error is None


def test_backup_component_ok_and_missing():
    ts = int(NOW.timestamp()) - 3600
    c = backup_component(id="pve01:backup:100", group="core", host="pve01",
                         name="Backup web", last_ts=ts, max_age_h=36, now=NOW)
    assert c.kind == "backup" and c.status == "ok"
    assert c.current == "2026-09-21T11:00:00+00:00"
    m = backup_component(id="x", group="core", host="pve01", name="n",
                         last_ts=None, max_age_h=36, now=NOW)
    assert m.status == "unknown" and m.current is None


def test_error_component_roundtrip():
    c = error_component(id="probe:a", group="app", host="h", kind="lxc_app",
                        name="a", error="boom", now=NOW)
    assert c.status == "error" and c.error == "boom" and c.current is None
    assert Component.from_dict(c.to_dict()) == c
