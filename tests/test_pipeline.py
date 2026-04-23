"""pipeline 策略与路由单元测试"""
import pytest

from ops_hub.pipeline.models import CategoryRoute, GroupImageStrategy, ClassificationResult, ProcessResult
from ops_hub.pipeline.strategy import _default_strategy, load_business_group_strategies
from ops_hub.pipeline.doc_detail_mode import normalize_doc_detail_mode, should_skip_deep_detail


class TestGroupImageStrategy:
    def test_default_strategy_has_all_categories(self):
        strategy = _default_strategy()
        assert "检装车通知单" in strategy.routes
        assert "出港计划通知单" in strategy.routes
        assert "other" in strategy.routes
        assert len(strategy.routes) >= 14

    def test_route_for_known_category(self):
        strategy = _default_strategy()
        route = strategy.route_for("检装车通知单")
        assert route.action == "inspection_slip_extract"

    def test_route_for_unknown_falls_to_other(self):
        strategy = _default_strategy()
        route = strategy.route_for("完全未知的类别")
        assert route.category == "other"
        assert route.action == "save_only"


class TestDocDetailMode:
    def test_normalize_none(self):
        assert normalize_doc_detail_mode(None) == "shallow"

    def test_normalize_full(self):
        assert normalize_doc_detail_mode("full") == "full"

    def test_normalize_invalid(self):
        assert normalize_doc_detail_mode("invalid") == "shallow"

    def test_skip_deep_for_shallow_inspection(self):
        assert should_skip_deep_detail("检装车通知单", "shallow") is True

    def test_no_skip_for_full_inspection(self):
        assert should_skip_deep_detail("检装车通知单", "full") is False

    def test_no_skip_for_photo(self):
        assert should_skip_deep_detail("照片-敞车内部情况和作业", "shallow") is False


class TestLoadStrategies:
    def test_default_when_no_config(self, tmp_path):
        strategies = load_business_group_strategies(tmp_path / "nonexistent.yaml")
        assert len(strategies) >= 1
        first = list(strategies.values())[0]
        assert first.name == "铁晟业务工作群"


class TestProcessResult:
    def test_basic_construction(self):
        result = ProcessResult(
            category="检装车通知单",
            bucket="table",
            saved_image_path="/tmp/test.jpg",
        )
        assert result.category == "检装车通知单"
        assert result.should_notify is False
        assert result.payload == {}
