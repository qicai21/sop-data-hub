"""Field contract for the minimal rail fixture schema (see support.db.init_min_rail_db)."""
from __future__ import annotations

# Columns that portable tests and common query paths may rely on.
MIN_RAIL_SHIPMENTS_COLUMNS = frozenset(
    {
        "ydid",
        "wagon_no",
        "origin_name",
        "destination_name",
        "transport_mode_name",
        "ticketed_at",
        "status_name",
        "latest_stage_key",
        "container_numbers_json",
        "raw_core_json",
        "hph",
        "marked_weight",
        "cargo_name",
    }
)
