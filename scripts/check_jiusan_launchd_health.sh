#!/usr/bin/env bash
set -euo pipefail

agents_dir="$HOME/Library/LaunchAgents"
labels=(
  "com.qicai21.sop-data-hub.live-service"
  "com.qicai21.sop-data-hub.text-watch"
  "com.qicai21.sop-data-hub.jiusan-sync"
  "com.qicai21.sop-data-hub.jiusan-bulk-report-ingest"
  "com.qicai21.sop-data-hub.jiusan-morning-reconcile"
  "com.qicai21.sop-data-hub.status-sync"
)
rc=0
for label in "${labels[@]}"; do
  plist="$agents_dir/$label.plist"
  if [[ ! -f "$plist" ]] || ! plutil -lint "$plist" >/dev/null 2>&1; then
    echo "INVALID_OR_MISSING $label $plist"
    rc=1
    continue
  fi
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    echo "LOADED $label"
  else
    echo "NOT_LOADED $label"
    rc=1
  fi
done
exit "$rc"
