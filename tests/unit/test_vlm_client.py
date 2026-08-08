from pathlib import Path

from sop_hub.vlm_client import OPENAI_VLM_MIN_TEMPERATURE, call_vlm


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}], "text": "legacy"}


def test_openai_vlm_zero_temperature_uses_nonzero_floor(monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"fake-image")
    captured = {}

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("sop_hub.vlm_client.requests.post", fake_post)

    text = call_vlm(
        "http://127.0.0.1:8021/v1/chat/completions",
        "prompt",
        img,
        128,
        openai_model="/model",
    )

    assert text == "ok"
    assert captured["json"]["temperature"] == OPENAI_VLM_MIN_TEMPERATURE
    assert captured["json"]["enable_thinking"] is False


def test_openai_vlm_keeps_explicit_nonzero_temperature(monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"fake-image")
    captured = {}

    def fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("sop_hub.vlm_client.requests.post", fake_post)

    call_vlm(
        "http://127.0.0.1:8021/v1/chat/completions",
        "prompt",
        img,
        128,
        temperature=0.2,
    )

    assert captured["json"]["temperature"] == 0.2


def test_legacy_vlm_keeps_zero_temperature(monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"fake-image")
    captured = {}

    def fake_post(url, *, json, timeout):
        captured["json"] = json
        return _FakeResponse()

    monkeypatch.setattr("sop_hub.vlm_client.requests.post", fake_post)

    text = call_vlm("http://127.0.0.1:8018/generate", "prompt", Path(img), 128)

    assert text == "legacy"
    assert captured["json"]["temperature"] == 0.0
