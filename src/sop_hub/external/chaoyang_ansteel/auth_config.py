"""鞍钢门户认证 —— 凭据 + 加密 单一来源(2026-06-17 去脆化)。

背景:`login.py` 原来**重放** 2026-06-02 抓包的两段固定 RSA 密文
(CAPTURED_ENCRYPTED_*)。能跑通只是因为服务端没把密文绑时间戳/nonce;
一旦加防重放,朝钢上传会**静默失效**。本模块:
  1. 把账号/密码/抓包密文集中一处(原来散在 login.py),env 可覆盖;
  2. 提供**实时 RSA 加密**(encrypt_credential)—— 配上公钥即每次新密文,根治重放;
  3. 默认行为不变:没配公钥 → 仍用抓包密文(保住当前能跑的链路)。

要彻底去脆化,只差一个输入:**鞍钢前端的 RSA 公钥**(在登录页前端 JS 里,
JSEncrypt.setPublicKey 那个)。拿到后:
  - 放到环境变量 ANSTEEL_RSA_PUBKEY(PEM),或
  - 写进下面 RSA_PUBLIC_KEY_PEM。
之后 login 自动走实时加密,不再重放。见 docs/改造清单-20260617.md #7。

用户已确认:货运系统,账号密码明文硬编码 OK。
"""
from __future__ import annotations

import base64
import os

# ── 账号(env 可覆盖)─────────────────────────────────────────────
USERNAME = os.getenv("ANSTEEL_USER", "CWL20085")
PASSWORD = os.getenv("ANSTEEL_PASS", "Zhufeng.123")

# ── 抓包密文(重放兜底;配了公钥就不用它)──────────────────────────
CAPTURED_ENCRYPTED_USERNAME = (
    "nkiCFDpIyjBOAb+BjznGsgDcK94oo3Vk3EPuhn9gFjHFQ+HirdVREWPzXn8Sl4MN"
    "kJhrLADWyz4r3V5WKB4KhS+OHAf3toEvcd0CjeJ2+kDWsJOxvQa2ez4sP5iejPl1"
    "bYuOHRupwad3u0TWWMpGpDdZzHSDS78T53v3cloLXEM="
)
CAPTURED_ENCRYPTED_PASSWORD = (
    "itAR9fVHzSMjV+fxKWiBtLXETvN1qPGSyVxwVkT2xWCVlQGOt1QJ5PaTGLSm6VVx"
    "aEyW930bHN2a80QIdMu+bmWSfemhf4oOkTNEvgGazlJBbFteyPWl1iWbtCr9JOlA"
    "kNHEoL+nwD6NXw9K5650zE8zENjzxd6z0GO2KHRFxpQ="
)

# ── 实时加密用的 RSA 公钥 ────────────────────────────────────────────
# 2026-06-17 从鞍钢登录页前端 JS(app.f2a5a6ca.js 的 setPublicKey)提取,
# 实时加密登录已实测 flag=0 成功(朱峰/鞍钢汽车运输)→ **默认走实时加密,重放退役**。
# env ANSTEEL_RSA_PUBKEY 可覆盖(门户换钥时改 env,不必动码)。
_DEFAULT_ANSTEEL_PUBKEY = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCq2eyKwS0nzbzyS05aqw8ljxEo\n"
    "EEaIRpZqLImLYp7UehRfmQFpBm/xUOsPGhY72GWiZNlETFgihzU1e676etdrpU0L\n"
    "YIzaxnLnjELlsIiCEQ0Qbwxz1Xltan3+f+CC0CGvw5C9WAytNDjpxc10dN16P4ZG\n"
    "zl8+QNDZYrekcv7i7wIDAQAB\n"
    "-----END PUBLIC KEY-----"
)
RSA_PUBLIC_KEY_PEM = os.getenv("ANSTEEL_RSA_PUBKEY", _DEFAULT_ANSTEEL_PUBKEY)

# 登录公司域(鞍钢母公司)
COMPANY_CODE = "00020001"
COMPANY_NAME = "鞍钢股份有限公司"
APP_NAME = "AGMPM1"


def _normalize_pubkey(pem_or_b64: str) -> str:
    """容错:接受完整 PEM,或裸 base64(JSEncrypt 常给裸 base64 DER)→ 包成 PEM。"""
    s = (pem_or_b64 or "").strip()
    if not s:
        return ""
    if "BEGIN PUBLIC KEY" in s:
        return s
    body = "\n".join(s[i:i + 64] for i in range(0, len(s), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----"


def encrypt_credential(plaintext: str, public_key_pem: str = "") -> str:
    """用 RSA 公钥实时加密(PKCS#1 v1.5,对齐前端 JSEncrypt 默认)→ base64。

    public_key_pem 缺省读 RSA_PUBLIC_KEY_PEM。公钥为空抛 ValueError —— 调用方
    据此回退到抓包密文。
    """
    pem = _normalize_pubkey(public_key_pem or RSA_PUBLIC_KEY_PEM)
    if not pem:
        raise ValueError("no RSA public key configured (ANSTEEL_RSA_PUBKEY)")
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(pem.encode("utf-8"))
    ct = key.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ct).decode("ascii")


def resolve_login_credentials() -> tuple[str, str, str]:
    """返回 (encrypted_username, encrypted_password, mode)。

    有公钥 → 实时加密(mode='live');否则抓包重放(mode='replay')。
    实时加密失败也安全回退到重放,不让登录直接崩。
    """
    if _normalize_pubkey(RSA_PUBLIC_KEY_PEM):
        try:
            return (encrypt_credential(USERNAME), encrypt_credential(PASSWORD), "live")
        except Exception:
            pass  # 回退重放
    return (CAPTURED_ENCRYPTED_USERNAME, CAPTURED_ENCRYPTED_PASSWORD, "replay")
