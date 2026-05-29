#!/usr/bin/env python3
"""CLI for enrich_release_batch executor.

Dry-run (default):
  python scripts/enrich_release_batch.py --release-batch-id <ID> --text "..." --dry-run

Apply:
  python scripts/enrich_release_batch.py --release-batch-id <ID> --text "..." --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ops_hub.sop.enrich_release_batch import enrich_release_batch_with_freight_detail
from ops_hub.sop.freight_detail_extractor import extract_freight_detail


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich release_batch with freight_detail_candidate (manual binding)"
    )
    parser.add_argument(
        "--release-batch-id", required=True,
        help="Target release_batches.id",
    )
    parser.add_argument(
        "--text", required=True,
        help="Freight detail text to parse",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview planned updates, no DB write")
    mode.add_argument("--apply", action="store_true", help="Write to release_batches")
    parser.add_argument(
        "--allow-overwrite", action="store_true",
        help="Overwrite existing non-null fields (default: skip)",
    )
    parser.add_argument(
        "--db-path", default=None,
        help="Path to sop_agent.db (default: BUSINESS_DATA_AGENT_DB_PATH or data/sop_agent.db)",
    )
    parser.add_argument(
        "--candidate-json", default=None,
        help="JSON string of a FreightDetailCandidate (for programmatic use; overrides --text)",
    )

    args = parser.parse_args()

    # ── Build FreightDetailCandidate ─────────────────────────────────
    if args.candidate_json:
        data = json.loads(args.candidate_json)
        from ops_hub.sop.freight_detail_extractor import FreightDetailCandidate
        candidate = FreightDetailCandidate(
            message_id=data.get("message_id", "cli"),
            group_id=data.get("group_id", "cli"),
            message_time=data.get("message_time", ""),
            raw_text=data.get("raw_text", ""),
            project_id=data.get("project_id", "jilin_jingang_jinzhou"),
            port=data.get("port", ""),
            cargo_name_detail=data.get("cargo_name_detail", ""),
            quantity_tons=data.get("quantity_tons", -1),
            contract_no=data.get("contract_no", ""),
            order_identifier=data.get("order_identifier", ""),
            status=data.get("status", "complete"),
            binding_status=data.get("binding_status", "needs_manual_binding"),
            source=data.get("source", "freight_detail_extractor"),
        )
    else:
        candidate = extract_freight_detail(args.text)

    # ── Execute ──────────────────────────────────────────────────────
    result = enrich_release_batch_with_freight_detail(
        candidate,
        args.release_batch_id,
        dry_run=args.dry_run,
        allow_overwrite=args.allow_overwrite,
        db_path=args.db_path,
    )

    # ── Output ───────────────────────────────────────────────────────
    output = {
        "status": result.status,
        "release_batch_id": args.release_batch_id,
        "candidate_fields": {
            "order_identifier": candidate.order_identifier,
            "contract_no": candidate.contract_no,
            "cargo_name_detail": candidate.cargo_name_detail,
            "quantity_tons": candidate.quantity_tons,
            "port": candidate.port,
            "status": candidate.status,
        },
        "planned_updates": result.planned_updates,
        "applied_fields": result.applied_fields,
        "skipped_fields": result.skipped_fields,
        "schema_missing_fields": result.schema_missing_fields,
        "message": result.message,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
