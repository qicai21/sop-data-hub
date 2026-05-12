from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from ops_hub.config import Settings
from ops_hub.runner import process_new_image


def test_contextual_artifacts_and_status_file(tmp_path):
    img = Image.new("RGB", (32, 32), color="white")
    image_path = tmp_path / "123_abc.jpg"
    img.save(image_path)

    settings = Settings(
        classified_output_dir=str(tmp_path / "wechat_images"),
        extraction_output_dir=str(tmp_path / "legacy_extractions"),
        auto_extract_categories=["检装车通知单"],
    )

    cls_result = MagicMock(category="检装车通知单", confidence=0.95)
    classifier = MagicMock()
    classifier.classify.return_value = cls_result

    extracted = {
        "rows_count": 1,
        "rows": [{"car_no": "1234567"}],
        "footer": {"zhuangche_jieshu": 53},
        "_agent_updated_ids": ["batch-1"],
    }
    with patch("ops_hub.runner._get_classifier", return_value=classifier), patch(
        "ops_hub.runner._run_extraction", return_value=extracted
    ):
        result = process_new_image(
            image_path,
            settings,
            month_str="2026-05",
            group_name="铁晟业务工作群",
        )

    base = tmp_path / "wechat_images" / "铁晟业务工作群"
    assert Path(result.saved_path) == base / "检装车通知单" / "123_abc.jpg"
    assert Path(result.extraction_saved_path) == base / "extractions" / "检装车通知单" / "123_abc_result.json"
    assert Path(result.status_path) == base / "_status" / "123_abc.json"
    assert Path(result.saved_path).exists()
    assert Path(result.extraction_saved_path).exists()
    status_text = Path(result.status_path).read_text(encoding="utf-8")
    assert '"state": "extracted"' in status_text
    assert '"category": "检装车通知单"' in status_text
    assert '"rows_count": 1' in status_text
    assert '"_agent_updated_ids": [\n    "batch-1"\n  ]' in status_text
    assert str(Path(result.extraction_saved_path)) in status_text
