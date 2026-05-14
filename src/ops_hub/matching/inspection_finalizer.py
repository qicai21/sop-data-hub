from __future__ import annotations

"""Deprecated compatibility shim for the old inspection finalizer name.

Use ops_hub.matching.inspection_95306_reconciler.reconcile_inspection_shipments instead.
The old finalize_inspection_candidates entrypoint accepts run_mode='dry_run' for one
compatibility round and maps it to run_mode='plan'.
"""

from pathlib import Path
from typing import Iterable, Literal
import warnings

from ops_hub.matching.inspection_95306_reconciler import InspectionReconcileResult, reconcile_inspection_shipments

RunMode = Literal["dry_run", "plan", "commit"]


def finalize_inspection_candidates(
    *,
    business_db_path: str | Path,
    rail_db_path: str | Path,
    project_id: str,
    release_batch_id: str,
    run_mode: RunMode,
    operator_note: str,
    candidate_ids: Iterable[str] | None = None,
    inspection_json_paths: Iterable[str | Path] | None = None,
    window_minutes: int = 30,
) -> InspectionReconcileResult:
    warnings.warn(
        "finalize_inspection_candidates/finalize-inspection is deprecated; use "
        "reconcile_inspection_shipments/reconcile-inspection with run_mode='plan' or 'commit'.",
        DeprecationWarning,
        stacklevel=2,
    )
    mapped_mode = "plan" if run_mode == "dry_run" else run_mode
    return reconcile_inspection_shipments(
        business_db_path=business_db_path,
        rail_db_path=rail_db_path,
        project_id=project_id,
        release_batch_id=release_batch_id,
        run_mode=mapped_mode,  # type: ignore[arg-type]
        operator_note=operator_note,
        candidate_ids=candidate_ids,
        inspection_json_paths=inspection_json_paths,
        window_minutes=window_minutes,
    )
