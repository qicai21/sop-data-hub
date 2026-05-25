"""Contract test for copied real SOP fixtures.

This is an audit-level fixture contract: the copied markdown SOPs must remain
stable source material for a future loader, but no runtime loader is implemented
in this round.
"""

from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sops"

FIXTURE_CASES = {
    "zhongtang_special_steel_sop.md": [
        "中唐特钢铁矿发运项目",
        "中唐特钢铁矿发运项目 SOP",
        "中唐特钢发运群",
        "铁晟业务工作群",
        "数据单发群",
        "## 5. 节点定义",
    ],
    "chaoyang_steel_sop.md": [
        "朝阳钢铁铁矿发运项目",
        "朝阳钢铁铁矿发运项目 SOP",
        "朝钢铁矿发运群",
        "铁晟业务工作群",
        "数据单发群",
        "## 5. 节点定义",
    ],
    "jilin_jingang_sop.md": [
        "吉林金钢-锦州港铁矿发运项目",
        "吉林金钢-锦州港铁矿发运项目 SOP",
        "普通货运项目",
        "高桥镇",
        "铁晟业务工作群",
        "## 5. 节点定义",
    ],
    "jiusan_soybean_sop.md": [
        "九三大豆铁路发运项目",
        "九三大豆铁路发运项目 SOP",
        "数据单发群",
        "95306 `xts` 账号",
        "## 5. 术语与业务锚点",
    ],
}


def test_real_sop_fixture_files_exist_and_keep_source_contract():
    for filename, expected_fragments in FIXTURE_CASES.items():
        path = FIXTURE_DIR / filename
        assert path.exists(), f"missing fixture: {path}"

        content = path.read_text(encoding="utf-8")
        assert content.strip(), f"empty fixture: {path}"

        for fragment in expected_fragments:
            assert fragment in content, f"fixture {filename} missing fragment: {fragment}"


def test_real_sop_fixture_paths_are_only_markdown_docs():
    fixture_paths = sorted(FIXTURE_DIR.glob("*.md"))
    assert [p.name for p in fixture_paths] == sorted(FIXTURE_CASES)
