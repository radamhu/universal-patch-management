import re

_NUM = re.compile(r"\d+(?:\.\d+)*")


def parse(v):
    if not v:
        return None
    m = _NUM.search(v)
    return tuple(int(p) for p in m.group().split(".")) if m else None


def status_for(current, latest):
    c, l = parse(current), parse(latest)
    if c is None or l is None:
        return "unknown"
    if len(c) < len(l):
        l = l[: len(c)]
    return "ok" if c >= l else "outdated"


def backup_status(last_ts, now_ts, max_age_h):
    if last_ts is None:
        return "unknown"
    return "ok" if now_ts - last_ts <= max_age_h * 3600 else "outdated"
