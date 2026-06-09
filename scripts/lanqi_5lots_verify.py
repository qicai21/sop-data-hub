"""蓝鳍 5 lots 全量 vs 吉林金钢工厂系统对账(2026-06-07)。

每个 lot 走 verify_factory_upload(order_id, release_batch_id):
- 工厂 list API 拉 orderId 下的 box → api_total
- DB 拼出本 lot 应有 box 集(整车 container_numbers_json/container_no
  + split 车 container_batch_map 里指向本 lot 的 box) → expected
- 比 expected vs api → missing(应有但工厂没的) / extra(工厂有但 DB 没的)

输出每 lot 一行 + 总表,缺/多就打印 box 示例方便定位。
"""
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
DB = REPO / "data" / "sop_agent.db"
PROJECT = "jilin_jingang_jinzhou"
SHIP = "蓝鳍"


def db_expected_boxes(conn, batch_id: str) -> tuple[set[str], int]:
    """本 lot 应有的 box 集 = 整车直属 + split 车里指向本 lot 的 box。

    注意:wagon.batch_id 指向"该车主 lot",split 车的 box 可能分别属不同
    lot,要看 container_batch_map。
    """
    boxes: set[str] = set()
    wagon_count = 0

    # 1. 整车直属(batch_id=本lot 且 container_batch_map IS NULL 或 empty)
    direct = conn.execute(
        "SELECT car_no, container_no, container_numbers_json, container_batch_map "
        "FROM wagon_shipments WHERE batch_id=?",
        (batch_id,),
    ).fetchall()
    for r in direct:
        cbm = r["container_batch_map"]
        if cbm:
            try:
                cbm_d = json.loads(cbm)
            except Exception:
                cbm_d = {}
            # split 车主 lot 是本 lot:只挑 cbm 里 value=本lot 的 box
            for box, target_lot in cbm_d.items():
                if target_lot == batch_id:
                    boxes.add(box.strip())
        else:
            # 整车
            cnj = r["container_numbers_json"]
            row_boxes: list[str] = []
            if cnj:
                try:
                    row_boxes = [b.strip() for b in json.loads(cnj) if b.strip()]
                except Exception:
                    pass
            if not row_boxes:
                raw = r["container_no"] or ""
                row_boxes = [b.strip() for b in raw.split("/") if b.strip()]
            for b in row_boxes:
                boxes.add(b)
        wagon_count += 1

    # 2. split 车主 lot 是别的 lot,但 cbm 指本 lot 的 box
    cross = conn.execute(
        "SELECT car_no, container_batch_map FROM wagon_shipments "
        "WHERE batch_id!=? AND container_batch_map IS NOT NULL AND container_batch_map!=''",
        (batch_id,),
    ).fetchall()
    for r in cross:
        try:
            cbm_d = json.loads(r["container_batch_map"])
        except Exception:
            continue
        for box, target_lot in cbm_d.items():
            if target_lot == batch_id:
                boxes.add(box.strip())

    return boxes, wagon_count


def main():
    from sop_hub.sop.factory_verify import verify_factory_upload
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    lots = conn.execute(
        "SELECT id, batch_sequence, order_identifier, contract_no, "
        "       dispatch_status, actual_wagon_count, batch_quantity "
        "FROM release_batches "
        "WHERE project=? AND ship_name=? ORDER BY batch_sequence",
        (PROJECT, SHIP),
    ).fetchall()

    print(f"{'lot':<6}{'order_id':<22}{'contract':<22}{'DB cars':>7}"
          f"{'DB boxes':>9}{'API total':>10}{'match':>7}{'miss':>5}{'extra':>6}")
    print("-" * 100)

    all_ok = True
    details: list[tuple[str, list[str], list[str]]] = []
    for lot in lots:
        bid = lot["id"]; seq = lot["batch_sequence"]
        oid = lot["order_identifier"] or ""
        contract = (lot["contract_no"] or "")[:20]

        if not oid:
            print(f"{seq:<6}{'(no order_id)':<22}{contract:<22}"
                  f"{lot['actual_wagon_count']:>7}{'-':>9}{'-':>10}{'SKIP':>7}{'-':>5}{'-':>6}")
            continue

        expected_boxes, db_wagon_cnt = db_expected_boxes(conn, bid)
        v = verify_factory_upload(
            order_id=oid, release_batch_id=bid, db_path=str(DB),
        )
        # verify 内部自己推 expected;我们再用自己更严的 split-aware 集做二次对账
        miss = expected_boxes - set(v.api_box_numbers or set())
        extra = set(v.api_box_numbers or set()) - expected_boxes

        match_flag = "OK" if (len(miss) == 0 and len(extra) == 0) else "X"
        if match_flag == "X":
            all_ok = False
        print(f"{seq:<6}{oid:<22}{contract:<22}{db_wagon_cnt:>7}"
              f"{len(expected_boxes):>9}{v.api_total:>10}{match_flag:>7}"
              f"{len(miss):>5}{len(extra):>6}")
        if miss or extra:
            details.append((seq, sorted(miss)[:10], sorted(extra)[:10]))

    conn.close()
    print()
    if details:
        print("=== 不对账详情 ===")
        for seq, m, e in details:
            print(f"  {seq}:")
            if m:
                print(f"    DB有 工厂无 ({len(m)} 示例): {m}")
            if e:
                print(f"    工厂有 DB无 ({len(e)} 示例): {e}")
    print(f"\nALL_OK: {all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
