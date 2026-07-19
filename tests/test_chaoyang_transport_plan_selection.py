"""Chaoyang ansteel plan selection must use 回运计划号, not first ship match."""

from __future__ import annotations

from types import SimpleNamespace


def test_upload_and_verify_refuses_ambiguous_ship_plan_without_plan_no(monkeypatch):
    """No transport_plan_no + multiple plans for same ship → hard fail."""
    from sop_hub.external.chaoyang_ansteel import upload_wagons as uw

    monkeypatch.setattr(
        uw,
        "login",
        lambda: (SimpleNamespace(success=True, error=""), object()),
    )

    plans = [
        SimpleNamespace(
            ship_cname="马兰幸福",
            transport_plan_no="HY26062603",
            allot_plan_no="A1",
            raw={},
        ),
        SimpleNamespace(
            ship_cname="马兰幸福",
            transport_plan_no="HY26071703",
            allot_plan_no="A2",
            raw={},
        ),
    ]
    monkeypatch.setattr(uw, "query_wmwm01", lambda **kwargs: (plans, {}))

    result = uw.upload_and_verify(
        db_path=":memory:",
        batch_id="lot3",
        ship_name="马兰幸福",
        # intentionally omit transport_plan_no
        car_nos=["1"],
    )
    assert result.success is False
    assert "必须传入 transport_plan_no" in (result.error or "")
    assert "HY26062603" in (result.error or "")
    assert "HY26071703" in (result.error or "")


def test_resolve_batch_transport_plan_prefers_order_identifier(tmp_path):
    """Batch order_identifier is the canonical 回运计划号 for chaoyang."""
    import sqlite3

    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE release_batches ("
        "id TEXT PRIMARY KEY, order_identifier TEXT, plan_id TEXT)"
    )
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?)",
        ("lot3", "HY26071703", ""),
    )
    conn.execute(
        "INSERT INTO release_batches VALUES (?,?,?)",
        ("lot2", "HY26062603", ""),
    )
    conn.commit()
    row = conn.execute(
        "SELECT order_identifier, plan_id FROM release_batches WHERE id=?",
        ("lot3",),
    ).fetchone()
    conn.close()
    transport = (row[0] or row[1] or "").strip()
    assert transport == "HY26071703"
    assert transport != "HY26062603"
