#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
agents_dir="$HOME/Library/LaunchAgents"
domain="gui/$(id -u)"
labels=(
  "com.qicai21.sop-data-hub.live-service"
  "com.qicai21.sop-data-hub.text-watch"
  "com.qicai21.sop-data-hub.jiusan-sync"
  "com.qicai21.sop-data-hub.jiusan-bulk-report-ingest"
)

mkdir -p "$agents_dir"
for label in "${labels[@]}"; do
  src="$repo_root/deploy/launchd/$label.plist"
  dst="$agents_dir/$label.plist"
  plutil -lint "$src" >/dev/null
  if [[ -e "$dst" ]] && ! plutil -lint "$dst" >/dev/null 2>&1; then
    cp "$dst" "$dst.invalid.$(date +%Y%m%d%H%M%S).bak"
  fi
  cp "$src" "$dst"
  launchctl bootout "$domain/$label" 2>/dev/null || true
  launchctl bootstrap "$domain" "$dst"
  echo "loaded $label"
done
