"""从检装车通知单候选(inspection_ingestion_candidates)反推 ship/dest/cargo/project。

业务铁律:**必须先有出港计划(release_batch in_progress)才能匹配检装车单**。
推不出来 → candidate_status='pending_review',挂起等人,绝不向下游传脏数据。

三层级联:
  1. B  文本融合 — 同群±30 分钟内的 sop_flow=departure_flow text → parse_departure_text
  2. A  证据打分 — payload.rows[].cargo_info_raw + meta.daoxian 对所有 open
        release_batches 打分(ship 3 + dest 2 + cargo 1 + daoxian 1,需 ≥5 且唯一)
  3. 都不行 → pending_review
"""
from __future__ import annotations

import re
import sqlite3
import yaml
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


YAML_ROOT = Path(__file__).resolve().parents[3] / "config" / "project_sops"


@dataclass
class InferenceResult:
    matched: bool
    source: str                # "text_fusion" | "evidence_scoring" | ""
    candidate_status: str      # "matched_by_text" | "matched_by_inference" | "pending_review"
    reason: str
    ship_name: str = ""
    destination: str = ""
    cargo_name: str = ""
    project_id: str = ""
    release_batch_id: str = ""
    score: int = 0
    evidence: list[str] = field(default_factory=list)


# ── yaml hints ─────────────────────────────────────────────────────────


def _load_all_project_hints() -> dict[str, dict]:
    """加载每个项目的 daoxian/destination_aliases/cargo_aliases/known_ships。"""
    out: dict[str, dict] = {}
    for f in YAML_ROOT.glob("*.yaml"):
        try:
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        pid = d.get("project_id")
        if not pid:
            continue
        pm = d.get("project_meta") or {}
        out[pid] = {
            "project_name": d.get("project_name") or "",
            "daoxian": [str(x) for x in (pm.get("daoxian_keywords") or [])],
            "destination_aliases": pm.get("destination_aliases") or {},
            "cargo_aliases": pm.get("cargo_aliases") or {},
            "known_ships": [str(x) for x in (pm.get("known_ships") or [])],
        }
    return out


# ── Step B: 文本融合 ────────────────────────────────────────────────


def _try_text_fusion(
    group_name: str,
    received_datetime: str,
    conn: sqlite3.Connection,
    window_minutes: int = 30,
) -> dict | None:
    """同群±window 分钟内找已识别 departure 文本 → parse → 返回结构化字段。"""
    if not group_name or not received_datetime:
        return None
    try:
        dt = datetime.fromisoformat(received_datetime.replace(" ", "T"))
    except (ValueError, AttributeError):
        return None
    lo = (dt - timedelta(minutes=window_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    hi = (dt + timedelta(minutes=window_minutes)).strftime("%Y-%m-%d %H:%M:%S")

    row = conn.execute(
        "SELECT message_id, text_content "
        "FROM message_inbox "
        "WHERE group_name=? AND sop_flow='departure_flow' "
        "  AND sop_node='detect_departure_message' "
        "  AND text_content IS NOT NULL AND text_content != '' "
        "  AND received_datetime BETWEEN ? AND ? "
        "ORDER BY ABS(julianday(received_datetime) - julianday(?)) ASC LIMIT 1",
        (group_name, lo, hi, dt.strftime("%Y-%m-%d %H:%M:%S")),
    ).fetchone()
    if not row:
        return None

    from sop_hub.sop.departure_text_parser import parse_departure_text
    parsed = parse_departure_text(
        row["text_content"], group_id=group_name,
        message_id=row["message_id"], message_time="",
    )
    if parsed.status not in ("complete", "incomplete"):
        return None
    if not parsed.optional_ship_name or not parsed.project_id:
        return None
    return {
        "ship_name": parsed.optional_ship_name,
        "project_id": parsed.project_id,
        "source_message_id": row["message_id"],
    }


# ── Step A: 证据打分 ────────────────────────────────────────────────


def _extract_evidence_tokens(payload: dict) -> tuple[set[str], str]:
    """从 candidate.payload_json 头 10 行 cargo_info_raw + meta.daoxian 抽 token。"""
    rows = payload.get("rows") or []
    tokens: set[str] = set()
    for r in rows[:10]:
        c = (r.get("cargo_info_raw") or "").strip()
        if not c:
            continue
        for piece in re.split(r"[/、,，\s]+", c):
            piece = piece.strip()
            if piece:
                tokens.add(piece)
    meta = payload.get("meta") or {}
    daoxian = (meta.get("daoxian") or "").strip()
    if daoxian:
        tokens.add(daoxian)
    return tokens, daoxian


def _score_batch_against_evidence(
    batch: dict,
    tokens: set[str],
    daoxian: str,
    all_hints: dict[str, dict],
) -> tuple[int, list[str]]:
    """单 batch 对 tokens 打分。"""
    ship = (batch["ship_name"] or "").strip()
    dest = (batch["destination_station"] or "").strip()
    cargo = (batch["cargo_name"] or "").strip()
    project = (batch["project"] or "").strip()

    hints = all_hints.get(project, {})
    ev: list[str] = []
    s = 0

    # ship hit
    if ship:
        for t in tokens:
            if ship in t or t in ship:
                s += 3
                ev.append(f"ship:{ship}({t})")
                break

    # destination hit (with alias)
    dest_aliases = list(hints.get("destination_aliases", {}).get(dest, []))
    dest_candidates = [d for d in [dest] + dest_aliases if d]
    if dest_candidates:
        for t in tokens:
            if any(d in t for d in dest_candidates):
                s += 2
                ev.append(f"dest:{dest}")
                break

    # cargo hit (with alias)
    cargo_aliases = list(hints.get("cargo_aliases", {}).get(cargo, []))
    cargo_candidates = [c for c in [cargo] + cargo_aliases if c]
    if cargo_candidates:
        for t in tokens:
            if any(c in t for c in cargo_candidates):
                s += 1
                ev.append(f"cargo:{cargo}")
                break

    # daoxian hit
    if daoxian and daoxian in hints.get("daoxian", []):
        s += 1
        ev.append(f"daoxian:{daoxian}")

    return s, ev


def _try_evidence_scoring(
    payload: dict,
    conn: sqlite3.Connection,
    *,
    min_score: int = 5,
) -> dict | None:
    """A:对所有 open release_batches 打分,取唯一最高分(≥min_score)。"""
    tokens, daoxian = _extract_evidence_tokens(payload)
    if not tokens:
        return None

    all_hints = _load_all_project_hints()

    rows = conn.execute(
        "SELECT id, project, ship_name, destination_station, cargo_name "
        "FROM release_batches "
        "WHERE dispatch_status IN ('in_progress', 'suspended') "
        "ORDER BY notice_date DESC"
    ).fetchall()
    if not rows:
        return None

    scored: list[tuple[Any, int, list[str]]] = []
    for r in rows:
        s, ev = _score_batch_against_evidence(dict(r), tokens, daoxian, all_hints)
        scored.append((r, s, ev))
    scored.sort(key=lambda x: -x[1])

    top = scored[0]
    if top[1] < min_score:
        return None
    # 唯一性:第二名必须严格低于最高分
    if len(scored) > 1 and scored[1][1] == top[1]:
        return {"_tied": True, "tied_count": sum(1 for x in scored if x[1] == top[1])}

    b = top[0]
    return {
        "ship_name": b["ship_name"],
        "destination": b["destination_station"],
        "cargo_name": b["cargo_name"],
        "project_id": b["project"],
        "release_batch_id": b["id"],
        "score": top[1],
        "evidence": top[2],
    }


# ── public entrypoint ──────────────────────────────────────────────


def infer_candidate_context(
    candidate_payload: dict,
    *,
    group_name: str,
    received_datetime: str = "",
    db_path: str | Path,
) -> InferenceResult:
    """三层级联推断。conn 自管。

    candidate_payload: VLM 抽到的 JSON(含 rows, meta, footer)
    group_name:        来源群名(用于 B 同群文本融合)
    received_datetime: 候选消息时间字符串(用于 B 时间窗)
    db_path:           sop_agent.db 路径
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Step B
        fusion = _try_text_fusion(group_name, received_datetime, conn)
        if fusion:
            rb_row = conn.execute(
                "SELECT id, project, ship_name, destination_station, cargo_name "
                "FROM release_batches "
                "WHERE ship_name=? AND dispatch_status IN ('in_progress','suspended') "
                "ORDER BY notice_date DESC LIMIT 1",
                (fusion["ship_name"],),
            ).fetchone()
            if rb_row:
                return InferenceResult(
                    matched=True, source="text_fusion",
                    candidate_status="matched_by_text",
                    reason="ok",
                    ship_name=rb_row["ship_name"],
                    destination=rb_row["destination_station"],
                    cargo_name=rb_row["cargo_name"],
                    project_id=rb_row["project"],
                    release_batch_id=rb_row["id"],
                    evidence=[f"text:{fusion['source_message_id']}",
                              f"ship:{fusion['ship_name']}"],
                )
            # B 命中船名但 release_batch 不存在 → 业务铁律:挂起
            return InferenceResult(
                matched=False, source="text_fusion",
                candidate_status="pending_review",
                reason="no_open_release_batch_for_ship",
                ship_name=fusion["ship_name"],
                project_id=fusion["project_id"],
                evidence=[f"text:{fusion['source_message_id']}",
                          f"ship_named:{fusion['ship_name']}"],
            )

        # Step A
        scoring = _try_evidence_scoring(candidate_payload, conn)
        if scoring and not scoring.get("_tied"):
            return InferenceResult(
                matched=True, source="evidence_scoring",
                candidate_status="matched_by_inference",
                reason="ok",
                ship_name=scoring["ship_name"],
                destination=scoring["destination"],
                cargo_name=scoring["cargo_name"],
                project_id=scoring["project_id"],
                release_batch_id=scoring["release_batch_id"],
                score=scoring["score"],
                evidence=scoring["evidence"],
            )
        if scoring and scoring.get("_tied"):
            return InferenceResult(
                matched=False, source="evidence_scoring",
                candidate_status="pending_review",
                reason=f"multi_candidate_tied:{scoring.get('tied_count', 2)}",
                evidence=["tied"],
            )

        # Step C 都不行 → 业务铁律:挂起
        return InferenceResult(
            matched=False, source="",
            candidate_status="pending_review",
            reason="no_open_release_batch_or_inference",
            evidence=[],
        )
    finally:
        conn.close()


# ── CLI for ad-hoc testing ──────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="Inspect a candidate's inferred context.")
    p.add_argument("candidate_id")
    p.add_argument("--db", default="data/sop_agent.db")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM inspection_ingestion_candidates WHERE id LIKE ?",
        (args.candidate_id + "%",),
    ).fetchone()
    if not row:
        print(json.dumps({"error": "candidate not found"}, ensure_ascii=False))
        raise SystemExit(2)
    payload = json.loads(row["payload_json"] or "{}")
    group_name = row["group_name"] or ""

    inbox = conn.execute(
        "SELECT received_datetime FROM message_inbox "
        "WHERE inspection_candidate_id=? OR raw_standard_image_path LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (row["id"], f"%{row['source_file_name']}"),
    ).fetchone()
    received = inbox["received_datetime"] if inbox else (row["created_at"] or "")
    conn.close()

    result = infer_candidate_context(
        payload, group_name=group_name,
        received_datetime=received, db_path=args.db,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
