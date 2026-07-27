#!/bin/bash
# launchd 守护进程包装：激活 venv + 清空沙箱代理（改为直连），再启动 loop_collector。
# 由 ~/Library/LaunchAgents/com.wxtrack.daemon.plist 调用。
set -e
cd /Users/dt/WorkBuddy/TIANQI/wxtrack
source .venv/bin/activate
# 清空 WorkBuddy 沙箱代理变量；launchd 环境本就无代理，这里双保险确保直连。
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
exec python3 scripts/loop_collector.py --interval 3600
