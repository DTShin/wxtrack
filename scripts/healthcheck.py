#!/usr/bin/env python3
"""系统健康检查：验证数据完整性，异常时输出告警"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, setup_log

log = setup_log("healthcheck")

conn = db()
issues = []

# 1. 预报快照增长检查
total_fc = conn.execute("SELECT COUNT(*) FROM forecast_snapshot").fetchone()[0]
if total_fc < 100:
    issues.append(f"预报快照异常少: {total_fc} 行")

# 最近批次
latest = conn.execute("SELECT MAX(run_slot) FROM forecast_snapshot").fetchone()[0]
if latest:
    log.info(f"最新采集批次: {latest}")

# 2. 各模型覆盖
model_counts = conn.execute(
    "SELECT model, COUNT(*) as n FROM forecast_snapshot WHERE tmax IS NOT NULL GROUP BY model"
).fetchall()
model_map = {r[0]: r[1] for r in model_counts}
log.info(f"模型覆盖: {len(model_map)} 个模型有数据")
for m, n in sorted(model_map.items(), key=lambda x: -x[1]):
    log.info(f"  {m}: {n} 行")

# 3. 实测数据检查
obs = conn.execute("SELECT COUNT(DISTINCT city), MIN(local_date), MAX(local_date) FROM obs_tmax").fetchone()
log.info(f"实测: {obs[0]} 城, 日期 {obs[1]} ~ {obs[2]}")

# 4. 城市覆盖
fc_cities = conn.execute("SELECT COUNT(DISTINCT city) FROM forecast_snapshot").fetchone()[0]
if fc_cities < 48:
    issues.append(f"预报城市覆盖不足: {fc_cities}/48")

# 5. 最近2天预报新鲜度
today = datetime.now().date().isoformat()
recent = conn.execute(
    "SELECT COUNT(*) FROM forecast_snapshot WHERE target_date >= ?", (today,)
).fetchone()[0]
if recent < 10:
    issues.append(f"今日预报数据过少: {recent} 行")

# 6. bias_stat 检查
bias_count = conn.execute("SELECT COUNT(*) FROM bias_stat").fetchone()[0]
log.info(f"偏差统计: {bias_count} 组")

# 输出结论
print(f"\n{'='*50}")
if issues:
    print("⚠️ 健康检查发现以下问题:")
    for i in issues:
        print(f"  - {i}")
        log.warning(i)
else:
    print("✅ 系统运行正常")
print(f"预报快照: {total_fc} 行 | 实测: {obs[0]}城 | 偏差: {bias_count}组")
print(f"{'='*50}\n")

conn.close()
