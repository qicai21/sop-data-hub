"""VLM 服务调用统一入口:按 service_url 自动适配接口。

- 含 ``/v1`` → OpenAI 兼容(mlx_vlm.server,如 35B@8021):base64 图 +
  /v1/chat/completions,带 ``enable_thinking=false`` 关思维链(避免思维链污染 JSON)。
- 否则 → 旧自定义 ``/generate``(VL-8B@8018):传本地 image_path。

返回模型输出的纯文本(两种接口都归一为 str),下游解析逻辑不变。
"""
from __future__ import annotations

import base64
from pathlib import Path

import requests


def call_vlm(
    service_url: str,
    prompt: str,
    image_path: str | Path,
    max_tokens: int,
    *,
    openai_model: str | None = None,
    temperature: float = 0.0,
    timeout: int = 240,
) -> str:
    if "/v1" in service_url:
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        resp = requests.post(
            service_url,
            json={
                "model": openai_model or str(image_path),
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ]}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "enable_thinking": False,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""
    resp = requests.post(
        service_url,
        json={
            "prompt": prompt,
            "image_path": str(image_path),
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("text", "")
