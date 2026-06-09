"""SOP runtime planning package.

This package is intentionally minimal in the planning branch. Concrete runtime
implementation should be introduced by follow-up implementation orders.
"""

from sop_hub.sop.dashboard_intent import DashboardIntent, resolve_dashboard_intent
from sop_hub.sop.dashboard_payload_queue import (
    DashboardPayload,
    DashboardPayloadQueue,
    build_dashboard_payload_queue,
    resolve_dashboard_payload,
    write_dashboard_payload_queue,
)
from sop_hub.sop.source_watcher import WxOpsSourceWatcher
