"""检装车 95306 时间窗反推 —— 用通知单上的车号当锚点,反查 95306 制票时间,
建时间窗,把窗内同到站/货名的所有制票车号拉回来,作为"真装车"权威列表。

设计动机(2026-06-03 宝腾海 53→52 漏触发根因):
  VLM 抽通知单时可能把真装车误标成排车(无理由 defect),导致按通知单标签
  统计出 52 而实际 53。**95306 的制票数才是权威**:同一批检装车通常在密集
  时间窗内统一制票,只要有 1 个车号能在 95306 上找到 ticketed_at,就能反推
  出整个窗内的真实制票车号集合。

业务铁律:
  - 朝阳西不允许拼列(用户口述)→ 同一时间窗内同到站+货名的票都属于同一批。
  - 先按通知单"非排车"顺序找锚点;前 N 个都查不到 → "还没制单",挂起等。
  - 窗内 ∩ 通知单全部车号 = 真装车;通知单独有 = 真排车;窗内独有 = 拼批异常。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def recover_loading_cars_via_window(
    *,
    rail_db_path: str | Path,
    loading_car_nos: list[str],
    all_notice_car_nos: list[str],
    destination: str,
    cargo_pattern: str = "%铁矿%",
    max_anchor_attempts: int = 4,
    window_minutes: int = 120,
    min_ticketed_at: str | None = None,
    auto_correct_car_no: bool = True,
    max_correct_edit_distance: int = 2,
    max_correct_pairs: int = 3,
) -> dict[str, Any]:
    """用通知单车号反推 95306 时间窗,返回窗内权威装车列表。

    Args:
      rail_db_path: 95306_collection.sqlite3 路径(只读)。
      loading_car_nos: 通知单上**非排车**(VLM 标的装车)的 car_no,**保持通知单
        原始顺序** —— 用于在 95306 上找第一个有票的当锚点。
      all_notice_car_nos: 通知单上**全部**车号(装+排) —— 用于跟窗内集合求交。
      destination: 到站(e.g. "朝阳西")。
      cargo_pattern: 货名 LIKE 模式(默认 "%铁矿%")。
      max_anchor_attempts: 找锚点最多尝试前几个 loading 车号(默认 4,业务约定)。
      window_minutes: 锚点 ticketed_at 前后各几分钟为窗(默认 120,即 ±2h)。
      min_ticketed_at: 锚点票的时间下界(通常 = 通知时间 - 12h)。车皮会反复
        发运,同车同到站历史上有旧票;不带下界时 ORDER BY DESC 会锚到**上一批
        的旧票**,整个时间窗错位(#144 根因)。早于下界的票视为"本批还没制票"。
      auto_correct_car_no: 开关 —— 是否启用"通知单错号 vs 95306 自动核对纠错"
        (2026-06-29 #检装车号95306自动核对纠错)。通知单上的车号有时本身录错
        (非 OCR,是单子写错),反推后表现为:真排车数 == 异常数 == N(N≥1)。
        若能把每个真排车在阈值内**唯一**配到一个窗内异常车(车号近似:小编辑距离
        /数字转位),就把通知单错号更正为 95306 真号(异常车并入真装车),让候选
        干净匹配、照常推进,而不是挂 pending_review。**任一保守边界不满足就不动**,
        维持原异常/真排车分类(见 _autocorrect_notice_car_numbers)。
      max_correct_edit_distance: 配对相似度阈值(默认 2),车号必须同长度全数字、
        编辑距离 ≤ 此值(或单次相邻转位)才算近似。
      max_correct_pairs: 单次最多自动更正几对(默认 3),超过即视为不可信、不纠错。

    Returns:
      {
        "status": "ok"            ← 找到锚点 + 窗内/通知单交集干净(或已自动纠错干净),可放心用
                 | "no_ticket_yet"← 前 max_anchor_attempts 个 loading 都没票,挂起等
                 | "anomaly"      ← 找到锚点但有异常(如拼批/无法唯一配对),需人工裁决
                 ,
        "loading_car_nos": [...],  ← 窗内 ∩ 通知单全部车号 = 真装车权威列表(含已纠错并入的真号)
        "anchor_car_no": "..." | None,
        "anchor_ticketed_at": "..." | None,
        "window_minutes": int,
        "window_total_count": int,  ← 95306 窗内总数(去重)
        "missing_from_notice": [...],  ← 窗内有但通知单没的(拼批/窗太宽;已纠错的不再列)
        "notice_only": [...],          ← 通知单有但 95306 没的(真排车;已纠错的不再列)
        "corrections": [               ← 自动核对纠错记录(旧号→新号),空列表=未纠错
            {"notice_car_no": 旧错号, "window_car_no": 95306真号,
             "edit_distance": int, "match_reason": "substitution"|"transposition",
             "source": "95306_window"}, ...
        ],
        "auto_corrected": bool,        ← 本次是否发生了自动纠错
        "anchor_attempts": int,
        "message": "...",
      }
    """
    if not loading_car_nos:
        return {
            "status": "no_ticket_yet",
            "loading_car_nos": [],
            "anchor_car_no": None,
            "anchor_ticketed_at": None,
            "window_minutes": window_minutes,
            "window_total_count": 0,
            "missing_from_notice": [],
            "notice_only": [],
            "corrections": [],
            "auto_corrected": False,
            "anchor_attempts": 0,
            "message": "通知单装车列表为空,无锚点可用",
        }

    rail = sqlite3.connect(f"file:{rail_db_path}?mode=ro", uri=True)
    rail.row_factory = sqlite3.Row
    try:
        # ── 1. 走通知单装车顺序,找第一个 95306 已制票的当锚点 ─────────
        anchor_car: str | None = None
        anchor_ts: str | None = None
        attempts = 0
        for c in loading_car_nos:
            c = (c or "").strip()
            if not c:
                continue
            attempts += 1
            sql = (
                "SELECT ticketed_at FROM shipments "
                "WHERE car_no=? AND destination_name=? AND cargo_name LIKE ? "
                "  AND ticketed_at IS NOT NULL AND ticketed_at != '' "
            )
            params: list[Any] = [c, destination, cargo_pattern]
            if min_ticketed_at:
                sql += "  AND ticketed_at >= ? "
                params.append(min_ticketed_at)
            sql += "ORDER BY ticketed_at DESC LIMIT 1"
            r = rail.execute(sql, params).fetchone()
            if r and r["ticketed_at"]:
                anchor_car = c
                anchor_ts = r["ticketed_at"]
                break
            if attempts >= max_anchor_attempts:
                break

        if anchor_car is None:
            return {
                "status": "no_ticket_yet",
                "loading_car_nos": [],
                "anchor_car_no": None,
                "anchor_ticketed_at": None,
                "window_minutes": window_minutes,
                "window_total_count": 0,
                "missing_from_notice": [],
                "notice_only": [],
                "corrections": [],
                "auto_corrected": False,
                "anchor_attempts": attempts,
                "message": (
                    f"通知单前 {attempts} 个装车号在 95306 都没制票 → "
                    f"还没制单,挂起等 95306 同步"
                ),
            }

        # ── 2. 用锚点 ticketed_at 建窗,查窗内所有 dest+cargo 制票车号 ──
        rows = rail.execute(
            "SELECT DISTINCT car_no FROM shipments "
            "WHERE destination_name=? AND cargo_name LIKE ? "
            "  AND ticketed_at IS NOT NULL AND ticketed_at != '' "
            "  AND ticketed_at BETWEEN datetime(?, ?) AND datetime(?, ?)",
            (
                destination, cargo_pattern,
                anchor_ts, f"-{window_minutes} minutes",
                anchor_ts, f"+{window_minutes} minutes",
            ),
        ).fetchall()
        window_cars = {r["car_no"] for r in rows if r["car_no"]}
        notice_set = {c for c in all_notice_car_nos if c}

        # ── 3. 集合运算:权威装车 = 窗内 ∩ 通知单 ─────────────────────
        loading = sorted(window_cars & notice_set)
        missing_from_notice = sorted(window_cars - notice_set)  # 异常:窗内有,通知单没
        notice_only = sorted(notice_set - window_cars)          # 真排车:通知单有,窗内没

        # ── 3.5. 通知单错号 vs 95306 自动核对纠错(#检装车号95306自动核对纠错)─
        # 通知单录错车号时表现为:真排车数 == 异常数 == N(N≥1)。若能把每个真排车
        # 在阈值内唯一配到一个窗内异常车(车号近似),就把错号更正为 95306 真号:
        # 把配对的异常车并入真装车,从真排车/异常里剔除,候选随之干净匹配照常推进。
        corrections: list[dict[str, Any]] = []
        if auto_correct_car_no and notice_only and missing_from_notice:
            corrections = _autocorrect_notice_car_numbers(
                notice_only=notice_only,
                window_only=missing_from_notice,
                max_edit_distance=max_correct_edit_distance,
                max_pairs=max_correct_pairs,
            )
        if corrections:
            paired_wrong = {c["notice_car_no"] for c in corrections}
            paired_true = {c["window_car_no"] for c in corrections}
            loading = sorted(set(loading) | paired_true)
            missing_from_notice = sorted(set(missing_from_notice) - paired_true)
            notice_only = sorted(set(notice_only) - paired_wrong)

        # ── 4. 异常 sanity ──────────────────────────────────────────────
        status = "ok"
        msg = (
            f"锚点车 {anchor_car} 制票 {anchor_ts};窗内 ±{window_minutes} 分钟"
            f" {len(window_cars)} 车;交集(真装车) {len(loading)};"
            f"通知单独有(真排车) {len(notice_only)}"
        )
        if corrections:
            pairs = ", ".join(
                f"{c['notice_car_no']}→{c['window_car_no']}" for c in corrections
            )
            msg += f";自动核对纠错 {len(corrections)} 车(95306窗口权威): {pairs}"
        if missing_from_notice:
            # 窗内有车号但通知单没列 → 可能是拼批、窗太宽、或别的列
            # 朝阳西按业务铁律不许拼列 → 这种就是异常,挂人工裁决
            status = "anomaly"
            msg += f";异常:95306 窗内 {len(missing_from_notice)} 车不在通知单上"

        return {
            "status": status,
            "loading_car_nos": loading,
            "anchor_car_no": anchor_car,
            "anchor_ticketed_at": anchor_ts,
            "window_minutes": window_minutes,
            "window_total_count": len(window_cars),
            "missing_from_notice": missing_from_notice,
            "notice_only": notice_only,
            "corrections": corrections,
            "auto_corrected": bool(corrections),
            "anchor_attempts": attempts,
            "message": msg,
        }
    finally:
        rail.close()


# ── 通知单错号 vs 95306 自动核对纠错 ────────────────────────────────────────

def _edit_distance(a: str, b: str) -> int:
    """标准 Levenshtein 编辑距离(插入/删除/替换各计 1)。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * lb
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def _is_adjacent_transposition(a: str, b: str) -> bool:
    """b 是否为 a 的一次相邻两位互换(如 1721344 ↔ 1271344)。"""
    if len(a) != len(b) or a == b:
        return False
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    return (
        len(diffs) == 2
        and diffs[1] == diffs[0] + 1
        and a[diffs[0]] == b[diffs[1]]
        and a[diffs[1]] == b[diffs[0]]
    )


def _similar_car_no(wrong: str, true: str, max_edit_distance: int) -> tuple[bool, int, str]:
    """判断两个车号是否近似(可信地认定 wrong 是 true 的录入错版)。

    保守条件:都必须是全数字、同长度。相邻转位优先标记为 transposition(距离 1);
    否则用 Levenshtein 距离 ≤ 阈值判 substitution。
    """
    wrong = (wrong or "").strip()
    true = (true or "").strip()
    if not (wrong.isdigit() and true.isdigit()):
        return (False, 99, "")
    if len(wrong) != len(true):
        return (False, 99, "")
    if _is_adjacent_transposition(wrong, true):
        return (True, 1, "transposition")
    dist = _edit_distance(wrong, true)
    if dist <= max_edit_distance:
        return (True, dist, "substitution")
    return (False, dist, "")


def _autocorrect_notice_car_numbers(
    *,
    notice_only: list[str],
    window_only: list[str],
    max_edit_distance: int,
    max_pairs: int,
) -> list[dict[str, Any]]:
    """把通知单错号(notice_only 中一部分)唯一配对到窗内异常车(window_only)。

    返回 corrections 列表;**任一保守边界不满足就返回 []**(不纠错,维持人工):
      - 异常车数 N==0;或 N > max_pairs。
      - 某个窗内异常车在 notice_only 中找不到**唯一最小距离**近似错号
        (0 个或最小距离并列)→ 整体放弃。
      - 配对非单射(两个异常车配到同一个错号)→ 整体放弃。
      - 未配对的 notice_only 允许继续保留为真排车,不阻断纠错。
    宁可挂人工,绝不乱配。
    """
    n = len(window_only)
    if not notice_only or n == 0 or n > max_pairs:
        return []

    pairing: dict[str, tuple[str, int, str]] = {}
    for true in window_only:
        cands = []
        for wrong in notice_only:
            ok, dist, reason = _similar_car_no(wrong, true, max_edit_distance)
            if ok:
                cands.append((wrong, dist, reason))
        if not cands:
            return []
        cands.sort(key=lambda item: item[1])
        if len(cands) > 1 and cands[0][1] == cands[1][1]:
            return []
        pairing[true] = cands[0]

    # 单射校验:不允许两个异常车配到同一个错号
    wrongs = [v[0] for v in pairing.values()]
    if len(set(wrongs)) != n:
        return []

    return [
        {
            "notice_car_no": wrong,
            "window_car_no": true,
            "edit_distance": dist,
            "match_reason": reason,
            "source": "95306_window",
        }
        for true, (wrong, dist, reason) in pairing.items()
    ]


# ── 审计:落自动纠错记录 + 更正候选车号集合 ────────────────────────────────

CAR_NO_CORRECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS inspection_car_no_corrections (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id      TEXT,
    release_batch_id  TEXT,
    notice_car_no     TEXT NOT NULL,
    window_car_no     TEXT NOT NULL,
    edit_distance     INTEGER,
    match_reason      TEXT,
    source            TEXT DEFAULT '95306_window',
    anchor_car_no     TEXT,
    anchor_ticketed_at TEXT,
    window_minutes    INTEGER,
    created_at        TEXT
);
"""


def persist_car_no_corrections(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    release_batch_id: str,
    corrections: list[dict[str, Any]],
    anchor_car_no: str | None = None,
    anchor_ticketed_at: str | None = None,
    window_minutes: int | None = None,
) -> int:
    """把自动纠错写审计表(旧号→新号、来源=95306窗口、置信依据),并把候选
    car_numbers_json 里的错号替换成真号。返回写入的审计行数。调用方负责 commit。

    幂等:同 (candidate_id, notice_car_no, window_car_no) 已存在则跳过,避免
    延迟验证器(rail95306-sync)重试时重复落审计。
    """
    if not corrections:
        return 0
    conn.execute(CAR_NO_CORRECTION_SCHEMA)
    try:
        from sop_hub.utils.time import now_iso_beijing
        now = now_iso_beijing()
    except Exception:
        from datetime import datetime as _dt
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    written = 0
    for c in corrections:
        exists = conn.execute(
            "SELECT 1 FROM inspection_car_no_corrections "
            "WHERE candidate_id=? AND notice_car_no=? AND window_car_no=?",
            (candidate_id, c["notice_car_no"], c["window_car_no"]),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO inspection_car_no_corrections "
            "(candidate_id, release_batch_id, notice_car_no, window_car_no, "
            " edit_distance, match_reason, source, anchor_car_no, "
            " anchor_ticketed_at, window_minutes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id, release_batch_id,
                c["notice_car_no"], c["window_car_no"],
                c.get("edit_distance"), c.get("match_reason"),
                c.get("source", "95306_window"),
                anchor_car_no, anchor_ticketed_at, window_minutes, now,
            ),
        )
        written += 1

    # 更正候选车号集合:car_numbers_json 里的错号 → 真号
    mapping = {c["notice_car_no"]: c["window_car_no"] for c in corrections}
    row = conn.execute(
        "SELECT car_numbers_json FROM inspection_ingestion_candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
    if row is not None:
        try:
            cars = json.loads((row[0] if not isinstance(row, sqlite3.Row) else row["car_numbers_json"]) or "[]")
        except (TypeError, ValueError):
            cars = []
        if isinstance(cars, list) and any(str(x) in mapping for x in cars):
            new_cars = [mapping.get(str(x), x) for x in cars]
            conn.execute(
                "UPDATE inspection_ingestion_candidates "
                "SET car_numbers_json=?, updated_at=? WHERE id=?",
                (json.dumps(new_cars, ensure_ascii=False), now, candidate_id),
            )
    return written


def autocorrect_config_from_env() -> dict[str, Any]:
    """从环境变量读自动纠错开关/阈值(便于运维关停),给 executor 透传。

    INSPECTION_AUTOCORRECT_CAR_NO   开关,默认开;设 "0"/"false"/"off" 关停。
    INSPECTION_CAR_NO_MAX_EDIT_DIST 相似度阈值(默认 2)。
    INSPECTION_CAR_NO_MAX_PAIRS     单次最多纠错对数(默认 3)。
    """
    raw = os.environ.get("INSPECTION_AUTOCORRECT_CAR_NO", "1").strip().lower()
    enabled = raw not in {"0", "false", "off", "no", ""}
    def _int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            return default
    return {
        "auto_correct_car_no": enabled,
        "max_correct_edit_distance": _int("INSPECTION_CAR_NO_MAX_EDIT_DIST", 2),
        "max_correct_pairs": _int("INSPECTION_CAR_NO_MAX_PAIRS", 3),
    }
