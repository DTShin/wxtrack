#!/bin/bash
# loop_collector 的崩溃自拉起包装（systemd 不可用时的兜底）：每 60s 检查，进程不在则重启。
# 推荐在 VPS 上直接用 systemd（见 systemd/wxtrack.service），本脚本仅作无 systemd 环境的备用。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"
PY="${WX_PYTHON:-python3}"
echo "[$(date -u +%FT%TZ)] keeper 启动"
while true; do
  PIDF="data/loop_collector.pid"
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null; then
    : # 运行中
  else
    echo "[$(date -u +%FT%TZ)] loop_collector 未在运行，重启..."
    rm -f "$PIDF"
    nohup "$PY" scripts/loop_collector.py --interval 3600 >> logs/loop_stdout.log 2>&1 &
  fi
  sleep 60
done
