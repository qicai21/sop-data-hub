from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sop_hub.sop.image_route_promoter import promote_classified_image_messages


def test_zhongtang_freight_only_group_skips_inspection_promotion(tmp_path: Path) -> None:
    db_path = tmp_path / "sop.db"
    ext_path = tmp_path / "inspection.json"
    ext_path.write_text(
        json.dumps(
            {"project": "zhongtang_special_steel", "_agent_sop_authorized": True},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE message_inbox (
          id INTEGER PRIMARY KEY,
          message_id TEXT,
          group_name TEXT,
          sop_project_id TEXT,
          extraction_json_path TEXT,
          sop_flow TEXT,
          sop_node TEXT,
          classification_label TEXT,
          processing_status TEXT,
          is_sop_msg INTEGER,
          updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO message_inbox (
          id, message_id, group_name, sop_project_id, extraction_json_path,
          sop_flow, sop_node, classification_label, processing_status, is_sop_msg
        ) VALUES (1, 'wx1', '中唐特钢发运群', '', ?, 'inspection_notice_flow',
                  'create_inspection_candidate', '检装车通知单', 'classified', 1)
        """,
        (str(ext_path),),
    )
    conn.commit()
    conn.close()

    result = promote_classified_image_messages(db_path=db_path)
    assert result["promoted"] == 0
    assert result["skipped"] == 1
    assert result["details"][0]["reason"] == "zhongtang_group_freight_only_non_notice"

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT processing_status FROM message_inbox WHERE id=1"
    ).fetchone()
    conn.close()
    assert row[0] == "classified"
