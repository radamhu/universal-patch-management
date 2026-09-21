from pathlib import Path

from fakes import NOW, FakeResolver
from upm.config import Components, Settings
from upm.main import build_collectors, poll_once
from upm.store import Store


def settings(env):
    return Settings.from_env(env)


def test_build_collectors_by_config():
    assert build_collectors(settings({}), Components(), None, None) == []
    env = {"PVE01_HOST": "h", "PVE01_HOST_USER": "root@pam", "PVE01_HOST_TOKEN": "t",
           "PVE01_HOST_TOKEN_SECRET": "s", "ORACLE_SSH_HOST": "o", "ORACLE_SSH_USER": "u"}
    probe = {"id": "probe:a"}
    cols = build_collectors(settings(env), Components(probes=[probe]), object(), object())
    assert [c.host for c in cols] == ["pve01", "oracle", "probe"]


def test_poll_once_saves_and_survives_failure(tmp_path):
    class Boom:
        host = "z"

        def collect(self, r, n):
            raise RuntimeError("x")

    store = Store(tmp_path)
    poll_once([Boom()], FakeResolver(), store, NOW)
    doc = store.load()
    assert doc["hosts"]["z"]["ok"] is False
