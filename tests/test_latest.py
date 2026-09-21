import httpx
from upm.latest import LatestResolver


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_endoflife_lts_cycle():
    def h(req):
        assert req.url.path == "/api/ubuntu.json"
        return httpx.Response(200, json=[
            {"cycle": "25.10", "lts": False, "latest": "25.10"},
            {"cycle": "24.04", "lts": True, "latest": "24.04.3"}])
    r = LatestResolver(client(h), {"os:ubuntu": {"source": "endoflife", "product": "ubuntu",
                                                 "lts": True, "field": "cycle"}})
    assert r.latest("os:ubuntu") == "24.04"


def test_endoflife_default_field():
    def h(req):
        return httpx.Response(200, json=[{"cycle": "8", "latest": "8.3.1"}])
    r = LatestResolver(client(h), {"proxmox": {"source": "endoflife", "product": "proxmox-ve"}})
    assert r.latest("proxmox") == "8.3.1"


def test_github_release_strips_v():
    def h(req):
        assert req.url.path == "/repos/grafana/alloy/releases/latest"
        return httpx.Response(200, json={"tag_name": "v1.4.2"})
    r = LatestResolver(client(h), {"app:grafana/alloy": {"source": "github_release",
                                                         "repo": "grafana/alloy"}})
    assert r.latest("app:grafana/alloy") == "1.4.2"


def test_dockerhub_picks_highest_numeric_tag():
    def h(req):
        return httpx.Response(200, json={"results": [
            {"name": "latest"}, {"name": "1.9.0"}, {"name": "1.10.2"}, {"name": "1.10.2-alpine"}]})
    r = LatestResolver(client(h), {"app:nginx": {"source": "dockerhub_tag", "repo": "library/nginx"}})
    assert r.latest("app:nginx") == "1.10.2"


def test_manual_and_unknown():
    r = LatestResolver(client(lambda q: httpx.Response(500)),
                       {"x": {"source": "manual", "version": "3.1"}})
    assert r.latest("x") == "3.1"
    assert r.latest("nope") is None


def test_cache_and_stale_fallback():
    calls = []
    now = [0.0]

    def h(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=[{"cycle": "1", "latest": "1.0"}])
        return httpx.Response(500)

    r = LatestResolver(client(h), {"k": {"source": "endoflife", "product": "p"}},
                       ttl=100, clock=lambda: now[0])
    assert r.latest("k") == "1.0"
    now[0] = 50
    assert r.latest("k") == "1.0" and len(calls) == 1   # cached
    now[0] = 500
    assert r.latest("k") == "1.0" and len(calls) == 2   # refresh failed, stale value kept


def test_failure_without_cache_returns_none():
    r = LatestResolver(client(lambda q: httpx.Response(500)),
                       {"k": {"source": "github_release", "repo": "a/b"}})
    assert r.latest("k") is None
