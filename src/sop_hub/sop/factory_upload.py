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
    preview: bool = False   # #98: 仅 build payload 不 POST(人工 review 用)

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
    """货物品名优先级:cargo_product_name(品名,如"印粉")→ cargo_name_detail
    → cargo_name(品类,如"铁矿")。yaml 要求"严格按放货批次货物品名原文"。"""
    return (
        batch_row.get("cargo_product_name")
        or batch_row.get("cargo_name_detail")
        or batch_row.get("cargo_name")
        or ""
    )


def _wagon_containers(wagon_row: dict[str, Any]) -> list[str]:
    """从 wagon row 提取箱号列表。container_numbers_json 优先(JSON array 稳定源),
    fallback container_no("A/B" 字符串)。两者都空返回 []。"""
    cnj = wagon_row.get("container_numbers_json")
    if cnj:
        try:
            return [b for b in json.loads(cnj) if b]
        except Exception:
            pass
    raw = wagon_row.get("container_no") or ""
    return [p.strip() for p in str(raw).split("/") if p.strip()]


def _split_container_pair(container_no: str) -> list[str]:
    """Legacy split — 仅当只能拿到 'A/B' 字符串时用。新代码用 _wagon_containers。"""
    if not container_no:
        return [""]
    return [p.strip() for p in container_no.split("/") if p.strip()]


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
        containers = _wagon_containers(w_dict)
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
    preview: bool = False,
    db_path: str | Path | None = None,
) -> FactoryUploadBatchResult:
    """Upload all wagon shipments for a release_batch to the factory system.

    #98 一体化:默认就是真上传(login → POST each wagon → 结构化结果)。
    preview=True 是唯一保留的"人工 review"语义:只 login + build payload、不 POST。

    Args:
        release_batch_id: The release_batch to upload wagons for.
        project_id: SOP project id.
        preview: If True, only login + build payloads, skip POST (human review).
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
        preview=preview,
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

    if preview:
        # preview:返回 payload 预览,不 POST(人工 review)
        for w in payloads:
            result.results.append(UploadResult(
                wagon_no=w.wagon_no,
                success=True,  # preview 一律标 success
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


# ── Per-dispatch-event API(2026-06-06 #107)────────────────────────────
# 配套 departure_excel.generate_dispatch_event_excel:同一次发车事件可能跨
# 多个 release_batch,每条 wagon 用各自 batch 的 contract_no / order_identifier。
# 上传一次(login → POST per wagon),后跑反查时按 order_identifier 分组各做
# 一次(jilin 工厂 list API 按 orderId 查)。


@dataclass
class DispatchEventUploadResult:
    project_id: str
    total_wagons: int = 0
    success_count: int = 0
    failure_count: int = 0
    results: list[UploadResult] = field(default_factory=list)
    login_success: bool = False
    login_error: str = ""
    preview: bool = False
    release_batch_ids: list[str] = field(default_factory=list)
    # 按 order_identifier 分组的反查指引:caller 用 verify_factory_upload 逐组反查
    order_identifier_groups: dict[str, list[str]] = field(default_factory=dict)
    # 2026-06-17:上传前校验结果 + 上传后门户ID回填箱级记录
    validation: dict[str, Any] = field(default_factory=dict)
    validation_blocked: bool = False        # 校验没过 → 没上传
    portal_backfilled: int = 0               # 回填了门户ID的箱级行数

    @property
    def all_success(self) -> bool:
        return self.login_success and self.failure_count == 0


def _build_event_upload_payloads(
    wagon_ids: list[str] | None, config: FactoryUploadConfig, *,
    container_ydids: list[str] | None = None,
    db_path: str | Path | None = None,
) -> tuple[list[WagonUploadPayload], list[str], str]:
    """Build upload payloads from explicit wagon_ids spanning batches.

    Returns (payloads, release_batch_ids, error). Each wagon's payload uses its
    own batch's contract_no / order_identifier / cargo_name / ship_name.
    """
    wagon_ids = wagon_ids or []
    container_ydids = container_ydids or []
    if not wagon_ids and not container_ydids:
        return [], [], "wagon_ids or container_ydids is empty"
    sop_path = Path(db_path) if db_path else SOP_DB_PATH
    if not sop_path.exists():
        return [], [], f"DB not found: {sop_path}"
    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
        if container_ydids:
            in_ph = ",".join("?" * len(container_ydids))
            wagons = conn.execute(
                f"SELECT car_no, ydid, project_id, ticketed_at, batch_id "
                f"FROM wagon_container_shipments WHERE ydid IN ({in_ph}) "
                f"GROUP BY car_no, ydid ORDER BY ticketed_at ASC, car_no ASC",
                container_ydids,
            ).fetchall()
        else:
            in_ph = ",".join("?" * len(wagon_ids))
            wagons = conn.execute(
                f"SELECT * FROM wagon_shipments WHERE id IN ({in_ph}) "
                f"ORDER BY ticketed_at ASC, car_no ASC", wagon_ids,
            ).fetchall()
        if not wagons:
            return [], [], f"no wagons found for given {len(wagon_ids)} ids"
        batch_ids_set: set[str] = {w["batch_id"] for w in wagons if w["batch_id"]}
        # 2026-06-07:split 车 cbm 里指向的 lot 也要进 batches dict,否则下面
        # build payload 时 batches.get(cbm[box]) 为 None,box2 fallback 回主 lot,
        # 工厂端 order_id 错位(实战暴露,lot6 box1388540 → lot5 order)。
        import json as _json
        for w in wagons:
            cbm_raw = w["container_batch_map"]
            if cbm_raw:
                try:
                    for v in (_json.loads(cbm_raw) or {}).values():
                        if v:
                            batch_ids_set.add(v)
                except Exception:
                    pass
        batch_ids = sorted(batch_ids_set)
        if not batch_ids:
            return [], [], "no batch_id on any wagon"
        b_ph = ",".join("?" * len(batch_ids))
        batches_rows = conn.execute(
            f"SELECT * FROM release_batches WHERE id IN ({b_ph})", batch_ids,
        ).fetchall()
        batches = {b["id"]: dict(b) for b in batches_rows}
    finally:
        conn.close()

    import json as _json
    # #123 Phase 2:集装箱业务直接 SELECT wagon_container_shipments per box rows,
    # box.batch_id 是真值,合同走该行 batch。整车业务保留老 cbm 路径。
    _project_id = ""
    for _w in wagons:
        _pid = (dict(_w).get("project_id") if not isinstance(_w, dict) else _w.get("project_id"))
        if _pid:
            _project_id = _pid; break
    _use_container_table = False
    try:
        from sop_hub.sop.wagon_container_shipments import is_container_business_project
        _use_container_table = is_container_business_project(_project_id)
    except Exception:
        _use_container_table = False

    payloads: list[WagonUploadPayload] = []

    if _use_container_table:
        # 用 (car_no, ydid) 查 box rows;补全 batches dict
        car_ydid_pairs = []
        for w in wagons:
            wd = w if isinstance(w, dict) else dict(w)
            cn = wd.get("car_no")
            if cn:
                car_ydid_pairs.append((cn, wd.get("ydid") or ""))
        conn2 = sqlite3.connect(str(sop_path)); conn2.row_factory = sqlite3.Row
        try:
            extra_batch_ids: set[str] = set()
            box_rows: list[dict[str, Any]] = []
            for car_no, ydid in car_ydid_pairs:
                if ydid:
                    rs = conn2.execute(
                        "SELECT car_no, box_no, batch_id FROM wagon_container_shipments "
                        "WHERE car_no=? AND ydid=? ORDER BY box_position",
                        (car_no, ydid),
                    ).fetchall()
                else:
                    rs = conn2.execute(
                        "SELECT car_no, box_no, batch_id FROM wagon_container_shipments "
                        "WHERE car_no=? ORDER BY box_position",
                        (car_no,),
                    ).fetchall()
                for r in rs:
                    box_rows.append(dict(r))
                    if r["batch_id"] not in batches:
                        extra_batch_ids.add(r["batch_id"])
            if extra_batch_ids:
                ph = ",".join("?" * len(extra_batch_ids))
                for br in conn2.execute(
                    f"SELECT * FROM release_batches WHERE id IN ({ph})", list(extra_batch_ids),
                ).fetchall():
                    batches[br["id"]] = dict(br)
        finally:
            conn2.close()
        for br in box_rows:
            batch = batches.get(br["batch_id"])
            if not batch:
                continue
            wagon_no = br["car_no"]; box = br["box_no"]
            ship_name = batch.get("ship_name", "") or ""
            cargo_name = _get_cargo_name_from_batch(batch)
            order_identifier = batch.get("order_identifier", "") or ""
            contract_no = batch.get("contract_no", "") or ""
            payload: dict[str, Any] = {}
            for fdef in config.fields:
                if fdef.source == "fixed":
                    payload[fdef.key] = fdef.value
                elif fdef.source == "release_batch.ship_name":
                    payload[fdef.key] = ship_name
                elif fdef.source == "release_batch.cargo_name":
                    payload[fdef.key] = cargo_name
                elif fdef.source == "release_batch.order_identifier":
                    payload[fdef.key] = order_identifier
                elif fdef.source in ("release_batch.contract_no",
                                     "release_batch.factory_contract_no"):
                    payload[fdef.key] = contract_no
                elif fdef.source == "95306_confirm" and fdef.key == "wagonNumber":
                    payload[fdef.key] = wagon_no
                elif fdef.source == "95306_confirm" and fdef.key == "boxNumber":
                    payload[fdef.key] = box
                else:
                    payload[fdef.key] = fdef.value or ""
            payloads.append(WagonUploadPayload(
                wagon_no=wagon_no, container_no=box, payload=payload,
            ))
        return payloads, batch_ids, ""

    # 老路径(整车业务)— split 车看 cbm 拆
    for w in wagons:
        w_dict = dict(w)
        primary_batch = batches.get(w_dict.get("batch_id"))
        if not primary_batch:
            continue
        wagon_no = w_dict.get("car_no", "") or ""
        containers = _wagon_containers(w_dict)
        # 解 cbm 取 per-box 归属 lot;split 车按 cbm 拆,整车直接走 primary
        cbm_raw = w_dict.get("container_batch_map")
        cbm: dict[str, str] = {}
        if cbm_raw:
            try:
                cbm = _json.loads(cbm_raw) or {}
            except Exception:
                cbm = {}
        for box in containers:
            # box 对应的 batch:cbm 有就用它指的;没 cbm 就用 wagon.batch_id
            target_batch_id = cbm.get(box) or w_dict.get("batch_id")
            batch = batches.get(target_batch_id, primary_batch)
            ship_name = batch.get("ship_name", "") or ""
            cargo_name = _get_cargo_name_from_batch(batch)
            order_identifier = batch.get("order_identifier", "") or ""
            contract_no = batch.get("contract_no", "") or ""
            payload: dict[str, Any] = {}
            for fdef in config.fields:
                if fdef.source == "fixed":
                    payload[fdef.key] = fdef.value
                elif fdef.source == "release_batch.ship_name":
                    payload[fdef.key] = ship_name
                elif fdef.source == "release_batch.cargo_name":
                    payload[fdef.key] = cargo_name
                elif fdef.source == "release_batch.order_identifier":
                    payload[fdef.key] = order_identifier
                elif fdef.source in (
                    "release_batch.contract_no",
                    "release_batch.factory_contract_no",
                ):
                    payload[fdef.key] = contract_no
                elif fdef.source == "95306_confirm" and fdef.key == "wagonNumber":
                    payload[fdef.key] = wagon_no
                elif fdef.source == "95306_confirm" and fdef.key == "boxNumber":
                    payload[fdef.key] = box
                else:
                    payload[fdef.key] = fdef.value or ""
            payloads.append(WagonUploadPayload(
                wagon_no=wagon_no, container_no=box, payload=payload,
            ))
    return payloads, batch_ids, ""


def upload_dispatch_event_wagons(
    wagon_ids: list[str] | None = None,
    *,
    project_id: str = "jilin_jingang_jinzhou",
    preview: bool = False,
    db_path: str | Path | None = None,
    validate: bool = True,
    container_ydids: list[str] | None = None,
) -> DispatchEventUploadResult:
    """Upload wagons of a per-dispatch-event to factory system.

    跨 batch 的 wagon 集合一次上传,login 只跑 1 次,POST per wagon。返回 results
    + order_identifier_groups,caller 按 group 逐个调 verify_factory_upload。

    validate=True(默认,2026-06-17):上传前强制校验(单一发车事件 + 车数/箱数
    对齐 95306,堵整批);校验不过直接拒传。手工部分补传传 validate=False 绕过。
    上传后把「箱号→门户ID」落审计台账并查重。
    """
    config = _load_factory_config(project_id)
    result = DispatchEventUploadResult(project_id=project_id, preview=preview)

    # ── 步骤1:上传前校验(堵整批 + 核车数/箱数)──────────────────
    if validate and not container_ydids:
        from sop_hub.sop.upload_validation import validate_dispatch_event_upload
        sop_path0 = Path(db_path) if db_path else SOP_DB_PATH
        v = validate_dispatch_event_upload(wagon_ids, db_path=sop_path0)
        result.validation = v.to_dict()
        if not v.ok:
            result.validation_blocked = True
            result.login_error = "上传前校验未过: " + "; ".join(v.errors)
            return result

    token, login_error = login_to_factory(config)
    if login_error:
        result.login_error = login_error
        return result
    result.login_success = True

    payloads, batch_ids, build_error = _build_event_upload_payloads(
        wagon_ids, config, container_ydids=container_ydids, db_path=db_path,
    )
    if build_error:
        result.login_error = build_error
        return result
    result.release_batch_ids = batch_ids
    result.total_wagons = len(payloads)

    # 按 order_identifier 分组(同 batch 共享一个 order_identifier)。
    # 反查时 jilin 工厂 list API 是按 orderId 查,所以每组 1 次反查。
    sop_path = Path(db_path) if db_path else SOP_DB_PATH
    conn = sqlite3.connect(str(sop_path))
    conn.row_factory = sqlite3.Row
    try:
        b_ph = ",".join("?" * len(batch_ids))
        bm_rows = conn.execute(
            f"SELECT id, order_identifier FROM release_batches WHERE id IN ({b_ph})",
            batch_ids,
        ).fetchall()
        batch_to_order = {
            r["id"]: (r["order_identifier"] or "") for r in bm_rows
        }
    finally:
        conn.close()
    # group: order_identifier → list of release_batch_id
    for bid in batch_ids:
        oid = batch_to_order.get(bid, "")
        if not oid:
            continue
        result.order_identifier_groups.setdefault(oid, []).append(bid)

    if preview:
        for w in payloads:
            result.results.append(UploadResult(
                wagon_no=w.wagon_no, success=True, http_status=0,
                response_body=json.dumps(w.payload, ensure_ascii=False),
            ))
        result.success_count = len(payloads)
        return result

    assert token is not None
    for w in payloads:
        r = upload_one_wagon(w, token, config)
        result.results.append(r)
        if r.success:
            result.success_count += 1
        else:
            result.failure_count += 1
            logger.warning(
                "factory_upload(event) failed wagon=%s status=%s error=%s",
                w.wagon_no, r.http_status, r.error or r.response_body[:100],
            )
        time.sleep(0.3)

    # ── 步骤2:上传后反查门户,把「门户ID」回填到箱级记录(随记录走)──────
    try:
        _backfill_portal_ids(result, db_path=db_path)
    except Exception as exc:
        logger.warning("portal_id backfill failed: %s", exc)
    return result


def _backfill_portal_ids(result, *, db_path: str | Path | None = None) -> None:
    """上传后反查门户,把 门户ID 回填到 wagon_container_shipments.portal_id。

    2026-06-17:对方系统主键(门户ID)随箱级记录走,取代独立 ledger 库。
    按 (box_no, batch_id) 精确回填 —— 集装箱跨 batch/项目复用同箱号,必须按
    batch 限定,否则串号(实证:同箱在九三玛格丽特+吉林马兰希望各有不同门户ID)。
    """
    from sop_hub.sop.factory_remove import fetch_order_rows
    from sop_hub.sop.factory_verify import _login as _verify_login
    from sop_hub.utils.time import now_iso_beijing

    # order_identifier → 本次涉及的 batch_id 列表
    groups = result.order_identifier_groups or {}
    if not groups:
        return
    tok, _ = _verify_login()
    if not tok:
        return
    now = now_iso_beijing()
    sop_path = Path(db_path) if db_path else SOP_DB_PATH
    conn = sqlite3.connect(str(sop_path))
    try:
        n = 0
        for oid, batch_ids in groups.items():
            rows = fetch_order_rows(oid, tok)
            for row in rows:
                box = str(row.get("boxNumber") or "")
                pid = row.get("id")
                if not box or pid is None:
                    continue
                for bid in batch_ids:
                    n += conn.execute(
                        "UPDATE wagon_container_shipments SET portal_id=?, "
                        "portal_uploaded_at=? WHERE box_no=? AND batch_id=?",
                        (pid, now, box, bid)).rowcount
        conn.commit()
        result.portal_backfilled = n
    finally:
        conn.close()


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
        "--preview", action="store_true",
        help="只 login + build payload,不 POST(人工 review);默认就是真上传"
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Path to sop_agent.db"
    )
    return parser


def main():
    parser = _build_cli_parser()
    args = parser.parse_args()

    result = upload_release_batch(
        args.release_batch_id,
        preview=args.preview,
        db_path=args.db_path,
    )

    print(json.dumps({
        "release_batch_id": result.release_batch_id,
        "preview": result.preview,
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
