"""Functional preview test for real SOP monitoring plan generation.

This is intentionally small:
real markdown fixtures -> normalizer -> adapter -> compiler -> wechat monitoring plan.
"""

from pathlib import Path

from ops_hub.sop.monitoring_plan_preview import (
    build_real_sop_monitoring_plan_preview,
    normalized_project_sops_to_compiler_input,
    render_real_sop_monitoring_plan_preview_markdown,
    write_real_sop_monitoring_plan_preview_report,
)
from ops_hub.models.project_sop import load_normalized_project_sops


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sops"


def test_real_sop_monitoring_plan_preview_chain_generates_wechat_plan():
    normalized = load_normalized_project_sops(FIXTURE_DIR)
    project_sops = normalized_project_sops_to_compiler_input(normalized)
    preview = build_real_sop_monitoring_plan_preview(FIXTURE_DIR)

    assert len(normalized) == 4
    assert len(project_sops) == 4
    assert "wechat_monitoring_plan" in preview.plan

    wechat_plan = preview.plan["wechat_monitoring_plan"]
    assert wechat_plan
    assert any(group_plan["watch_items"] for group_plan in wechat_plan.values())

    candidate_projects = set()
    for group_plan in wechat_plan.values():
        for item in group_plan["watch_items"]:
            candidate_projects.update(item.get("candidate_projects", []))

    assert {"zhongtang_special_steel", "chaoyang_steel", "jilin_jingang_jinzhou", "jiusan"}.issubset(candidate_projects)
    assert any(
        item.get("target_sop_nodes")
        for group_plan in wechat_plan.values()
        for item in group_plan["watch_items"]
    )


def test_real_sop_monitoring_plan_preview_report_is_human_readable(tmp_path):
    output_path = tmp_path / "real_sop_monitoring_plan_preview.md"
    rendered = render_real_sop_monitoring_plan_preview_markdown(FIXTURE_DIR)
    written_path = write_real_sop_monitoring_plan_preview_report(FIXTURE_DIR, output_path)

    assert written_path == output_path
    assert output_path.exists()
    assert rendered == output_path.read_text(encoding="utf-8")
    assert "Real SOP Monitoring Plan Preview" in rendered
    assert "wechat_monitoring_plan" in rendered
    assert "GROUP001" in rendered or "GROUP003" in rendered or "GROUP013" in rendered
    assert "candidate_projects" in rendered
