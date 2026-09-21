import logging

log = logging.getLogger(__name__)


def build_status(collectors, resolver, previous, now):
    iso = now.isoformat(timespec="seconds")
    prev = {c["id"]: c for c in (previous or {}).get("components", [])}
    components, hosts = [], {}
    for col in collectors:
        try:
            fresh = col.collect(resolver, now)
        except Exception as exc:
            log.exception("collector %s failed", col.host)
            msg = str(exc)[:300]
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
