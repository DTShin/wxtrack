#!/usr/bin/env python3
"""偏差统计（T+1 原则）：每个城市本地日结束后次日统计
配对预报与实测，计算 bias/MAE/RMSE，输出修正建议。
用法:
  python3 stats_bias.py [--window 30] [--out data/exports/bias.xlsx]
"""
import argparse
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, iso, load_cities, load_models, setup_log, utcnow

log = setup_log("stats_bias")


def get_last_snapshot(conn, city, model, target_date, lead):
    """获取 target_date 对应提前期 lead 的最后一���采集快照（"判定快照"）"""
    row = conn.execute(
        "SELECT tmax FROM forecast_snapshot"
        " WHERE city=? AND model=? AND target_date=? AND lead_days=? AND tmax IS NOT NULL"
        " ORDER BY collected_at DESC LIMIT 1",
        (city, model, target_date, lead)
    ).fetchone()
    return row[0] if row else None


def compute_bias(conn, window_days=30):
    """计算偏差指标，返回 bias_stat 行列表"""
    cities = load_cities()
    models_cfg = load_models()
    models = [m["id"] for m in models_cfg["models"]]
    today = datetime.now().date()
    stat_date = today.isoformat()
    rows = []
    for window in [window_days, 99999]:  # 滚动30天 + 全期
        cutoff = (today - timedelta(days=window)) if window < 99999 else date(2000, 1, 1)
        # 按 city×model×lead 分组计算
        for city in cities:
            icao = city["icao"]
            for model in models:
                for lead in range(4):
                    errs = []
                    # 查 obs 的日期列表
                    dates = conn.execute(
                        "SELECT local_date,tmax_final FROM obs_tmax"
                        " WHERE city=? AND local_date BETWEEN ? AND ? AND tmax_final IS NOT NULL",
                        (icao, cutoff.isoformat(), today.isoformat())
                    ).fetchall()
                    for drow in dates:
                        d, obs = drow["local_date"], drow["tmax_final"]
                        fc = get_last_snapshot(conn, icao, model, d, lead)
                        if fc is not None:
                            errs.append(fc - obs)
                    if not errs:
                        continue
                    n = len(errs)
                    bias = sum(errs) / n
                    mae = sum(abs(e) for e in errs) / n
                    rmse = math.sqrt(sum(e * e for e in errs) / n)
                    rows.append((stat_date, icao, model, lead, n, round(bias, 2),
                                 round(mae, 2), round(rmse, 2), window))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    conn = db()
    rows = compute_bias(conn, args.window)
    conn.executemany(
        "INSERT OR REPLACE INTO bias_stat(stat_date,city,model,lead_days,n,bias,mae,rmse,window_days)"
        " VALUES(?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()

    # 打印摘要
    today = datetime.now().date().isoformat()
    window_rows = [r for r in rows if r[8] == args.window and r[5] > 0]
    log.info(f"偏差统计完成: {len(window_rows)} 组（窗口={args.window}天）")
    top = sorted(window_rows, key=lambda r: abs(r[5]), reverse=True)[:10]
    print(f"\n{'='*60}")
    print(f"偏差统计 {today}（窗口 {args.window} 天）")
    print(f"{'='*60}")
    print(f"{'城市':6s} {'模型':22s} {'L':3s} {'n':4s} {'Bias':>6s} {'MAE':>6s} {'RMSE':>6s}")
    print("-" * 60)
    for r in top:
        print(f"{r[1]:6s} {r[2]:22s} {r[3]:1d}  {r[4]:3d}  {r[5]:+5.1f}  {r[6]:5.1f}  {r[7]:5.1f}")

    if args.out:
        import pandas as pd
        pd.DataFrame([{
            "stat_date": r[0], "city": r[1], "model": r[2], "lead_days": r[3],
            "n": r[4], "bias": r[5], "mae": r[6], "rmse": r[7], "window": r[8]
        } for r in rows]).to_excel(args.out, index=False)
        log.info(f"导出 {args.out}")

    # 输出修正建议
    print(f"\n{'='*60}")
    print("修正建议（前 10 组，fc_corrected = fc − bias30）")
    print(f"{'='*60}")
    for r in top:
        sign = "-" if r[5] > 0 else "+"
        print(f"{r[1]} {r[2]:22s} L{r[3]}: 原始预测 {sign}{abs(r[5]):.1f}°C = 修正后")

    conn.close()


if __name__ == "__main__":
    main()
