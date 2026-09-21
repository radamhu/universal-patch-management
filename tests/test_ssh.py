import subprocess

import pytest
from upm.ssh import SshError, SshRunner


def test_run_builds_command_and_returns_stdout():
    seen = {}

    def fake(args, **kw):
        seen["args"], seen["kw"] = args, kw
        return subprocess.CompletedProcess(args, 0, stdout="hello\n", stderr="")

    out = SshRunner("/k", "/kh", run=fake).run("ubuntu", "1.2.3.4", "uname -a")
    assert out == "hello\n"
    a = seen["args"]
    assert a[0] == "ssh" and "ubuntu@1.2.3.4" in a and a[-1] == "uname -a"
    assert "BatchMode=yes" in a and "UserKnownHostsFile=/kh" in a and "/k" in a
    assert seen["kw"]["timeout"] == 20


def test_nonzero_exit_raises():
    def fake(args, **kw):
        return subprocess.CompletedProcess(args, 255, stdout="", stderr="Permission denied")
    with pytest.raises(SshError, match="Permission denied"):
        SshRunner("/k", "/kh", run=fake).run("u", "h", "x")


def test_timeout_raises():
    def fake(args, **kw):
        raise subprocess.TimeoutExpired(args, 20)
    with pytest.raises(SshError, match="timed out"):
        SshRunner("/k", "/kh", run=fake).run("u", "h", "x")
