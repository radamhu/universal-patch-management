import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .collectors.oracle import OracleCollector
from .collectors.probes import ProbesCollector
from .collectors.pve01 import Pve01Collector
from .config import Settings, load_components
from .latest import LatestResolver
from .runner import build_status
from .server import make_server
from .ssh import SshRunner
from .store import Store

log = logging.getLogger("upm")


def build_collectors(s, components, ssh, pve_client):
    cols = []
    if s.pve_base_url:
        cols.append(Pve01Collector(pve_client, s.backup_max_age_h,
                                   ssh, s.pve_ssh_user, s.pve_hostname))
    if s.oracle_host and s.oracle_user:
        cols.append(OracleCollector(ssh, s.oracle_user, s.oracle_host,
                                    s.oracle_backup_cmd, s.backup_max_age_h))
    if components.probes:
        cols.append(ProbesCollector(components.probes, ssh, s.pve_ssh_user, s.pve_hostname))
    return cols


def poll_once(collectors, resolver, store, now):
    doc = build_status(collectors, resolver, store.load(), now)
    store.save(doc)


def poll_loop(collectors, resolver, store, interval_s, stop):
    while not stop.is_set():
        try:
            poll_once(collectors, resolver, store, datetime.now(timezone.utc))
        except Exception:
            log.exception("poll failed")
        stop.wait(interval_s)


def main():
    logging.basicConfig(level=logging.INFO)
    s = Settings.from_env(os.environ)
    components = load_components(s.components_path)
    ssh = SshRunner(s.ssh_key_path, s.known_hosts_path)
    pve_client = httpx.Client(
        base_url=s.pve_base_url or "", verify=s.pve_verify_tls, timeout=15,
        headers={"Authorization": s.pve_auth or ""})
    resolver = LatestResolver(httpx.Client(timeout=15, follow_redirects=True), components.sources)
    store = Store(s.data_dir)
    collectors = build_collectors(s, components, ssh, pve_client)
    interval = s.poll_interval_min * 60
    stop = threading.Event()
    threading.Thread(target=poll_loop, args=(collectors, resolver, store, interval, stop),
                     daemon=True).start()
    static = Path(__file__).parent / "static"
    server = make_server(store, static, max_age_s=interval * 3, port=s.port)
    log.info("serving on :%d, collectors=%s", s.port, [c.host for c in collectors])
    try:
        server.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
