#!/bin/bash
# 气象数据管线：单命令入口，供定时任务调用
# 用法:
#   bash scripts/run_pipeline.sh forecast   # 预报采集+研判(最新槽)+dashboard+推送
#   bash scripts/run_pipeline.sh obs        # 实测采集
#   bash scripts/run_pipeline.sh stats      # 偏差统计+研判回填+vault+dashboard+推送
#   bash scripts/run_pipeline.sh verify     # 研判准确性验证(每次研判 vs 实测)
#   bash scripts/run_pipeline.sh health     # 健康检查
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."

MODE="${1:-forecast}"
echo "[pipeline] mode=$MODE start $(date -u +%FT%TZ)"

case "$MODE" in
  forecast)
    python3 scripts/collect_forecast.py 2>&1 | tail -3
    python3 scripts/predict.py 2>&1 | tail -2
    python3 scripts/gen_dashboard.py 2>&1 | tail -1
    bash scripts/push_dashboard.sh 2>&1 | tail -1
    ;;
  obs)
    python3 scripts/collect_obs.py --skip-wu 2>&1 | tail -3
    python3 scripts/verify_judgment.py 2>&1 | tail -3
    ;;
  stats)
    python3 scripts/stats_bias.py --window 30 2>&1 | tail -3
    python3 scripts/predict.py --backfill 2>&1 | tail -2
    python3 scripts/gen_vault.py 2>&1 | tail -1
    python3 scripts/gen_dashboard.py 2>&1 | tail -1
    bash scripts/push_dashboard.sh 2>&1 | tail -1
    ;;
  verify)
    python3 scripts/verify_judgment.py 2>&1 | tail -5
    ;;
  health)
    python3 scripts/healthcheck.py 2>&1
    ;;
  *)
    echo "未知模式: $MODE (forecast|obs|stats|verify|health)" >&2
    exit 1
    ;;
esac

echo "[pipeline] mode=$MODE done $(date -u +%FT%TZ)"
