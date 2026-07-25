#!/bin/bash
# Dashboard 数据自动推送到 GitHub Pages
# 用法: bash scripts/push_dashboard.sh
# 依赖: $ROOT/.secrets/github_token  (一行，纯 token)
#       $WX_DASHBOARD_REPO          (可选，默认 DTShin/weather-dashboard)
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
TOKEN_FILE="$ROOT/.secrets/github_token"
DASH="$ROOT/dashboard"

if [ ! -f "$TOKEN_FILE" ]; then
    echo "ERROR: 未找到 GitHub 令牌文件 $TOKEN_FILE" >&2
    exit 1
fi
TOKEN=$(cat "$TOKEN_FILE")
REPO="${WX_DASHBOARD_REPO:-DTShin/weather-dashboard}"
REMOTE="https://oauth2:${TOKEN}@github.com/${REPO}.git"

cd "$DASH"
# dashboard 目录需是 git 仓库（setup.sh 已 init；若无则补建）
if [ ! -d .git ]; then
    git init -q
    git remote add origin "$REMOTE"
fi
git remote set-url origin "$REMOTE" 2>/dev/null

# 有变更才提交
git add -A
if git diff --cached --quiet; then
    echo "无数据变更，跳过推送"
    exit 0
fi
TS=$(date -u +"%Y-%m-%dT%H:%MZ")
git -c user.name="wxtrack-bot" -c user.email="bot@dtshin.xyz" commit -m "data: ${TS}"
if ! git push origin main > /tmp/push_dashboard_out.log 2>&1; then
    echo "ERROR: git push 失败" >&2
    cat /tmp/push_dashboard_out.log >&2
    exit 1
fi
grep -v "remote:" /tmp/push_dashboard_out.log | tail -2
echo "已推送: ${TS}"
