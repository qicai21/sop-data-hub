"""classifier 单元测试 — Mock VLM API"""
import json
from unittest.mock import patch, MagicMock

import pytest

from sop_hub.classifier.classifier import BusinessGroupImageClassifier, _extract_json_fragment


class TestExtractJsonFragment:
    def test_plain_json_object(self):
        result = _extract_json_fragment('{"category": "检装车通知单", "confidence": 0.95}')
        assert result["category"] == "检装车通知单"

    def test_markdown_wrapped(self):
        text = '```json\n{"category": "other", "confidence": 0.5}\n```'
        result = _extract_json_fragment(text)
        assert result["category"] == "other"

    def test_mixed_text_with_json(self):
        text = 'Some explanation text\n{"category": "请车表", "confidence": 0.8}\nMore text'
        result = _extract_json_fragment(text)
        assert result["category"] == "请车表"

    def test_json_array(self):
        text = '[{"seq": 1, "car_no": "1234567"}]'
        result = _extract_json_fragment(text)
        assert isinstance(result, list)
        assert result[0]["seq"] == 1

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            _extract_json_fragment("This is just plain text with no JSON.")

    def test_salvages_truncated_object_with_core_fields(self):
        text = '{"category":"检装车通知单","confidence":0.95,"detected_title":"锦州港杂码公司火运货物疏港检、装车通知单","evidence":"标题明确包含检装车通知单；表格包含道线、节数、车皮号等字段'
        result = _extract_json_fragment(text)
        assert result["category"] == "检装车通知单"
        assert result["confidence"] == 0.95
        assert result["detected_title"] == "锦州港杂码公司火运货物疏港检、装车通知单"
        assert "标题明确" in result["evidence"]


class TestClassifierCategoryRouting:
    def test_unknown_category_falls_back_to_other(self):
        classifier = BusinessGroupImageClassifier()
        # Simulate the category validation logic
        assert "other" in classifier.category_cards
        assert "检装车通知单" in classifier.category_cards

    def test_category_cards_count(self):
        classifier = BusinessGroupImageClassifier()
        # Should have at least 11 categories + other
        assert len(classifier.category_cards) >= 12

    @patch("sop_hub.classifier.classifier.requests.post")
    def test_classify_returns_result(self, mock_post, tmp_path):
        # Create a tiny test image
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="white")
        img_path = tmp_path / "test.jpg"
        img.save(img_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "text": json.dumps({
                "category": "检装车通知单",
                "confidence": 0.95,
                "detected_title": "锦州港检装车通知单",
                "evidence": "标题可见"
            }, ensure_ascii=False)
        }
        mock_post.return_value = mock_resp

        classifier = BusinessGroupImageClassifier()
        result = classifier.classify(img_path)
        assert result.category == "检装车通知单"
        assert result.confidence == 0.95

    @patch("sop_hub.classifier.classifier.requests.post")
    def test_low_confidence_日现场_falls_to_other(self, mock_post, tmp_path):
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="white")
        img_path = tmp_path / "test2.jpg"
        img.save(img_path)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "text": json.dumps({
                "category": "日现场工作记录表",
                "confidence": 0.5,
                "detected_title": "",
                "evidence": "不太确定"
            }, ensure_ascii=False)
        }
        mock_post.return_value = mock_resp

        classifier = BusinessGroupImageClassifier()
        result = classifier.classify(img_path)
        # Low confidence 日现场 should fall to other
        assert result.category == "other"
