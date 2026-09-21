import logging

log = logging.getLogger(__name__)


def build_status(collectors, resolver, previous, now):
    iso = now.isoformat(timespec="seconds")
    # Defensively build prev map: only accept dict previous with list components
    prev = {}
    if isinstance(previous, dict):
        comps = previous.get("components")
        if isinstance(comps, list):
            for c in comps:
                if isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("status"), str) and isinstance(c.get("host"), str):
                    prev[c["id"]] = c
    components, hosts = [], {}
    for col in collectors:
        try:
            fresh = col.collect(resolver, now)
        except Exception as exc:
            log.exception("collector %s failed", col.host)
            msg = f"{type(exc).__name__}: {exc}"[:300]
            hosts[col.host] = {"ok": False, "error": msg, "checked_at": iso}
            components += [{**old, "stale": True, "error": msg}
                           for old in prev.values() if old["id"].startswith(f"{col.host}:")]
            continue
        hosts[col.host] = {"ok": True, "error": None, "checked_at": iso}
        for c in fresh:
            d = c.to_dict()
            old = prev.get(d["id"])
            if d["status"] == "error" and old and old["status"] != "error":
                d = {**old, "stale": True, "error": d["error"]}
            components.append(d)
    return {"generated_at": iso, "hosts": hosts, "components": components}
