"""守门:鞍钢门户认证去脆化 —— replay 默认不变 + 实时加密机制正确(2026-06-17)。"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from sop_hub.external.chaoyang_ansteel import auth_config


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv, pub_pem


def test_default_is_replay_unchanged():
    """没配公钥 → 用抓包密文重放(保住当前能跑的链路)。"""
    u, p, mode = auth_config.resolve_login_credentials()
    assert mode == "replay"
    assert u == auth_config.CAPTURED_ENCRYPTED_USERNAME
    assert p == auth_config.CAPTURED_ENCRYPTED_PASSWORD


def test_live_encryption_roundtrips():
    """配公钥 → 实时加密;密文能用私钥解回明文(证明加密对路)。"""
    priv, pub_pem = _keypair()
    ct = auth_config.encrypt_credential("CWL20085", pub_pem)
    back = priv.decrypt(base64.b64decode(ct), padding.PKCS1v15()).decode()
    assert back == "CWL20085"


def test_bare_base64_pubkey_normalized():
    """JSEncrypt 常给裸 base64 → _normalize 兜成 PEM 也能加密。"""
    priv, pub_pem = _keypair()
    raw = (pub_pem.replace("-----BEGIN PUBLIC KEY-----", "")
           .replace("-----END PUBLIC KEY-----", "").replace("\n", ""))
    ct = auth_config.encrypt_credential("Zhufeng123!", raw)
    back = priv.decrypt(base64.b64decode(ct), padding.PKCS1v15()).decode()
    assert back == "Zhufeng123!"


def test_resolve_uses_live_when_key_present(monkeypatch):
    priv, pub_pem = _keypair()
    monkeypatch.setattr(auth_config, "RSA_PUBLIC_KEY_PEM", pub_pem)
    u, p, mode = auth_config.resolve_login_credentials()
    assert mode == "live"
    assert u != auth_config.CAPTURED_ENCRYPTED_USERNAME


def test_encrypt_without_key_raises():
    import pytest
    with pytest.raises(ValueError):
        auth_config.encrypt_credential("x", "")
