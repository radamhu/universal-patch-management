import subprocess


class SshError(Exception):
    pass


class SshRunner:
    def __init__(self, key_path, known_hosts, run=subprocess.run, timeout=20):
        self._key, self._known_hosts = key_path, known_hosts
        self._run, self._timeout = run, timeout

    def run(self, user, host, command):
        args = ["ssh", "-i", self._key,
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={self._known_hosts}",
                "-o", "ConnectTimeout=10",
                f"{user}@{host}", command]
        try:
            p = self._run(args, capture_output=True, text=True, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            raise SshError(f"ssh {host} timed out")
        if p.returncode != 0:
            raise SshError(f"ssh {host} exit {p.returncode}: {p.stderr.strip()[:200]}")
        return p.stdout
