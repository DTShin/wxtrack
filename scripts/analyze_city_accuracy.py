#!/usr/bin/env python3
"""城市级研判准确度：把 judgment_snapshot.corrected_median 与 obs_tmax 实测逐条配对，
按城市(可选)计算连续 MAE + 整数°C分档命中率。"""
import sqlite3, sys, json
from collections import defaultdict

DB = "data/wxtrack.db"
US = {"KMIA","KLGA","KATL","KORD","KDAL","KHOU","KAUS","KBKF","KLAX","KSFO","KSEA"}

def load(icao_filter=None):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    # 实测
    obs = {}
    for r in con.execute("SELECT city, local_date, tmax_final FROM obs_tmax WHERE tmax_final IS NOT NULL"):
        obs[(r["city"], r["local_date"])] = r["tmax_final"]
    # 研判（lead_days=0 当天；也统计全部提前期）
    rows = con.execute(
        "SELECT city, target_date, lead_days, corrected_median, confidence, run_slot "
        "FROM judgment_snapshot WHERE corrected_median IS NOT NULL").fetchall()
    con.close()
    pairs = []
    for r in rows:
        key = (r["city"], r["target_date"])
        if key in obs:
            pairs.append(dict(
                icao=r["city"], target=r["target_date"], lead=r["lead_days"],
                pred=r["corrected_median"], obs=obs[key],
                conf=r["confidence"], run_slot=r["run_slot"]))
    if icao_filter:
        pairs = [p for p in pairs if p["icao"] == icao_filter]
    return pairs

def stats(pairs, bin_unit):
    if not pairs: return None
    errs = [p["pred"] - p["obs"] for p in pairs]
    n = len(errs)
    mae = sum(abs(e) for e in errs)/n
    rmse = (sum(e*e for e in errs)/n)**0.5
    bias = sum(errs)/n
    le = lambda t: sum(1 for e in errs if abs(e) <= t)/n
    # 整数分档命中
    same = sum(1 for p in pairs if int(round(p["pred"])) == int(round(p["obs"])))/n
    pm1 = sum(1 for p in pairs if abs(int(round(p["pred"])) - int(round(p["obs"]))) <= 1)/n
    return dict(n=n, mae=mae, rmse=rmse, bias=bias,
                le05=le(0.5), le1=le(1.0), le2=le(2.0),
                bin_same=same, bin_pm1=pm1, bin_unit=bin_unit)

def by_conf(pairs):
    g = defaultdict(list)
    for p in pairs: g[p["conf"]].append(p)
    out = {}
    for k, v in g.items():
        out[k] = stats(v, "°C")
    return out

def main():
    icaos = [a for a in sys.argv[1:]]
    names = {"LTAC":"安卡拉","ZGGG":"广州"}
    for icao in icaos:
        pairs = load(icao)
        unit = "°F" if icao in US else "°C"
        s = stats(pairs, unit)
        print(f"\n===== {names.get(icao,icao)} ({icao})  样本={s['n']} =====")
        print(f"  连续 MAE={s['mae']:.3f}°C  RMSE={s['rmse']:.3f}°C  平均偏差={s['bias']:+.3f}°C")
        print(f"  命中 ≤0.5°C={s['le05']*100:.1f}%  ≤1°C={s['le1']*100:.1f}%  ≤2°C={s['le2']*100:.1f}%")
        print(f"  整数同档命中={s['bin_same']*100:.1f}%  整数±1档={s['bin_pm1']*100:.1f}%  (分档单位 {unit})")
        # 按置信度
        bc = by_conf(pairs)
        print("  按置信度:")
        for k in ("high","medium","low"):
            if k in bc and bc[k]:
                ss = bc[k]
                print(f"    {k}: n={ss['n']} MAE={ss['mae']:.3f}°C 命中≤1°C={ss['le1']*100:.1f}%")
        # 按天趋势
        byday = defaultdict(list)
        for p in pairs: byday[p["target"]].append(p)
        print("  按实测日:")
        for d in sorted(byday):
            ss = stats(byday[d], unit)
            print(f"    {d}: n={ss['n']} MAE={ss['mae']:.3f}°C 命中≤1°C={ss['le1']*100:.1f}%")

if __name__ == "__main__":
    main()
