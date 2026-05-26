"""SOP runtime planning package.

This package is intentionally minimal in the planning branch. Concrete runtime
implementation should be introduced by follow-up implementation orders.
"""

from ops_hub.sop.dashboard_intent import DashboardIntent, resolve_dashboard_intent
from ops_hub.sop.source_watcher import WxOpsSourceWatcher
