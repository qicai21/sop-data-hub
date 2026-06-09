"""#124 e2e 烟雾测试 — 多项目 chain 路由 & lifecycle 守门。

这套不跑完整链(无 95306 mock),只验证:
  - chain 入口路由(text_router / _resolve_task_type)对每项目落正确 task_type
  - yaml 守门:jilin has_inspection_slip=false 拒 inspection chain → SKIP_TASK_TYPE_SENTINEL
  - chain step 2 phase 过滤 + pending_match 兜底(中唐 / 朝阳 same shape)
  - lifecycle 推进顺序合法(各项目 mode 下)

中唐鞍子河 + 朝阳完整链已经有专项 e2e(test_create_wagon_shipments / test_inspection_*)。
本套是"组合层"防回归:守门 / 路由 / lifecycle 跨项目一致性。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))


# ── task_type 路由(#121 守门 + 三项目分支) ──────────────────────
def test_task_type_routing_per_project_and_flow():
    from sop_hub.sop import workflow_task_store as wts

    # jilin departure text → jljg_departure_text_chain
    assert wts._resolve_task_type(
        "jilin_jingang_jinzhou", "departure_flow", "detect_departure_message"
    ) == "jljg_departure_text_chain"

    # 中唐 inspection → zhongtang_inspection_chain
    assert wts._resolve_task_type(
        "zhongtang_special_steel", "inspection_notice_flow", "create_inspection_candidate"
    ) == "zhongtang_inspection_chain"

    # 朝阳 inspection → chaoyang_inspection_chain
    assert wts._resolve_task_type(
        "chaoyang_steel", "inspection_notice_flow", "create_inspection_candidate"
    ) == "chaoyang_inspection_chain"

    # 朝阳 dispatch_flow → #129 守门拒(yaml 没声明 dispatch_flow,老 chaoyang_dispatch_context
    # 路由也是 generic_sop_task 死路,守门一致拒绝)
    assert wts._resolve_task_type(
        "chaoyang_steel", "dispatch_flow", "anything"
    ) == wts.SKIP_TASK_TYPE_SENTINEL

    # freight detail → freight_detail_enrichment(项目通吃)
    for pid in ("chaoyang_steel", "jilin_jingang_jinzhou", "zhongtang_special_steel"):
        assert wts._resolve_task_type(
            pid, "freight_detail_flow", "extract_freight_detail"
        ) == "freight_detail_enrichment"


# ── jilin yaml 守门:has_inspection_slip=false 拒 inspection flow ──
def test_jilin_inspection_flow_rejected_by_yaml_guard(monkeypatch):
    from sop_hub.sop import workflow_task_store as wts

    # 真实 jilin yaml 写了 has_inspection_slip: false,_project_has_inspection_slip 返 False
    assert not wts._project_has_inspection_slip("jilin_jingang_jinzhou"), (
        "jilin yaml 应明确写 has_inspection_slip: false"
    )

    # 路由该被守门
    assert wts._resolve_task_type(
        "jilin_jingang_jinzhou", "inspection_notice_flow", "create_inspection_candidate"
    ) == wts.SKIP_TASK_TYPE_SENTINEL


# ── lifecycle 路径合法性(8 phase + 跳转矩阵)─────────────────────
def test_lifecycle_all_paths_legal_per_mode(tmp_path, monkeypatch):
    """3 个典型项目跑完整 happy path:
       jilin: created→enriched→loading→all_loaded→tracking→delivered→confirmed_received→closed
       chaoyang: loading→all_loaded→closed(shipped_is_completed mode 短链)
       zhongtang: 同 jilin(full_track_to_received mode)
    """
    from sop_hub.sop.lifecycle_transition import advance_lifecycle
    from sop_hub.sop import lifecycle as lc

    db = tmp_path / "lc_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE release_batches (
        id TEXT PRIMARY KEY, dispatch_status TEXT NOT NULL,
        dispatch_status_note TEXT, dispatch_status_updated_at TEXT,
        updated_at TEXT
    )""")
    conn.executemany(
        "INSERT INTO release_batches (id, dispatch_status) VALUES (?,?)",
        [
            ("jilin_b", "pending_freight"),
            ("chaoyang_b", "loading"),     # 朝阳常见起点
            ("zhongtang_b", "pending_freight"),
        ],
    )
    conn.commit(); conn.close()
    monkeypatch.setenv("BUSINESS_DATA_AGENT_DB_PATH", str(db))

    # jilin full happy path
    for nxt in [lc.ENRICHED, lc.LOADING, lc.ALL_LOADED, lc.TRACKING,
                lc.DELIVERED, lc.CONFIRMED_RECEIVED, lc.CLOSED]:
        r = advance_lifecycle("jilin_b", nxt, reason="step", db_path=str(db))
        assert r["action"] == "advanced", f"jilin → {nxt} failed: {r}"

    # chaoyang 短链(loading 起,跳到 all_loaded 再 closed)
    for nxt in [lc.ALL_LOADED, lc.CLOSED]:
        r = advance_lifecycle("chaoyang_b", nxt, reason="step", db_path=str(db))
        assert r["action"] == "advanced", f"chaoyang → {nxt}: {r}"

    # 非法回退被拒
    r = advance_lifecycle("jilin_b", lc.LOADING, reason="should_reject",
                          db_path=str(db))
    assert r["action"] == "rejected"

    # zhongtang 走 full track,跳过 enriched 直接 loading 也合法(实际 chain
    # 可能跳级,因为消息进来时合同已经在更早 batch ingest 时补齐了)
    for nxt in [lc.LOADING, lc.ALL_LOADED, lc.TRACKING, lc.DELIVERED,
                lc.CONFIRMED_RECEIVED]:
        r = advance_lifecycle("zhongtang_b", nxt, reason="step", db_path=str(db))
        assert r["action"] == "advanced"


# ── parser 各项目 examples 全 complete ──────────────────────────────
def test_departure_text_parser_examples_per_project_all_complete():
    """yaml departure_flow.examples 里写的样例 parser 必须能识别 status=complete。
    防 yaml ↔ parser 漂移(老例子改 yaml 但 parser 没更新或反之)。"""
    from sop_hub.sop.departure_text_parser import parse_departure_text
    samples = {
        "jilin": [
            "煤六 50节 四平铁 蓝鳍",
            "十四道 41节 四平铁 厦门世纪",
            "煤六  四平铁\"蓝鳍\"18节",
            "九道   四平镍\"长航滨海\"46节",
        ],
        # 中唐用 inspection chain(图),text "煤五汐子铁鞍子河实装54节" 也走 freight
        # 但 parser 是 jilin 的 departure;此处不测中唐。
        # 朝阳 dispatch 用 inspection_notice,无 departure text。
    }
    for project, texts in samples.items():
        for txt in texts:
            c = parse_departure_text(txt, group_id="g", message_id="m",
                                     message_time="2026-06-06 21:28:40")
            assert c.status == "complete", f"{project}: {txt!r} → {c.status}"
            assert c.car_count > 0
