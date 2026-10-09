from __future__ import annotations

from sop_hub.sop import factory_http


def test_portal_request_disables_ambient_proxy(monkeypatch):
    seen: dict[str, object] = {}

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, method, url, **kwargs):
            seen.update(method=method, url=url, trust_env=self.trust_env, kwargs=kwargs)
            return "response"

    monkeypatch.setattr(factory_http.requests, "Session", FakeSession)

    assert factory_http.request("GET", "http://portal.example/list", timeout=30) == "response"
    assert seen["method"] == "GET"
    assert seen["url"] == "http://portal.example/list"
    assert seen["trust_env"] is False
