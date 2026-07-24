"""inspection_slip engine 单元测试 — Mock API + 逻辑测试"""
import json
from unittest.mock import patch, MagicMock

import pytest

from sop_hub.engines.inspection_slip import InspectionSlipEngine


class TestNormalizeRows:
    def setup_method(self):
        self.engine = InspectionSlipEngine.__new__(InspectionSlipEngine)

    def test_basic_row_normalization(self):
        raw = [
            {"seq": 1, "car_type": "P64", "car_no": "1234567", "cargo_info_raw": "汐子铁矿粉", "remark": "", "defect": False},
            {"seq": 2, "car_type": "P64", "car_no": "7654321", "cargo_info_raw": "", "remark": "装高1.5", "defect": False},
        ]
        rows = self.engine._normalize_rows("normal", raw, page_index=1, side_name="left")
        assert len(rows) == 2
        assert rows[0]["seq"] == 1
        assert rows[0]["car_no"] == "1234567"
        assert rows[1]["remark"] == "装高1.5"

    def test_station_ocr_corrections_apply_to_cargo_info_raw(self):
        raw = [
            {"seq": 1, "car_type": "70", "car_no": "1234567", "cargo_info_raw": "沙子铁矿粉", "remark": "", "defect": False},
            {"seq": 2, "car_type": "70", "car_no": "7654321", "cargo_info_raw": "沱子铁矿粉", "remark": "", "defect": False},
        ]
        rows = self.engine._normalize_rows("normal", raw, page_index=1, side_name="left")
        assert rows[0]["cargo_info_raw"] == "汐子铁矿粉"
        assert rows[1]["cargo_info_raw"] == "汐子铁矿粉"

    def test_alumina_extra_fields(self):
        raw = [
            {"seq": 1, "car_type": "C64", "car_no": "1234567", "cargo_info_raw": "", "tarp_no": "T001", "piece_count": 40, "defect": False},
        ]
        rows = self.engine._normalize_rows("alumina", raw, page_index=1, side_name="left")
        assert rows[0]["tarp_no"] == "T001"
        assert rows[0]["piece_count"] == 40

    def test_dict_wrapper_unwrap(self):
        """Model sometimes wraps rows in {"rows": [...]}"""
        raw = {"rows": [{"seq": 1, "car_no": "1234567"}]}
        rows = self.engine._normalize_rows("normal", raw, page_index=1, side_name="left")
        assert len(rows) == 1

    def test_empty_input(self):
        rows = self.engine._normalize_rows("normal", "invalid", page_index=1, side_name="left")
        assert rows == []

    def test_seq_with_annotation(self):
        """seq 字段可能含有注释文字"""
        raw = [{"seq": "1（划）", "car_no": "1234567"}]
        rows = self.engine._normalize_rows("normal", raw, page_index=1, side_name="left")
        assert rows[0]["seq"] == 1


class TestApplyInheritance:
    def setup_method(self):
        self.engine = InspectionSlipEngine.__new__(InspectionSlipEngine)
        self.engine.service_url = ""
        self.engine.output_base = None

    def test_anchor_detection(self):
        rows = [
            {"seq": 1, "car_no": "1111111", "cargo_info_raw": "汐子铁矿粉/金泰68", "remark": "", "defect": False},
            {"seq": 2, "car_no": "2222222", "cargo_info_raw": "", "remark": "", "defect": False},
            {"seq": 3, "car_no": "3333333", "cargo_info_raw": "马林工业盐", "remark": "", "defect": False},
        ]
        summary = self.engine._apply_inheritance(rows)
        # First two rows should be under 汐子铁矿粉
        assert rows[0]["cargo_info_effective"].startswith("汐子铁矿粉")
        assert rows[1]["cargo_info_effective"].startswith("汐子铁矿粉")
        # Third row should start a new batch
        assert rows[2]["cargo_info_effective"].startswith("马林工业盐")

    def test_defect_detection_by_keyword(self):
        rows = [
            {"seq": 1, "car_no": "1111111", "cargo_info_raw": "汐子铁矿粉", "remark": "门缝大", "defect": False},
        ]
        self.engine._apply_inheritance(rows)
        assert rows[0]["defect"] is True

    def test_detail_accumulation(self):
        """Non-anchor cargo info rows accumulate within the same batch"""
        rows = [
            {"seq": 1, "car_no": "1111111", "cargo_info_raw": "汐子铁矿粉/金泰68", "remark": "", "defect": False},
            {"seq": 2, "car_no": "2222222", "cargo_info_raw": "15节", "remark": "", "defect": False},
            {"seq": 3, "car_no": "3333333", "cargo_info_raw": "", "remark": "", "defect": False},
        ]
        self.engine._apply_inheritance(rows)
        # All rows should belong to the 汐子铁矿粉 batch
        for row in rows:
            assert "汐子铁矿粉" in row["cargo_info_effective"]

    def test_anchor_detection_after_station_ocr_correction(self):
        rows = [
            {"seq": 1, "car_no": "1111111", "cargo_info_raw": "沙子铁矿粉/宝丽", "remark": "", "defect": False},
            {"seq": 2, "car_no": "2222222", "cargo_info_raw": "", "remark": "", "defect": False},
        ]
        rows = self.engine._normalize_rows("normal", rows, page_index=1, side_name="left")
        self.engine._apply_inheritance(rows)
        assert rows[0]["cargo_info_raw"] == "汐子铁矿粉/宝丽"
        assert rows[0]["cargo_info_effective"].startswith("汐子铁矿粉")
        assert rows[1]["cargo_info_effective"].startswith("汐子铁矿粉")


class TestValidateResult:
    def setup_method(self):
        self.engine = InspectionSlipEngine.__new__(InspectionSlipEngine)

    def test_count_match(self):
        meta = {"jieshu": 3}
        footer = {}
        rows = [{"seq": 1}, {"seq": 2}, {"seq": 3}]
        msg = self.engine._validate_result(meta, footer, rows)
        assert msg == ""

    def test_count_mismatch(self):
        meta = {"jieshu": 5}
        footer = {}
        rows = [{"seq": 1}, {"seq": 2}]
        msg = self.engine._validate_result(meta, footer, rows)
        assert "车数不符" in msg

    def test_footer_reconciliation_failure(self):
        meta = {"jieshu": 10}
        footer = {"zhuangche_jieshu": 7, "paiche_jieshu": 5}
        rows = list(range(10))
        msg = self.engine._validate_result(meta, footer, rows)
        assert "对账失败" in msg


class TestLooksLikeInspection:
    def setup_method(self):
        self.engine = InspectionSlipEngine.__new__(InspectionSlipEngine)

    def test_positive_keywords(self):
        layout = {"title_texts": ["锦州港杂码公司火运输港检、装车通知单"], "summary": ""}
        assert self.engine._looks_like_inspection(layout) is True

    def test_negative(self):
        layout = {"title_texts": ["出港计划通知单"], "summary": "这是一份计划单据"}
        assert self.engine._looks_like_inspection(layout) is False


def test_trusted_document_type_bypasses_entry_vlm_false(tmp_path):
    image_path = tmp_path / "inspection.jpg"
    image_path.write_bytes(b"not-read-because-splits-are-mocked")

    engine = InspectionSlipEngine()
    engine._prepare_preview = MagicMock(return_value=image_path)
    engine._split_into_pages = MagicMock(return_value=[image_path])
    engine._split_page_into_sides = MagicMock(
        return_value={"left": image_path, "right": image_path}
    )
    engine._split_side_into_chunks = MagicMock(return_value=[image_path])
    engine.call_api = MagicMock(
        side_effect=[
            ({"is_inspection": False, "summary": "未识别到检车单结构"}, 0.1),
            (
                {
                    "title": "锦州港杂码公司火运货物疏港检、装车通知单",
                    "meta": {"daoxian": "煤四", "jieshu": 1},
                    "footer": {"zhuangche_jieshu": 1, "paiche_jieshu": 0},
                },
                0.1,
            ),
            (
                [
                    {
                        "seq": 1,
                        "car_type": "70",
                        "car_no": "1570121",
                        "cargo_info_raw": "汐子铁矿粉 宝丽",
                        "remark": "",
                        "defect": False,
                    }
                ],
                0.1,
            ),
            ([], 0.1),
        ]
    )

    result = engine.process_image(
        str(image_path),
        trusted_document_type=True,
    )

    assert result["is_inspection"] is True
    assert result["rows_count"] == 1
    assert result["car_nos"] == ["1570121"]
    assert engine.call_api.call_count == 4
