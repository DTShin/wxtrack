#!/bin/bash
# launchd 本地看板服务包装：激活 venv + 清空沙箱代理（改为直连），再启动 http.server。
# 由 ~/Library/LaunchAgents/com.wxtrack.dashboard.plist 调用。
set -e
cd /Users/dt/WorkBuddy/TIANQI/wxtrack
source .venv/bin/activate
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
exec python3 -m http.server 8765 --directory dashboard
