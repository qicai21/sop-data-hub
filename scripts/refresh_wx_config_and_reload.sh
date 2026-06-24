#!/bin/bash
# 重生成 wx-ops-agent 的监听名单(wx_config.json)+ 重启 wx daemon 让它生效。
# 背景:daemon 只在启动时读 wx_config.json;SOP yaml 改了群(加/删)后若不重跑
# exporter + 重启 daemon,daemon 会一直用旧名单(2026-06-24 实证:用了6-02的旧名单
# 22天,还在跟踪早删掉的"郭东北"、漏了后加的"九三大豆发运群")。本脚本定时兜底。
set -u
REPO="/Users/qicai21/projects/repos/sop-data-hub"
cd "$REPO" || exit 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 重生成 wx_config.json ..."
PYTHONPATH=src /opt/homebrew/bin/python3.14 -m sop_hub.sop.wx_config_exporter || {
    echo "  exporter 失败,不重启 daemon"; exit 1; }

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 重启 wx daemon 加载新名单 ..."
launchctl kickstart -k "gui/$(id -u)/com.qicai21.wechat-ops-agent" 2>&1
echo "  done"
