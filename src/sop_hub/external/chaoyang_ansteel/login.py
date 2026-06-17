"""鞍钢门户(朝阳钢铁收货人系统)登录 — Step 1。

URL: https://56.ansteel.com.cn/api/encryptLogin
账号: CWL20085 / 朱峰 / 鞍钢汽车运输有限责任公司

⚠️ Payload 中 UserName/Password 是 RSA 加密后的 base64 — 服务端用公钥加密、私钥解密。
   实际跑生产时,需要拿到公钥自己加密。当前阶段:先用抓包里抓到的固定密文重放,
   看能不能跑通(确认认证机制 + 拿 cookie)。如不行,就要研究加密。

业务上下文:这是朝阳钢铁项目"收货人数据上传"流程的认证入口。鞍钢汽车运输是
朝阳钢铁项目的承运方(party_b 在我们 contracts 表),用这个账号在母公司鞍钢
的门户上报告铁路发运/收货数据。

用户明确说:账号密码明文硬编码 OK(货运系统,不是金融)。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


# ── 配置(用户确认可硬编码)──────────────────────────────────────────


LOGIN_URL = "https://56.ansteel.com.cn/api/encryptLogin"

# 凭据/密文/公钥/加密 集中在 auth_config(2026-06-17 去脆化)。这里仅别名引用,
# 保持本模块原符号名不变(CLI、payload 仍用这些名字)。
from sop_hub.external.chaoyang_ansteel.auth_config import (  # noqa: E402
    APP_NAME,
    CAPTURED_ENCRYPTED_PASSWORD,
    CAPTURED_ENCRYPTED_USERNAME,
    COMPANY_CODE,
    COMPANY_NAME,
    resolve_login_credentials,
)
from sop_hub.external.chaoyang_ansteel.auth_config import (  # noqa: E402
    PASSWORD as PLAINTEXT_PASSWORD,
)
from sop_hub.external.chaoyang_ansteel.auth_config import (  # noqa: E402
    USERNAME as PLAINTEXT_USERNAME,
)

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://56.ansteel.com.cn",
    "Referer": "https://56.ansteel.com.cn/ag/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}


# ── 结果数据 ────────────────────────────────────────────────────────


@dataclass
class LoginResult:
    success: bool
    flag: int = -1            # SysInfo.Flag

    # ── 认证 token(关键!后续 API 调用要带 signature header) ──
    signature: str = ""       # response header signature
    refresh_token: str = ""   # response header refresh_token

    msg: str = ""
    user_name_cn: str = ""    # 朱峰
    user_id: int = 0
    dept_cname: str = ""      # 鞍钢汽车运输有限责任公司
    cookies: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def signature_expires_at(self) -> str:
        """signature 头里第 1 段是时间戳 YYYYMMDDHHMM。"""
        if not self.signature:
            return ""
        return self.signature.split(":", 1)[0]

    @property
    def refresh_token_expires_at(self) -> str:
        if not self.refresh_token:
            return ""
        return self.refresh_token.split(":", 1)[0]

    def to_auth_headers(self) -> dict[str, str]:
        """业务 API 调用时往 headers 里 .update(result.to_auth_headers())。"""
        h: dict[str, str] = {}
        if self.signature:
            h["signature"] = self.signature
        if self.refresh_token:
            h["refresh_token"] = self.refresh_token
        return h


# ── 主流程 ──────────────────────────────────────────────────────────


def login(
    *,
    session: requests.Session | None = None,
    encrypted_username: str | None = None,
    encrypted_password: str | None = None,
    timeout: int = 30,
) -> tuple[LoginResult, requests.Session]:
    """登录鞍钢门户,返回 (LoginResult, Session)。

    凭据来源:显式传入 > auth_config.resolve_login_credentials()
    (配了 RSA 公钥则实时加密 mode='live',否则抓包重放 mode='replay')。
    Session 已带 cookies,后续业务调用直接复用即可。
    """
    if encrypted_username is None or encrypted_password is None:
        enc_u, enc_p, auth_mode = resolve_login_credentials()
        encrypted_username = encrypted_username or enc_u
        encrypted_password = encrypted_password or enc_p
    else:
        auth_mode = "explicit"

    if session is None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)

    payload = {
        "UserName": encrypted_username,
        "Password": encrypted_password,
        "RememberMe": True,
        "CompanyCode": COMPANY_CODE,
        "CompanyName": COMPANY_NAME,
        "AppName": APP_NAME,
        "LoginOnly": True,
    }

    try:
        resp = session.post(LOGIN_URL, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        return LoginResult(success=False, error=f"request_exception: {exc}"), session

    if resp.status_code != 200:
        return LoginResult(
            success=False,
            error=f"http_{resp.status_code}: {resp.text[:300]}",
        ), session

    try:
        body = resp.json()
    except json.JSONDecodeError:
        return LoginResult(
            success=False,
            error=f"json_decode_failed: {resp.text[:300]}",
        ), session

    sys_info = body.get("SysInfo") or {}
    flag = int(sys_info.get("Flag", -1))
    msg = (sys_info.get("Msg") or "").strip()

    if flag != 0:
        hint = ""
        if auth_mode == "replay":
            # 抓包密文被拒 —— 服务端很可能加了防重放/密文过期。这就是该切实时加密的信号。
            hint = ("  ⚠ 当前用的是 2026-06-02 抓包密文重放;若提示认证/解密失败,"
                    "说明门户已加防重放 —— 需配置 ANSTEEL_RSA_PUBKEY 走实时加密"
                    "(见 auth_config / docs/改造清单-20260617.md #7)")
        return LoginResult(
            success=False,
            flag=flag,
            msg=msg,
            error=f"login_failed_flag={flag} mode={auth_mode} msg={msg!r}{hint}",
            raw_response=body,
        ), session

    # 抽取登录后用户信息
    user_name_cn = ""
    user_id = 0
    dept_cname = ""
    for tbl in (body.get("Tables") or []):
        rows = tbl.get("Rows") or []
        if not rows:
            continue
        cols = [c["Name"] for c in (tbl.get("Columns") or [])]
        row0 = rows[0]
        rowdict = dict(zip(cols, row0))
        if tbl.get("Name") == "Table0":
            user_name_cn = str(rowdict.get("username") or "").strip()
        elif tbl.get("Name") == "UserInfo":
            user_id = int(rowdict.get("ID") or 0)
            user_name_cn = user_name_cn or str(rowdict.get("CNAME") or "").strip()
            dept_cname = str(rowdict.get("DEPT_CNAME") or "").strip()

    # 抽 cookies + 认证 headers(关键!)
    cookies_dict = {c.name: c.value for c in session.cookies}
    response_headers = dict(resp.headers)
    signature = response_headers.get("signature", "")
    refresh_token = response_headers.get("refresh_token", "")

    # 让 session 后续请求自动带这两个 header
    if signature:
        session.headers["signature"] = signature
    if refresh_token:
        session.headers["refresh_token"] = refresh_token

    return LoginResult(
        success=True,
        flag=flag,
        signature=signature,
        refresh_token=refresh_token,
        msg=msg,
        user_name_cn=user_name_cn,
        user_id=user_id,
        dept_cname=dept_cname,
        cookies=cookies_dict,
        response_headers=response_headers,
        raw_response=body,
    ), session


# ── CLI 单步测试 ────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """直接跑一次登录,把结果 + cookies 打到 stdout。"""
    import argparse

    p = argparse.ArgumentParser(description="鞍钢门户登录测试")
    p.add_argument("--verbose", action="store_true",
                   help="打印完整 response body + response headers")
    p.add_argument("--save-cookies", type=Path, default=None,
                   help="把 cookies 存到 JSON 文件,后续脚本可加载")
    args = p.parse_args(argv)

    print(f"→ POST {LOGIN_URL}")
    print(f"  账号(明文): {PLAINTEXT_USERNAME}")
    print(f"  Payload UserName 长度: {len(CAPTURED_ENCRYPTED_USERNAME)}")
    print(f"  Payload Password 长度: {len(CAPTURED_ENCRYPTED_PASSWORD)}")
    print()

    result, session = login()

    if args.verbose:
        print("=== response headers ===")
        # 这里其实拿不到,因为 session.post 已经返回。重做一次抓 headers
        print("(verbose 模式重发一次拿响应 headers...)")
        try:
            resp = session.post(
                LOGIN_URL,
                json={
                    "UserName": CAPTURED_ENCRYPTED_USERNAME,
                    "Password": CAPTURED_ENCRYPTED_PASSWORD,
                    "RememberMe": True,
                    "CompanyCode": COMPANY_CODE,
                    "CompanyName": COMPANY_NAME,
                    "AppName": APP_NAME,
                    "LoginOnly": True,
                },
                timeout=30,
            )
            for k, v in resp.headers.items():
                print(f"  {k}: {v}")
            print()
            print("=== response body ===")
            print(json.dumps(resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {"text": resp.text}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"  (verbose 重发失败: {exc})")
        print()

    print("=== LoginResult ===")
    if result.success:
        print(f"  ✅ success")
        print(f"  flag       = {result.flag}")
        print(f"  msg        = {result.msg!r}")
        print(f"  user_name  = {result.user_name_cn}")
        print(f"  user_id    = {result.user_id}")
        print(f"  dept       = {result.dept_cname}")
        print(f"  signature  = {result.signature[:80]}{'...' if len(result.signature) > 80 else ''}")
        print(f"             过期 {result.signature_expires_at}")
        print(f"  refresh    = {result.refresh_token[:80]}{'...' if len(result.refresh_token) > 80 else ''}")
        print(f"             过期 {result.refresh_token_expires_at}")
        print(f"  cookies    = {len(result.cookies)} 个")
        for k, v in result.cookies.items():
            short = v if len(v) <= 60 else v[:57] + "..."
            print(f"    {k} = {short}")
    else:
        print(f"  ❌ failed")
        print(f"  flag  = {result.flag}")
        print(f"  msg   = {result.msg!r}")
        print(f"  error = {result.error}")

    if args.save_cookies and result.success:
        # 兼容老参数名:实际存的是 auth bundle(cookies + signature + refresh)
        bundle = {
            "cookies": result.cookies,
            "signature": result.signature,
            "refresh_token": result.refresh_token,
            "user_id": result.user_id,
            "user_name_cn": result.user_name_cn,
            "dept_cname": result.dept_cname,
            "signature_expires_at": result.signature_expires_at,
            "refresh_token_expires_at": result.refresh_token_expires_at,
        }
        args.save_cookies.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  auth bundle saved to {args.save_cookies}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
