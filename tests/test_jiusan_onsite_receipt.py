from sop_hub.fees.jiusan_onsite_receipt import (
    BulkTrainGroup,
    display_rows,
    receipt_lines,
    render_receipt_page,
)


def _rows(n: int) -> list[dict]:
    return [
        {"car_seq": i, "car_no": f"810{i:04d}", "car_model": "L18", "ydid": f"YD{i}"}
        for i in range(1, n + 1)
    ]


def test_display_rows_keeps_first_two_and_last_two() -> None:
    rows = _rows(50)
    shown = display_rows(rows)
    assert shown[0]["car_seq"] == 1
    assert shown[1]["car_seq"] == 2
    assert shown[2] is None
    assert shown[3]["car_seq"] == 49
    assert shown[4]["car_seq"] == 50


def test_receipt_lines_lock_business_text() -> None:
    group = BulkTrainGroup("和谐1", "2026-06-13", "七道", 50, _rows(50))
    lines = [text for text, _align in receipt_lines(group)]
    assert "作业单位/班组: 优瑞达物流" in lines
    assert not any("样张" in text or "数据源" in text for text in lines)
    assert "[ ] #19 散粮车装卸辅助作业服务" in lines
    assert "[ ] #20 散粮车车体检查及作业现场检查服务" in lines
    assert lines.count("") >= 8


def test_render_receipt_page_has_receipt_width() -> None:
    group = BulkTrainGroup("和谐1", "2026-06-13", "七道", 50, _rows(50))
    image = render_receipt_page(receipt_lines(group))
    assert image.width == 639
    assert image.height > 0
