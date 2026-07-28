#!/usr/bin/env python3
"""生成 Dashboard 数据：聚合 SQLite 中的预报/研判/实测/偏差 → dashboard/data.json
用法: python3 gen_dashboard.py [--date 2026-07-23]
"""
import argparse
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from datetime import datetime as _dt
from pathlib import Path
from zoneinfo import ZoneInfo

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
    "KATL": ("atlanta", True), "MPMG": ("panama-city", False), "KDAL": ("dallas", False),
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

# 美国城市（ICAO K 前缀）的 Polymarket 市场按 °F 整数分档，其余按 °C
US_SET = {
    "KMIA", "KLGA", "KATL", "KORD", "KDAL", "KHOU",
    "KAUS", "KBKF", "KLAX", "KSFO", "KSEA",
}


def city_unit(icao):
    return "°F" if icao in US_SET else "°C"


def to_disp(v_c, unit):
    """把 °C 值换算成该城市分档单位的整数（研判推荐展示用）。

    Polymarket 温度档按「实际最高温落在哪个整数区间」结算 = floor(actual high)：
    实测 31.9°C → 归属 31 档，买 32 错。故推介整数必须用 floor（向下取整），
    不能用 round（四舍五入会让 frac>=0.5 的预测多 +1，例如 31.8→32，而实际 31.9 仍属 31 档）。
    等价于「取整到不大于预测值的整数」，该整数即预测值所在档位 = 期望结算档。
    """
    if v_c is None:
        return None
    f = v_c * 9 / 5 + 32 if unit == "°F" else v_c
    return int(math.floor(f))


def edge_distance(v_c, unit):
    """预测值（按展示单位）距最近整数档边界的距离（floor 规则下边界在整数处）。
    <=0.2 即视为边界高风险——此时推介整数极易因微小误差翻档：
      如 f=31.9→推31，实际≥32.0(+0.1)即翻到32档；
      如 f=31.1→推31，实际<31.0(-0.1)即翻到30档。
    """
    if v_c is None:
        return None
    f = v_c * 9 / 5 + 32 if unit == "°F" else v_c
    g = f - math.floor(f)
    return min(g, 1 - g)


def remaining_hours_to_max(tz, tmax_hour, target_iso, now):
    """距城市 target_iso 当日最高温时刻(tmax_hour, 本地时区)的剩余小时；已过时取 0。"""
    try:
        z = ZoneInfo(tz)
    except Exception:
        z = timezone.utc
    y, m, d = (int(x) for x in target_iso.split("-"))
    target = _dt(y, m, d, int(tmax_hour), 0, tzinfo=z)
    now_local = now.astimezone(z)
    rem = (target - now_local).total_seconds() / 3600.0
    return rem if rem > 0 else 0.0


def _model_bias_curve(biases, model):
    """(lead_days -> bias) 曲线，供插值。"""
    out = {}
    for lead, stat in (biases.get(model, {}) or {}).items():
        out[int(lead)] = (stat or {}).get("bias", 0.0) or 0.0
    return out


def interp_bias(bias_obj, lead_frac):
    """在 (lead_days -> bias) 曲线上线性插值出分数 lead 的偏差。"""
    if not bias_obj:
        return 0.0
    leads = sorted(float(k) for k in bias_obj.keys())
    if not leads:
        return 0.0
    if lead_frac <= leads[0]:
        k0 = int(leads[0])
        return bias_obj.get(k0, bias_obj[leads[0]])
    if lead_frac >= leads[-1]:
        k1 = int(leads[-1])
        return bias_obj.get(k1, bias_obj[leads[-1]])
    for i in range(len(leads) - 1):
        a, b = leads[i], leads[i + 1]
        if a <= lead_frac <= b:
            t = (lead_frac - a) / (b - a)
            ka, kb = int(a), int(b)
            va = bias_obj.get(ka, bias_obj[a])
            vb = bias_obj.get(kb, bias_obj[b])
            return va * (1 - t) + vb * t
    return 0.0


def dynamic_predict(fc, biases, lead_frac):
    """按距最高温剩余时间(lead_frac=剩余小时/24)做逐模型偏差修正。
    返回 (修正后中位数, 置信度, 模型数, 平均修正值)。"""
    corrected, bias_sum, n = [], 0.0, 0
    for m, v in fc.items():
        if v is None:
            continue
        b = interp_bias(_model_bias_curve(biases, m), lead_frac)
        corrected.append(v - b)
        bias_sum += b
        n += 1
    if not corrected:
        return None, "n/a", 0, 0.0
    med = statistics.median(corrected)
    nn = len(corrected)
    if nn >= 4:
        srt = sorted(corrected)
        iqr = srt[3 * nn // 4] - srt[nn // 4]
    else:
        iqr = max(corrected) - min(corrected) if nn > 1 else 0.0
    conf = "高" if iqr <= 1.0 else ("中" if iqr <= 2.5 else "低")
    return round(med, 1), conf, nn, round(bias_sum / nn, 2)


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
    now = utcnow()  # 用于计算"距城市当日最高温的剩余小时"动态提前期

    conn = db()
    cities_out = []
    for c in load_cities():
        icao = c["icao"]
        unit = city_unit(icao)
        biases = bias_map(conn, icao)
        th = c.get("tmax_hour", 15)  # 该城市当日最高温出现的本地小时（默认 15:00）
        # 三天预测
        day_preds = []
        for i, dstr in enumerate(days):
            fc = latest_forecasts(conn, icao, dstr)
            rem_h = remaining_hours_to_max(c["tz"], th, dstr, now)
            lead_frac = rem_h / 24.0
            val, conf, nmod, dyn_bias = dynamic_predict(fc, biases, lead_frac)
            url, verified = pm_url(icao, dstr)
            bias_by_lead = {
                MODEL_LABEL.get(m, m): {
                    int(lead): (biases.get(m, {}).get(lead, {}) or {}).get("bias", 0.0) or 0.0
                    for lead in (biases.get(m, {}) or {})
                } for m in fc
            }
            day_preds.append({
                "date": dstr, "lead": i,
                "tmax": to_disp(val, unit),
                "tmax_disp": to_disp(val, unit),
                # 保留原始预测小数，供边界风险判断与前端展示
                "tmax_raw": round(val, 1) if val is not None else None,
                "remaining_hours": round(rem_h, 1),
                "lead_frac": round(lead_frac, 3),
                "dyn_bias": dyn_bias,
                "edge_risk": (edge_distance(val, unit) is not None
                              and edge_distance(val, unit) < 0.2),
                "edge_dist": round(edge_distance(val, unit), 2) if edge_distance(val, unit) is not None else None,
                "conf": conf, "n_models": nmod,
                "models": {MODEL_LABEL.get(m, m): round(v, 1) for m, v in
                           sorted(fc.items(), key=lambda x: MODEL_ORDER.index(x[0]) if x[0] in MODEL_ORDER else 99)},
                "bias_by_lead": bias_by_lead,
                "pm_url": url, "pm_verified": verified,
                "evolution": snapshot_evolution(conn, icao, dstr),
                "judgment_evolution": judgment_evolution(conn, icao, dstr),
            })
        # 近14天实测
        obs_rows = conn.execute(
            "SELECT local_date, tmax_final, src_final FROM obs_tmax"
            " WHERE city=? ORDER BY local_date DESC LIMIT 14", (icao,)).fetchall()
        obs_list = [{"date": r["local_date"],
                     "tmax": r["tmax_final"],
                     "tmax_disp": to_disp(r["tmax_final"], unit),
                     "src": r["src_final"]}
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
            "tmax_hour": th, "unit": unit, "lat": c.get("lat"), "lon": c.get("lon"),
            "days": day_preds, "obs": obs_list, "bias": bias_summary,
        })

    # 全局指标：最近一天综合研判 MAE
    mae_global, cnt = 0.0, 0
    for c in cities_out:
        if c["obs"] and c["days"][0]["tmax"] is not None:
            latest_obs = c["obs"][0]
            # 找当天预测对应实测（按整数档比较：floor(rec) vs floor(actual)）
            if latest_obs["tmax"] is not None:
                for d in c["days"]:
                    if d["date"] == latest_obs["date"] and d["tmax"] is not None:
                        mae_global += abs(d["tmax"] - int(math.floor(latest_obs["tmax"])))
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
