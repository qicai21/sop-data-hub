"""Read back one sent GROUP013 file before retrying a planned send action."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


WX_OPS_ROOT = Path("/Users/qicai21/projects/repos/wx-ops-agent")
GROUP013_WXID = "56327225598@chatroom"


def select_sent_file_evidence(
    rows: list[dict[str, Any]], *, filename: str, not_before: str,
) -> dict[str, Any] | None:
    """Return the newest exact file attachment sent no earlier than an action.

    This pure selector is intentionally strict: a similarly named text message,
    an earlier attachment, or a different filename is not delivery evidence.
    """
    threshold = str(not_before or "").replace("T", " ")[:19]
    matches: list[dict[str, Any]] = []
    for row in rows:
        sent_at = str(row.get("time") or row.get("send_time") or "").replace("T", " ")[:19]
        if sent_at < threshold:
            continue
        if str(row.get("file_name") or row.get("link_title") or "") != filename:
            continue
        if str(row.get("appmsg_kind") or row.get("message_kind") or "") != "file":
            continue
        matches.append(row)
    if not matches:
        return None
    row = max(matches, key=lambda item: str(item.get("time") or item.get("send_time") or ""))
    return {
        "time": row.get("time") or row.get("send_time"),
        "local_id": row.get("local_id"),
        "file_name": row.get("file_name") or row.get("link_title"),
        "file_size": row.get("file_size"),
    }


def find_group013_file_evidence(filename: str, *, not_before: str) -> dict[str, Any] | None:
    """Read encrypted WeChat history for a previously sent exact attachment.

    A missing key, database, or transient query failure is treated as no
    evidence.  The caller may then send normally; it must never infer success
    from a bridge return code alone.
    """
    if not filename or not WX_OPS_ROOT.is_dir():
        return None
    src_root = str(WX_OPS_ROOT / "src")
    if src_root not in sys.path:
        sys.path.insert(0, src_root)
    try:
        from wechat_ops_agent.db.query import query_contact_messages
        from wechat_ops_agent.key_manager.discovery import find_db_dir

        keys = json.loads((WX_OPS_ROOT / "current_keys.json").read_text())
        db_dir = Path(find_db_dir() or "")
        if not db_dir.is_dir():
            return None
        for db in sorted(db_dir.rglob("message_*.db"), reverse=True):
            if not re.fullmatch(r"message_\d+\.db", db.name):
                continue
            key = keys.get(db.read_bytes()[:16].hex())
            if not key:
                continue
            rows = query_contact_messages(str(db), key, GROUP013_WXID, limit=200)
            evidence = select_sent_file_evidence(
                rows, filename=filename, not_before=not_before,
            )
            if evidence:
                evidence["message_db"] = str(db)
                return evidence
    except Exception:
        return None
    return None
