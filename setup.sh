#!/bin/bash
# wxtrack 一键部署脚本（VPS 本地环境，无沙箱/无网盘代理）
# 用法:
#   bash setup.sh                 # 创建 venv + 装依赖 + 配 token + 初始化 dashboard 仓库
#   bash setup.sh --no-venv       # 直接装到系统 python3
#   bash setup.sh --systemd       # 额外安装并启用 systemd 服务（需 root + systemd）
#
# 非交互 token 可通过环境变量传入:
#   WX_GITHUB_TOKEN=ghp_xxx  bash setup.sh --systemd
#   WX_DASHBOARD_REPO=owner/repo   # 可选，默认 DTShin/weather-dashboard
set -euo pipefail
PKG="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PKG"

echo "==> wxtrack 部署目录: $PKG"

# 1) Python 版本检查
PY=$(command -v python3)
if [ -z "$PY" ]; then echo "ERROR: 未找到 python3"; exit 1; fi
PV=$("$PY" -c "import sys;print('%d.%d'%sys.version_info[:2])")
echo "==> 使用 python3 ($PV)"
if [ "$("$PY" -c "import sys;print(sys.version_info[:2]>= (3,10))")" != "True" ]; then
  echo "ERROR: 需要 Python >= 3.10（推荐 3.11）"; exit 1
fi

# 2) 虚拟环境（默认创建，--no-venv 跳过）
if [ "${1:-}" = "--no-venv" ]; then
  VPY="$PY"
else
  if [ ! -x "$PKG/.venv/bin/python" ]; then
    echo "==> 创建虚拟环境 .venv"
    "$PY" -m venv "$PKG/.venv"
  fi
  VPY="$PKG/.venv/bin/python"
  "$VPY" -m pip install --quiet --upgrade pip
fi
echo "==> 安装依赖"
"$VPY" -m pip install --quiet -r "$PKG/requirements.txt"
echo "    依赖安装完成"

# 3) 密钥目录
mkdir -p "$PKG/.secrets"
if [ -z "${WX_GITHUB_TOKEN:-}" ]; then
  if [ -f "$PKG/.secrets/github_token" ]; then
    echo "==> 已存在 .secrets/github_token，跳过"
  else
    read -rsp "请输入 GitHub Personal Access Token (repo 权限): " TOK
    echo
    printf '%s' "$TOK" > "$PKG/.secrets/github_token"
  fi
else
  printf '%s' "$WX_GITHUB_TOKEN" > "$PKG/.secrets/github_token"
fi
chmod 600 "$PKG/.secrets/github_token"
REPO="${WX_DASHBOARD_REPO:-DTShin/weather-dashboard}"

# 4) 初始化 dashboard git 仓库（GitHub Pages 源）
if [ ! -d "$PKG/dashboard/.git" ]; then
  echo "==> 初始化 dashboard git 仓库 ($REPO)"
  ( cd "$PKG/dashboard" && git init -q && git remote add origin "https://oauth2:$(cat "$PKG/.secrets/github_token")@github.com/${REPO}.git" )
fi

# 5) 初始化数据库（首次运行自动建表；已有 wxtrack.db 则保留）
"$VPY" -c "import sys;sys.path.insert(0,'$PKG/scripts');import common;common.db();print('==> 数据库就绪:',common.DB_PATH)"

# 6) 冒烟测试
echo "==> 健康检查"
"$VPY" "$PKG/scripts/healthcheck.py" 2>&1 | tail -5 || true

# 7) systemd（可选）
if [ "${1:-}" = "--systemd" ] || [ "${2:-}" = "--systemd" ]; then
  if [ "$(id -u)" -ne 0 ]; then echo "WARN: --systemd 需 root，跳过"; else
    if command -v systemctl >/dev/null 2>&1; then
      echo "==> 安装 systemd 服务"
      sed -e "s|__PKG__|$PKG|g" -e "s|__PY__|$VPY|g" -e "s|__USER__|root|g" \
        "$PKG/systemd/wxtrack.service" > /etc/systemd/system/wxtrack.service
      systemctl daemon-reload
      systemctl enable --now wxtrack
      echo "    已启用并启动 wxtrack 服务 (journalctl -u wxtrack -f)"
    else
      echo "WARN: 未检测到 systemctl，使用 run_loop_forever.sh 兜底";
    fi
  fi
fi

cat <<EOF

============================================================
部署完成 ✅
- 项目根: $PKG
- Python: $VPY
- GitHub Pages 仓库: $REPO
- 数据库: $PKG/data/wxtrack.db

手动运行示例:
  $VPY $PKG/scripts/run_pipeline.sh forecast   # 采集+研判+看板+推送
  $VPY $PKG/scripts/run_pipeline.sh obs        # 实测采集+验证
  $VPY $PKG/scripts/run_pipeline.sh stats      # 偏差统计+回填+看板
  $VPY $PKG/scripts/run_pipeline.sh verify     # 研判准确性验证

常驻采集（每小时）:
  方式A (systemd):  systemctl start wxtrack
  方式B (无systemd): nohup bash $PKG/scripts/run_loop_forever.sh > $PKG/logs/keeper.log 2>&1 &

看板: https://$(echo $REPO | cut -d/ -f1).github.io/$(echo $REPO | cut -d/ -f2)/
详细交接文档见: $PKG/交接文档_HANDOFF.md
============================================================
EOF
