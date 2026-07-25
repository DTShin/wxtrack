#!/usr/bin/env python3
"""生成 Dashboard 数据：聚合 SQLite 中的预报/研判/实测/偏差 → dashboard/data.json
用法: python3 gen_dashboard.py [--date 2026-07-23]
"""
import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, load_cities, load_models, setup_log, utcnow, iso

log = setup_log("gen_dashboard")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard" / "data.json"

MONTHS = ["january","february","march","april","may","june","july",
          "august","september","october","november","december"]

# Polymarket 城市 slug 映射（verified=已验证市场存在）
POLYMARKET = {
    "ZBAA": ("beijing", True), "ZSPD": ("shanghai", True), "RJTT": ("tokyo", True),
    "RKSI": ("seoul", True), "KLGA": ("nyc", True), "KMIA": ("miami", True),
    "KORD": ("chicago", True), "KLAX": ("los-angeles", True), "LIMC": ("milan", True),
    "EGLC": ("london", True),
    # 未验证（按命名规则猜测）
    "NZWN": ("wellington", False), "RKPK": ("busan", False),
    "ZUUU": ("chengdu", False), "ZUCK": ("chongqing", False), "ZGGG": ("guangzhou", False),
    "WMKK": ("kuala-lumpur", False), "RPLL": ("manila", False), "ZSQD": ("qingdao", False),
    "ZGSZ": ("shenzhen", False), "RCSS": ("taipei", False), "ZHHH": ("wuhan", False),
    "WSSS": ("singapore", False), "VILK": ("lucknow", False), "OPKC": ("karachi", False),
    "LTAC": ("ankara", False), "OEJN": ("jeddah", False), "EFHK": ("helsinki", False),
    "LTFM": ("istanbul", False), "UUWW": ("moscow", False), "LLBG": ("tel-aviv", False),
    "EHAM": ("amsterdam", False), "FACT": ("cape-town", False), "EPWA": ("warsaw", False),
    "LEMD": ("madrid", False), "EDDM": ("munich", False), "LFPB": ("paris", False),
    "SAEZ": ("buenos-aires", False), "SBGR": ("sao-paulo", False), "CYYZ": ("toronto", False),
    "KATL": ("atlanta", False), "MPMG": ("panama-city", False), "KDAL": ("dallas", False),
    "KHOU": ("houston", False), "KAUS": ("austin", False), "KBKF": ("denver", False),
    "MMMX": ("mexico-city", False), "KSFO": ("san-francisco", False), "KSEA": ("seattle", False),
}

MODEL_ORDER = ["ecmwf_ifs025", "ecmwf_aifs025", "gfs_seamless", "icon_seamless",
               "icon_eu", "chmi_aladin_seamless", "jma_msm", "jma_gsm",
               "gem_seamless", "ukmo_seamless", "kma_seamless",
               "meteofrance_seamless", "meteoblue"]
MODEL_LABEL = {
    "ecmwf_ifs025": "ECMWF IFS", "ecmwf_aifs025": "ECMWF AIFS", "gfs_seamless": "GFS",
    "icon_seamless": "ICON", "icon_eu": "ICON-EU", "chmi_aladin_seamless": "ALADIN",
    "jma_msm": "MSM", "jma_gsm": "GSM", "gem_seamless": "GEM", "ukmo_seamless": "UKMO",
    "kma_seamless": "KMA", "meteofrance_seamless": "ARPEGE", "meteoblue": "meteoblue",
}


def pm_url(icao, dstr):
    """生成 Polymarket 市场 URL"""
    info = POLYMARKET.get(icao)
    if not info:
        return None, False
    slug, verified = info
    d = date.fromisoformat(dstr)
    url = (f"https://polymarket.com/zh/event/highest-temperature-in-{slug}"
           f"-on-{MONTHS[d.month-1]}-{d.day}-{d.year}")
    return url, verified


def latest_forecasts(conn, icao, target_date):
    """某城某日各模型最新预报 {model: tmax}，及所属 run_slot"""
    rows = conn.execute(
        "SELECT model, tmax, collected_at FROM forecast_snapshot"
        " WHERE city=? AND target_date=? AND tmax IS NOT NULL"
        " ORDER BY collected_at DESC", (icao, target_date)).fetchall()
    out, seen = {}, set()
    for r in rows:
        if r["model"] not in seen:
            seen.add(r["model"])
            out[r["model"]] = r["tmax"]
    return out


def snapshot_evolution(conn, icao, target_date):
    """同一目标日各采集时刻的预报中位数演化 [{slot, median, n}]"""
    rows = conn.execute(
        "SELECT run_slot, tmax FROM forecast_snapshot"
        " WHERE city=? AND target_date=? AND tmax IS NOT NULL"
        " ORDER BY run_slot", (icao, target_date)).fetchall()
    by_slot = {}
    for r in rows:
        by_slot.setdefault(r["run_slot"], []).append(r["tmax"])
    return [{"slot": s, "median": round(statistics.median(v), 1), "n": len(v)}
            for s, v in sorted(by_slot.items())]


def judgment_evolution(conn, icao, target_date):
    """同一目标日各采集槽的修正后研判演进 [{slot, corrected, raw, iqr, conf, n}]"""
    rows = conn.execute(
        "SELECT run_slot, corrected_median, raw_median, iqr, confidence, n_models"
        " FROM judgment_snapshot WHERE city=? AND target_date=?"
        " ORDER BY run_slot", (icao, target_date)).fetchall()
    return [{
        "slot": r["run_slot"],
        "corrected": r["corrected_median"],
        "raw": r["raw_median"],
        "iqr": r["iqr"],
        "conf": r["confidence"],
        "n": r["n_models"],
    } for r in rows]


def bias_map(conn, icao):
    """30天 bias: {model: {lead: bias}}"""
    rows = conn.execute(
        "SELECT model, lead_days, bias, mae, rmse, n FROM bias_stat"
        " WHERE city=? AND window_days=30"
        " AND stat_date=(SELECT MAX(stat_date) FROM bias_stat)",
        (icao,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["model"], {})[r["lead_days"]] = {
            "bias": r["bias"], "mae": r["mae"], "rmse": r["rmse"], "n": r["n"]}
    return out


def predict_value(fcasts, biases, lead):
    """综合研判：修正后中位数 + 离散度"""
    corrected = []
    for m, v in fcasts.items():
        b = (biases.get(m, {}).get(lead) or {}).get("bias") or 0.0
        corrected.append(v - b)
    if not corrected:
        return None, None, 0
    med = statistics.median(corrected)
    n = len(corrected)
    if n >= 4:
        srt = sorted(corrected)
        iqr = srt[3*n//4] - srt[n//4]
    else:
        iqr = max(corrected) - min(corrected) if n > 1 else 0.0
    conf = "高" if iqr <= 1.0 else ("中" if iqr <= 2.5 else "低")
    return round(med, 1), conf, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()
    days = [(today + timedelta(days=i)).isoformat() for i in range(3)]

    conn = db()
    cities_out = []
    for c in load_cities():
        icao = c["icao"]
        biases = bias_map(conn, icao)
        # 三天预测
        day_preds = []
        for i, dstr in enumerate(days):
            fc = latest_forecasts(conn, icao, dstr)
            val, conf, nmod = predict_value(fc, biases, i)
            url, verified = pm_url(icao, dstr)
            day_preds.append({
                "date": dstr, "lead": i, "tmax": val, "conf": conf, "n_models": nmod,
                "models": {MODEL_LABEL.get(m, m): round(v, 1) for m, v in
                           sorted(fc.items(), key=lambda x: MODEL_ORDER.index(x[0]) if x[0] in MODEL_ORDER else 99)},
                "pm_url": url, "pm_verified": verified,
                "evolution": snapshot_evolution(conn, icao, dstr),
                "judgment_evolution": judgment_evolution(conn, icao, dstr),
            })
        # 近14天实测
        obs_rows = conn.execute(
            "SELECT local_date, tmax_final, src_final FROM obs_tmax"
            " WHERE city=? ORDER BY local_date DESC LIMIT 14", (icao,)).fetchall()
        obs_list = [{"date": r["local_date"], "tmax": r["tmax_final"], "src": r["src_final"]}
                    for r in obs_rows]
        # 偏差修正摘要（D0 各模型）
        bias_summary = []
        for m in MODEL_ORDER:
            b = biases.get(m, {}).get(0)
            if b and b["n"] and b["n"] >= 1:
                bias_summary.append({
                    "model": MODEL_LABEL.get(m, m), "bias": b["bias"],
                    "mae": b["mae"], "rmse": b["rmse"], "n": b["n"]})
        cities_out.append({
            "icao": icao, "name": c["name"], "tz": c["tz"], "order": c["order"],
            "days": day_preds, "obs": obs_list, "bias": bias_summary,
        })

    # 全局指标：最近一天综合研判 MAE
    mae_global, cnt = 0.0, 0
    for c in cities_out:
        if c["obs"] and c["days"][0]["tmax"] is not None:
            latest_obs = c["obs"][0]
            # 找当天预测对应实测
            for d in c["days"]:
                if d["date"] == latest_obs["date"] and d["tmax"] is not None:
                    mae_global += abs(d["tmax"] - latest_obs["tmax"])
                    cnt += 1
    global_stats = {
        "cities": len(cities_out),
        "models": len(MODEL_ORDER),
        "obs_mae": round(mae_global / cnt, 2) if cnt else None,
        "obs_n": cnt,
    }

    payload = {
        "generated_at": iso(utcnow()),
        "base_date": today.isoformat(),
        "days": days,
        "global": global_stats,
        "cities": cities_out,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    log.info(f"dashboard 数据已生成: {OUT}（{len(cities_out)} 城, {OUT.stat().st_size//1024}KB）")
    conn.close()


if __name__ == "__main__":
    main()
