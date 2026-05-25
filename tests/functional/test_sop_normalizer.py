"""Functional contract test for the minimal SOP normalizer.

The normalizer is intentionally narrow:
- read real markdown SOP fixtures;
- extract project_id, group tokens, document/message keywords, and monitoring entries;
- return normalized project_sops without compiler/runtime/OCR/business inference.
"""

from pathlib import Path

from ops_hub.models.project_sop import load_normalized_project_sops, normalize_markdown_sop_fixture


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sops"
EXPECTED_PROJECT_IDS = {
    "zhongtang_special_steel",
    "chaoyang_steel",
    "jilin_jingang_jinzhou",
    "jiusan",
}


def _by_project_id(normalized_project_sops):
    return {project.project_id: project for project in normalized_project_sops}


def test_normalizer_loads_all_real_sop_fixtures():
    normalized = load_normalized_project_sops(FIXTURE_DIR)
    assert {project.project_id for project in normalized} == EXPECTED_PROJECT_IDS

    for project in normalized:
        assert project.source_path.suffix == ".md"
        assert project.source_path.exists()
        assert project.source_title
        assert project.monitoring_entries, project.project_id
        assert project.group_tokens, project.project_id


def test_normalizer_extracts_project_and_group_tokens():
    normalized = _by_project_id(load_normalized_project_sops(FIXTURE_DIR))

    zhongtang = normalized["zhongtang_special_steel"]
    assert {"GROUP001", "GROUP003"}.issubset(set(zhongtang.group_tokens))
    assert {"出港计划通知单", "检装车通知单"}.issubset(set(zhongtang.document_keywords))
    assert "文字放货信息" in zhongtang.message_keywords
    assert any(entry.group_token == "GROUP001" for entry in zhongtang.monitoring_entries)
    assert any(entry.group_token == "GROUP003" for entry in zhongtang.monitoring_entries)

    chaoyang = normalized["chaoyang_steel"]
    assert "GROUP001" in chaoyang.group_tokens
    assert {"出港计划通知单", "检装车通知单"}.issubset(set(chaoyang.document_keywords))
    assert "文字" in chaoyang.message_keywords
    assert any(entry.group_token == "GROUP001" for entry in chaoyang.monitoring_entries)

    jilin = normalized["jilin_jingang_jinzhou"]
    assert "GROUP001" in jilin.group_tokens
    assert {"出港计划通知单", "手写箱号表"}.issubset(set(jilin.document_keywords))
    assert "文字报告" in jilin.message_keywords
    assert any(entry.group_token == "GROUP001" for entry in jilin.monitoring_entries)

    jiusan = normalized["jiusan"]
    assert "GROUP013" in jiusan.group_tokens
    assert "放货单" in jiusan.document_keywords
    assert "发运动态" in jiusan.message_keywords
    assert any(entry.group_token == "GROUP013" for entry in jiusan.monitoring_entries)
    assert any(entry.channel == "rail95306" for entry in jiusan.monitoring_entries)


def test_normalizer_preserves_raw_source_path_and_title():
    path = FIXTURE_DIR / "jilin_jingang_sop.md"
    normalized = normalize_markdown_sop_fixture(path)

    assert normalized.source_path == path
    assert normalized.source_title == "吉林金钢-锦州港铁矿发运项目 SOP"
    assert normalized.project_id == "jilin_jingang_jinzhou"
    assert normalized.project_name == "吉林金钢-锦州港铁矿发运项目"
    assert normalized.monitoring_entries
