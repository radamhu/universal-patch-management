import logging
import re
import time

from .versions import parse

log = logging.getLogger(__name__)
_TAG = re.compile(r"^v?\d+(\.\d+)*$")


def _endoflife(client, cfg):
    r = client.get(f"https://endoflife.date/api/{cfg['product']}.json")
    r.raise_for_status()
    for row in r.json():
        if cfg.get("lts") and not row.get("lts"):
            continue
        return str(row[cfg.get("field", "latest")])
    return None


def _github_release(client, cfg):
    r = client.get(f"https://api.github.com/repos/{cfg['repo']}/releases/latest",
                   headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()["tag_name"].lstrip("v")


def _dockerhub_tag(client, cfg):
    r = client.get(f"https://hub.docker.com/v2/repositories/{cfg['repo']}/tags",
                   params={"page_size": 100, "ordering": "last_updated"})
    r.raise_for_status()
    names = [t["name"] for t in r.json()["results"] if _TAG.match(t["name"])]
    return max(names, key=parse).lstrip("v") if names else None


def _manual(client, cfg):
    return cfg["version"]


SOURCES = {"endoflife": _endoflife, "github_release": _github_release,
           "dockerhub_tag": _dockerhub_tag, "manual": _manual}


class LatestResolver:
    def __init__(self, client, mapping, ttl=21600, clock=time.monotonic):
        self._client, self._mapping = client, mapping
        self._ttl, self._clock = ttl, clock
        self._cache = {}

    def latest(self, key):
        cfg = self._mapping.get(key)
        if not cfg:
            return None
        entry = self._cache.get(key)
        if entry and self._clock() - entry[0] < self._ttl:
            return entry[1]
        try:
            value = SOURCES[cfg["source"]](self._client, cfg)
        except Exception as exc:
            log.warning("latest lookup failed for %s: %s", key, exc)
            return entry[1] if entry else None
        self._cache[key] = (self._clock(), value)
        return value
