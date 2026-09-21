import pytest
from upm.versions import backup_status, parse, status_for


@pytest.mark.parametrize("cur,lat,exp", [
    ("8.2.4", "8.3.1", "outdated"),
    ("8.3.1", "8.3.1", "ok"),
    ("8.4", "8.3.1", "ok"),
    ("24.04", "24.04.3", "ok"),      # current less specific: compare on its precision
    ("24.04", "26.04", "outdated"),
    ("latest", "1.2.3", "unknown"),
    ("v1.2.3", "1.2.3", "ok"),
    ("1.2.3-alpine", "1.2.4", "outdated"),
    (None, "1.0", "unknown"),
    ("1.0", None, "unknown"),
])
def test_status_for(cur, lat, exp):
    assert status_for(cur, lat) == exp


def test_parse():
    assert parse("v10.2.1-x") == (10, 2, 1)
    assert parse("nope") is None
    assert parse(None) is None


def test_backup_status():
    now = 1_000_000
    assert backup_status(None, now, 36) == "unknown"
    assert backup_status(now - 3600 * 35, now, 36) == "ok"
    assert backup_status(now - 3600 * 37, now, 36) == "outdated"
