"""对 inbox 88 (鞍子河 56 节装车通知单) 手动建 inspection candidate,
让 daemon 的 zhongtang_inspection_chain 接住跑通。

背景:wx_12 图 OCR 成功(_pending/.../90_0c4552cf...result.json),但 live_service
分类时 sop_project_id 漏标 + extraction_json_path 误绑到另一张图(80_6088b987...),
导致 candidate 没建。已先修复 inbox.sop_project_id / extraction_json_path,
本脚本走 ingest_inspection_payload 路径建 candidate(同 r72/live_service 同一函数)。
"""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sop_hub.data_agent.agent import BusinessDataAgent

INBOX_ID = 88
JSON_PATH = Path(
    "/Users/qicai21/Documents/bussiness-artifacts/wechat_images/_pending/2026-06/json/90_0c4552cf1dca2e3ec2e3d58e96e5a6df_result.json"
)
SOURCE_FILE_NAME = "90_0c4552cf1dca2e3ec2e3d58e96e5a6df.jpg"
GROUP_NAME = "中唐特钢发运群"


def main():
    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    print(f"OCR rows_count={payload.get('rows_count')}, doc_type={payload.get('doc_type')}")
    agent = BusinessDataAgent()
    res = agent.ingest_inspection_payload(
        payload, source_file_name=SOURCE_FILE_NAME, group_name=GROUP_NAME,
    )
    print("ingest result:", json.dumps(res, ensure_ascii=False)[:300])
    # 这一步会写 inspection_ingestion_candidates 行,且函数内会回填 inbox 的
    # inspection_candidate_id / message_id 关联(只要 source_file_name 能反查上)。
    print("\nDONE — 现在 daemon 下一轮应 pick 起 zhongtang_inspection_chain on inbox 88")


if __name__ == "__main__":
    main()
