#!/usr/bin/env bash
# 一键重启常驻服务 + 拉起终端看板。
#
#   ./scripts/restart_all.sh         # 重启 sop-data-hub 服务 + 看板(日常:改了代码后)
#   ./scripts/restart_all.sh --all   # 再带上 wechat-ops-agent / rail95306-sync / qwen3vl
#
# launchd daemon 机器重启后会自动拉起(KeepAlive),但终端看板(tmux)不是 launchd
# 托管的,机器重启后必须手动拉 —— 这脚本主要解决这个 + 改代码后让服务跑上新代码。
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
UID_NUM="$(id -u)"

kick() {  # $1=label  $2=描述
  if launchctl print "gui/$UID_NUM/$1" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$UID_NUM/$1" 2>/dev/null \
      && echo "  ✓ 重启 $2 ($1)" || echo "  ✗ 重启失败 $2 ($1)"
  else
    echo "  – 未加载,跳过 $2 ($1)"
  fi
}

echo "== sop-data-hub 服务(跑本仓代码,改代码后必重启)=="
kick com.qicai21.sop-data-hub.live-service "live-service 消息入库链"
kick com.qicai21.sop-data-hub.text-watch  "text-watch 文本链"
kick com.qicai21.sop-data-hub.jiusan-sync "九三集装箱/散粮同步"
kick com.qicai21.sop-data-hub.jiusan-bulk-report-ingest "九三散粮晨报入库"
kick com.qicai21.sop-data-hub.jiusan-morning-reconcile "九三每日晨报对账"

if [ "${1:-}" = "--all" ]; then
  echo "== 其余常驻(有登录/会话/模型态,按需)=="
  kick com.qicai21.wechat-ops-agent "wechat-ops-agent 微信拉取"
  kick com.qicai21.rail95306-sync   "rail95306-sync 货票同步"
  kick com.qicai.qwen3vl.mlx        "qwen3vl VLM 模型(重载慢)"
fi

echo "== 终端看板(tmux board)=="
tmux kill-session -t board 2>/dev/null && echo "  停旧 board"
tmux new-session -d -s board "PYTHONPATH=src python3 scripts/cli_dashboard.py" \
  && echo "  ✓ 看板已起(tmux attach -t board 查看)"

sleep 4
echo "== 现状 =="
launchctl list 2>/dev/null | grep -iE "sop-data-hub|wechat-ops|rail95306|qwen3vl" \
  | awk '{printf "  pid=%-7s status=%-4s %s\n",$1,$2,$3}'
tmux has-session -t board 2>/dev/null \
  && echo "  board: 运行中" || echo "  board: ✗ 未起"
