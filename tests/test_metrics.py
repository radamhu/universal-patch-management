from upm.metrics import render_prometheus


def _doc(components, generated_at="2026-09-28T12:00:00+00:00"):
    return {"generated_at": generated_at, "hosts": {}, "components": components}


def test_none_doc_renders_empty():
    assert render_prometheus(None) == b""


def test_no_components_still_emits_freshness():
    body = render_prometheus(_doc([]))
    assert b"upm_component_status{" not in body
    assert b"upm_generated_timestamp_seconds 1790596800.0" in body


def test_component_series_rendered():
    components = [
        {"id": "pve01:os", "group": "core", "host": "pve01", "kind": "os",
         "name": "Proxmox VE", "current": "8.3.1", "latest": "8.3.1",
         "status": "ok", "checked_at": "2026-09-28T12:00:00+00:00"},
        {"id": "pve01:lxc100:nginx", "group": "app", "host": "pve01", "kind": "docker",
         "name": "nginx", "current": "1.24", "latest": "1.27", "status": "outdated",
         "checked_at": "2026-09-28T12:00:00+00:00"},
    ]
    body = render_prometheus(_doc(components)).decode()
    assert ('upm_component_status{id="pve01:os",group="core",host="pve01",kind="os",'
            'name="Proxmox VE",current="8.3.1",latest="8.3.1",status="ok",stale="false"} 1') in body
    assert ('upm_component_status{id="pve01:lxc100:nginx",group="app",host="pve01",'
            'kind="docker",name="nginx",current="1.24",latest="1.27",status="outdated",'
            'stale="false"} 1') in body


def test_stale_component_rendered_as_true():
    components = [
        {"id": "pve01:os", "group": "core", "host": "pve01", "kind": "os",
         "name": "Proxmox VE", "current": "8.3.1", "latest": "8.3.1",
         "status": "ok", "checked_at": "2026-09-28T12:00:00+00:00", "stale": True},
        {"id": "pve01:lxc100:nginx", "group": "app", "host": "pve01", "kind": "docker",
         "name": "nginx", "current": "1.24", "latest": "1.27", "status": "outdated",
         "checked_at": "2026-09-28T12:00:00+00:00", "stale": False},
    ]
    body = render_prometheus(_doc(components)).decode()
    lines = [l for l in body.splitlines() if l.startswith("upm_component_status{")]
    assert 'id="pve01:os"' in lines[0] and 'stale="true"' in lines[0]
    assert 'id="pve01:lxc100:nginx"' in lines[1] and 'stale="false"' in lines[1]


def test_stale_key_omitted_renders_as_false():
    components = [{"id": "x:y", "group": "app", "host": "x", "kind": "docker",
                   "name": "y", "current": "1.0", "latest": "1.0", "status": "ok",
                   "checked_at": "2026-09-28T12:00:00+00:00"}]
    body = render_prometheus(_doc(components)).decode()
    assert 'stale="false"' in body


def test_none_current_latest_render_as_empty_string():
    components = [{"id": "x:y", "group": "app", "host": "x", "kind": "docker",
                   "name": "y", "current": None, "latest": None, "status": "unknown",
                   "checked_at": "2026-09-28T12:00:00+00:00"}]
    body = render_prometheus(_doc(components)).decode()
    assert 'current="",latest=""' in body
    assert "None" not in body


def test_label_values_escaped():
    components = [{"id": "x:y", "group": "app", "host": "x", "kind": "docker",
                   "name": 'weird "name"\\with\\backslash', "current": "1.0",
                   "latest": "1.0", "status": "ok", "checked_at": "2026-09-28T12:00:00+00:00"}]
    body = render_prometheus(_doc(components)).decode()
    assert 'name="weird \\"name\\"\\\\with\\\\backslash"' in body


def test_malformed_generated_at_omits_freshness_series():
    body = render_prometheus(_doc([], generated_at="not-a-date"))
    assert b"upm_generated_timestamp_seconds" not in body
    body2 = render_prometheus({"hosts": {}, "components": []})
    assert b"upm_generated_timestamp_seconds" not in body2
