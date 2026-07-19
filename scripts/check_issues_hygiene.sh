#!/usr/bin/env bash
# 工单目录卫生检查：根目录不得堆已完成；状态须落在合法枚举。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)/docs/issues"
cd "$ROOT"

LEGAL='待处理|进行中|部分完成|暂缓|待设计|已完成|已取消'
fail=0

echo "== root tickets (should be open-only) =="
count=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  count=$((count + 1))
  st=$(rg -n --max-count=1 '(?:\*\*)?状态(?:\*\*)?[：:]\s*(.+)' "$f" | head -1 | sed -E 's/.*状态(\*\*)?[：:]\s*//')
  st=${st%% — *}
  st=${st%% - *}
  st=$(printf '%s' "$st" | sed -E 's/\*\*//g; s/[[:space:]]+$//')
  if printf '%s' "$st" | rg -q "^(已完成|已处理|已修复|已取消)"; then
    echo "FAIL claimed_done still in root: $f  status=$st"
    fail=1
  fi
  if [ -n "$st" ] && ! printf '%s' "$st" | rg -q "^($LEGAL)($| |—|-)"; then
    echo "WARN non-enum status: $f  status=$st"
  fi
done < <(find . -maxdepth 1 -name '*.md' ! -name 'README.md' | sed 's|^\./||' | sort)

echo "count=$count"

echo
echo "== archived count =="
find archived -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | awk '{print "count="$1}'

if [ "$fail" -ne 0 ]; then
  echo
  echo "hygiene FAILED"
  exit 1
fi
echo
echo "hygiene OK"
