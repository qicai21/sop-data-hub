"""Factory system upload executor — R54.

Uploads wagon shipment data to the factory system at http://111.26.178.96:88.

Flow:
  departure_flow → generate_factory_json → delivery_target_test
  → factory_upload (this module)

Reads factory_upload_config from jilin_jingang.yaml.
Hardcodes credentials in this script (no env vars, no memory lookup).
Login → build payload from wagon_shipments → POST one wagon at a time.

This module:
- reads sop_agent.db (wagon_shipments + release_batches)
- reads config/project_sops/jilin_jingang.yaml (factory_upload_config)
- POSTs to http://111.26.178.96:88/prod-api
- writes upload result records locally
- does NOT modify wagon_shipments or release_batches
- does NOT query 95306
- does NOT generate Excel/JSON
- does NOT send messages
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

logger = logging.getLogger(__name__)

# ── Hardcoded factory auth (user confirmed: no risk) ─────────────────────
FACTORY_USERNAME = "saibin"
FACTORY_PASSWORD = "Xts@95306"

# ── Canonical paths ──────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[3]  # src/sop_hub/sop → repo root
SOP_YAML_PATH = REPO_ROOT / "config" / "project_sops" / "jilin_jingang.yaml"
SOP_DB_PATH = REPO_ROOT / "data" / "sop_agent.db"


# ── Data models ──────────────────────────────────────────────────────────

@dataclass
class UploadFieldDef:
    """One payload field definition from YAML."""

    key: str  # factory system field name (wagonNumber, boxNumber, ...)
    source: str  # 95306_confirm | release_batch.xxx | fixed
    value: str = ""  # for fixed fields
    description: str = ""


@dataclass
class FactoryUploadConfig:
    """Parsed factory upload config."""

    endpoint: str
    login_path: str
    upload_path: str
    username: str
    token_header: str
    fields: list[UploadFieldDef]
    excel_to_factory: dict[str, str]

    @property
    def login_url(self) -> str:
        return f"{self.endpoint}{self.login_path}"

    @property
    def upload_url(self) -> str:
        return f"{self.endpoint}{self.upload_path}"


@dataclass
class WagonUploadPayload:
    """Payload for one wagon upload."""

    wagon_no: str
    container_no: str
    payload: dict[str, Any]


@dataclass
class UploadResult:
    """Result of a single wagon upload."""

    wagon_no: str
    success: bool
    http_status: int = 0
    response_body: str = ""
    error: str = ""


@dataclass
class FactoryUploadBatchResult:
    """Result of uploading a full batch."""

    release_batch_id: str
    total_wagons: int
    success_count: int
    failure_count: int
    results: list[UploadResult] = field(default_factory=list)
    login_success: bool = False
    login_error: str = ""
    dry_run: bool = False

    @property
    def all_success(self) -> bool:
        return self.login_success and self.failure_count == 0


# ── Config loading ───────────────────────────────────────────────────────

def _load_factory_config(project_id: str = "jilin_jingang_jinzhou") -> FactoryUploadConfig:
    """Load factory_upload_config from SOP YAML."""
    raw = yaml.safe_load(SOP_YAML_PATH.read_text())
    try:
        fc = raw["flows"]["departure_flow"]["factory_upload_config"]
    except KeyError:
        raise ValueError(
            f"factory_upload_config not found in {SOP_YAML_PATH} "
            f"under flows.departure_flow"
        )

    fields = []
    for key, info in fc["payload_fields"].items():
        fields.append(
            UploadFieldDef(
                key=key,
                source=info.get("source", ""),
                value=info.get("value", ""),
                description=info.get("description", ""),
            )
        )

    return FactoryUploadConfig(
        endpoint=fc["endpoint"],
        login_path=fc["login_path"],
        upload_path=fc["upload_path"],
        username=FACTORY_USERNAME,
        token_header=fc.get("auth", {}).get("token_header", "Admin-Token"),
        fields=fields,
        excel_to_factory=fc.get("excel_to_factory_mapping", {}),
    )


# ── Auth ─────────────────────────────────────────────────────────────────

def login_to_factory(config: FactoryUploadConfig | None = None) -> tuple[str | None, str]:
    """Login to factory system. Returns (token, error)."""
    if config is None:
        config = _load_factory_config()

    try:
        resp = requests.post(
            config.login_url,
            json={"username": config.username, "password": FACTORY_PASSWORD},
            timeout=15,
        )
        if resp.status_code != 200:
            return None, f"login HTTP {resp.status_code}: {resp.text[:200]}"

        data = resp.json()
        # Factory returns: {"code": 200, "data": {"access_token": "..."}}
        token = (
            data.get("token")
            or data.get("access_token")
            or (data.get("data") or {}).get("token")
            or (data.get("data") or {}).get("access_token")
        )
        if not token:
            return None, f"no token in login response: {json.dumps(data)[:200]}"

        return token, ""
    except requests.RequestException as e:
        return None, str(e)


# ── Payload building ─────────────────────────────────────────────────────

def _get_cargo_name_from_batch(batch_row: dict[str, Any]) -> str:
    """Get cargo_name: prefer cargo_name_detail, fall back to cargo_name."""
    return (
        batch_row.get("cargo_name_detail")
        or batch_row.get("cargo_name")
        or ""
    )


def _split_container_pair(container_no: str) -> list[str]:
    """Split "TBJU5418112/TBJU8388454" into two single containers."""
    if not container_no:
        return [""]
    parts = container_no.split("/")
    return [p.strip() for p in parts if p.strip()]


def build_upload_payloads(
    release_batch_id: str,
    config: FactoryUploadConfig | None = None,
    *,
    db_path: str | Path | None = None,
) -> tuple[list[WagonUploadPayload], str]:
    """Build upload payloads for all wagons in a release_batch.

    Returns (payloads, error). Each wagon produces one or two rows
    (boxNumber splits container_no "/" into two records).
    """
    if config is None:
        config = _load_factory_config()

    sop_path = Path(db_path) if db_path else SOP_DB_PATH
    if not sop_path.exists():
        return [], f"DB not found: {sop_path}"

    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
        # ── Load release_batch ──
        batch = conn.execute(
            "SELECT * FROM release_batches WHERE id = ?",
            (release_batch_id,),
        ).fetchone()
        if batch is None:
            return [], f"release_batch not found: {release_batch_id}"

        ship_name = batch["ship_name"] or ""
        cargo_name = _get_cargo_name_from_batch(dict(batch))
        order_identifier = batch["order_identifier"] or ""
        contract_no = batch["contract_no"] or ""

        # ── Load wagon_shipments ──
        wagons = conn.execute(
            "SELECT * FROM wagon_shipments WHERE batch_id = ? ORDER BY car_no",
            (release_batch_id,),
        ).fetchall()

        if not wagons:
            return [], f"no wagon_shipments for release_batch {release_batch_id}"

    finally:
        conn.close()

    payloads: list[WagonUploadPayload] = []
    for w in wagons:
        w_dict = dict(w)
        wagon_no = w_dict.get("car_no", "")
        container_raw = w_dict.get("container_no", "")

        containers = _split_container_pair(container_raw)
        for box in containers:
            payload: dict[str, Any] = {}
            for fdef in config.fields:
                if fdef.source == "fixed":
                    payload[fdef.key] = fdef.value
                elif fdef.source == "95306_confirm":
                    if fdef.key == "wagonNumber":
                        payload[fdef.key] = wagon_no
                    elif fdef.key == "boxNumber":
                        payload[fdef.key] = box
                elif fdef.source == "release_batch.cargo_name":
                    payload[fdef.key] = cargo_name
                elif fdef.source == "release_batch.ship_name":
                    payload[fdef.key] = ship_name
                elif fdef.source == "release_batch.factory_contract_no":
                    payload[fdef.key] = contract_no  # note: factory contract ≠ transport contract
                elif fdef.source == "release_batch.order_identifier":
                    payload[fdef.key] = order_identifier

            container_label = box if box else "(no container)"
            payloads.append(WagonUploadPayload(
                wagon_no=wagon_no,
                container_no=container_label,
                payload=payload,
            ))

    return payloads, ""


# ── Upload ───────────────────────────────────────────────────────────────

def upload_one_wagon(
    w: WagonUploadPayload,
    token: str,
    config: FactoryUploadConfig | None = None,
) -> UploadResult:
    """Upload a single wagon to the factory system."""
    if config is None:
        config = _load_factory_config()

    headers = {
        "Authorization": f"Bearer {token}",
        "Cookie": f"{config.token_header}={token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            config.upload_url,
            json=[w.payload],  # factory expects array, even for single wagon
            headers=headers,
            timeout=30,
        )
        # Check business-level code, not just HTTP 200
        try:
            body = resp.json()
            biz_code = body.get("code")
            biz_ok = (resp.status_code == 200 and biz_code == 200)
        except Exception:
            biz_ok = (resp.status_code == 200)
        return UploadResult(
            wagon_no=w.wagon_no,
            success=biz_ok,
            http_status=resp.status_code,
            response_body=resp.text[:500],
        )
    except requests.RequestException as e:
        return UploadResult(
            wagon_no=w.wagon_no,
            success=False,
            error=str(e),
        )


def upload_release_batch(
    release_batch_id: str,
    *,
    project_id: str = "jilin_jingang_jinzhou",
    dry_run: bool = True,
    db_path: str | Path | None = None,
) -> FactoryUploadBatchResult:
    """Upload all wagon shipments for a release_batch to the factory system.

    In dry_run mode: builds payloads, attempts login, but does NOT POST wagons.
    In live mode: login → POST each wagon → collect results.

    Args:
        release_batch_id: The release_batch to upload wagons for.
        project_id: SOP project id.
        dry_run: If True, only login + build payloads, skip POST.
        db_path: Optional sop_agent.db path.

    Returns:
        FactoryUploadBatchResult with per-wagon results.
    """
    config = _load_factory_config(project_id)

    result = FactoryUploadBatchResult(
        release_batch_id=release_batch_id,
        total_wagons=0,
        success_count=0,
        failure_count=0,
        dry_run=dry_run,
    )

    # ── Step 1: login ──
    token, login_error = login_to_factory(config)
    if login_error:
        result.login_error = login_error
        return result
    result.login_success = True

    # ── Step 2: build payloads ──
    payloads, build_error = build_upload_payloads(
        release_batch_id, config, db_path=db_path
    )
    if build_error:
        result.login_error = build_error
        return result

    result.total_wagons = len(payloads)

    if dry_run:
        # In dry-run: return payload previews in results
        for w in payloads:
            result.results.append(UploadResult(
                wagon_no=w.wagon_no,
                success=True,  # dry-run always "succeeds"
                http_status=0,
                response_body=json.dumps(w.payload, ensure_ascii=False),
            ))
        result.success_count = len(payloads)
        return result

    # ── Step 3: upload each wagon ──
    assert token is not None, "token must not be None here — login succeeded"
    for w in payloads:
        r = upload_one_wagon(w, token, config)
        result.results.append(r)
        if r.success:
            result.success_count += 1
        else:
            result.failure_count += 1
            logger.warning(
                "factory_upload failed wagon=%s status=%s error=%s",
                w.wagon_no, r.http_status, r.error or r.response_body[:100],
            )
        # Brief pause between uploads (factory system may not handle bursts)
        time.sleep(0.3)

    return result


# ── CLI ──────────────────────────────────────────────────────────────────

def _build_cli_parser():
    import argparse
    parser = argparse.ArgumentParser(
        description="Upload wagon shipments to factory system."
    )
    parser.add_argument(
        "--release-batch-id", required=True, help="Release batch ID"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Login + build payloads, skip POST (default)"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually POST to factory system"
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Path to sop_agent.db"
    )
    return parser


def main():
    parser = _build_cli_parser()
    args = parser.parse_args()

    dry_run = not args.apply

    result = upload_release_batch(
        args.release_batch_id,
        dry_run=dry_run,
        db_path=args.db_path,
    )

    print(json.dumps({
        "release_batch_id": result.release_batch_id,
        "dry_run": result.dry_run,
        "login_success": result.login_success,
        "login_error": result.login_error,
        "total_wagons": result.total_wagons,
        "success_count": result.success_count,
        "failure_count": result.failure_count,
        "results": [
            {
                "wagon_no": r.wagon_no,
                "success": r.success,
                "http_status": r.http_status,
                "response_body": r.response_body,
                "error": r.error,
            }
            for r in result.results
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
