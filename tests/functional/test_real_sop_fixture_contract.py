"""Functional contract test for real SOP markdown fixtures.

This round establishes a minimal markdown loader contract only:
- fixtures must be markdown documents;
- the loader returns raw file path, first H1 title, and raw content;
- no SOP business semantics are parsed here.
"""

from pathlib import Path

from sop_hub.models.project_sop import load_markdown_sop_fixture


# 2026-06-02 文档收敛:SOP md 从 tests/fixtures/sops/ 挪到 docs/project-sops/
# 文件名同步去掉 _sop 后缀(跟 yaml project_id 对齐)
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "docs" / "project-sops"
EXPECTED_FIXTURE_NAMES = sorted(
    [
        "chaoyang_steel.md",
        "jiusan.md",
        "jilin_jingang_jinzhou.md",
        "zhongtang_special_steel.md",
    ]
)


def test_markdown_loader_contract_loads_fixture_metadata():
    fixture_paths = sorted(FIXTURE_DIR.glob("*.md"))
    assert [path.name for path in fixture_paths] == EXPECTED_FIXTURE_NAMES

    for path in fixture_paths:
        fixture = load_markdown_sop_fixture(path)

        assert fixture.file_path == path
        assert fixture.file_path.suffix == ".md"
        assert fixture.content.strip(), f"empty fixture: {path}"
        assert fixture.content.startswith("# ")
        assert fixture.title == fixture.content.splitlines()[0][2:].strip()
        assert fixture.title, f"missing title: {path}"
