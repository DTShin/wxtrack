#!/usr/bin/env python3
"""综合研判预报：融合所有模型预报 + 偏差修正 → 最终每日最高温预测。

核心改进（Phase B）：每次预报采集产生的研判结果，按 (city, target_date,
lead_days, run_slot) 永久写入 judgment_snapshot 表，保证事后可追溯、可比对实测。
- 每个采集槽（run_slot）对应一次完整的预报采集，会生成一组研判行。
- 实时管线只写入「最新槽」的研判；--backfill 会回填历史上所有采集槽。
- INSERT OR REPLACE 保证幂等，可安全重复运行。
输出: 今天(D0)、明天(D+1)、后天(D+2) 的最终预测（°C + °F），按时区排序。
"""
import argparse
import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, load_cities, setup_log, utcnow, iso

log = setup_log("predict")
ROOT = Path(__file__).resolve().parent.parent
OUT_MD = ROOT / "vault" / "00_MOC" / "综合研判预报.md"
OUT_JSON = ROOT / "data" / "exports" / "predict.json"


def _load_bias_map(conn):
    """预载 (city,model,lead,window) -> bias 到内存，加速研判计算。
    优先 30 天窗口，回退全窗口(99999)，再回退 0。"""
    m = {}
    for r in conn.execute(
        "SELECT city,model,lead_days,bias,window_days FROM bias_stat"
    ).fetchall():
        m[(r["city"], r["model"], r["lead_days"], r["window_days"])] = (
            r["bias"] if r["bias"] is not None else 0.0
        )
    return m


def get_bias(bias_map, city, model, lead):
    v = bias_map.get((city, model, lead, 30))
    if v is None:
        v = bias_map.get((city, model, lead, 99999))
    return v if v is not None else 0.0


def collect_forecasts_at_slot(conn, city, target_date, lead, run_slot):
    """获取某城市某日某提前期、在某采集槽下的全部模型预报 [(model, tmax)]。"""
    rows = conn.execute(
        "SELECT model, tmax FROM forecast_snapshot"
        " WHERE city=? AND target_date=? AND lead_days=? AND run_slot=?"
        " AND tmax IS NOT NULL",
        (city, target_date, lead, run_slot),
    ).fetchall()
    return [(r["model"], r["tmax"]) for r in rows]


def confidence_from_iqr(iqr):
    if iqr is None:
        return "n/a"
    if iqr <= 1.0:
        return "High"
    if iqr <= 2.5:
        return "Medium"
    return "Low"


def predict_city_at_slot(conn, bias_map, city, target_date, lead, run_slot, now_iso):
    """针对单个采集槽计算综合研判，返回结果字典或 None。"""
    forecasts = collect_forecasts_at_slot(conn, city, target_date, lead, run_slot)
    if not forecasts:
        return None
    raw = [fc for _, fc in forecasts]
    raw_median = round(statistics.median(raw), 1)

    corrected, biases = [], []
    for model, fc in forecasts:
        b = get_bias(bias_map, city, model, lead)
        corrected.append(fc - b)
        biases.append(b)
    corrected.sort()
    n = len(corrected)

    # IQR 计算（含性四分位）
    iqr = None
    if n >= 4:
        try:
            q1, _, q3 = statistics.quantiles(corrected, n=4, method="inclusive")
            iqr = round(q3 - q1, 2)
        except statistics.StatisticsError:
            iqr = None

    # 去极端值：IQR 过滤
    filtered = corrected
    if iqr is not None and n >= 5:
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        f = [v for v in corrected if lo <= v <= hi]
        if f:
            filtered = f

    corrected_median = round(statistics.median(filtered), 1)
    avg_bias = round(sum(biases) / len(biases), 2) if biases else 0.0
    confidence = confidence_from_iqr(iqr)
    return {
        "city": city,
        "target_date": target_date,
        "lead_days": lead,
        "run_slot": run_slot,
        "collected_at": now_iso,
        "raw_median": raw_median,
        "corrected_median": corrected_median,
        "iqr": iqr,
        "confidence": confidence,
        "n_models": n,
        "avg_bias": avg_bias,
    }


def generate_judgments(conn, backfill=False):
    """计算并写入 judgment_snapshot，返回最新槽的研判结果（用于文件输出）。"""
    bias_map = _load_bias_map(conn)
    now_iso = iso(utcnow())

    if backfill:
        slots = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_slot FROM forecast_snapshot ORDER BY run_slot"
        ).fetchall()]
    else:
        slots = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_slot FROM forecast_snapshot ORDER BY run_slot DESC LIMIT 1"
        ).fetchall()]

    written = 0
    for slot in slots:
        combos = conn.execute(
            "SELECT DISTINCT city, target_date, lead_days FROM forecast_snapshot"
            " WHERE run_slot=?",
            (slot,),
        ).fetchall()
        for cmb in combos:
            j = predict_city_at_slot(
                conn, bias_map, cmb["city"], cmb["target_date"],
                cmb["lead_days"], slot, now_iso,
            )
            if j is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO judgment_snapshot"
                "(city,target_date,lead_days,run_slot,collected_at,"
                " raw_median,corrected_median,iqr,confidence,n_models,avg_bias)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (j["city"], j["target_date"], j["lead_days"], j["run_slot"],
                 j["collected_at"], j["raw_median"], j["corrected_median"],
                 j["iqr"], j["confidence"], j["n_models"], j["avg_bias"]),
            )
            written += 1
    conn.commit()
    log.info(f"研判快照写入 {written} 行 (slots={len(slots)})")

    # 最新槽结果用于文件输出
    latest = slots[-1] if slots else None
    latest_rows = []
    if latest:
        for r in conn.execute(
            "SELECT * FROM judgment_snapshot WHERE run_slot=?", (latest,)
        ).fetchall():
            latest_rows.append(dict(r))
    return latest_rows


def c_to_f(c):
    return round(c * 9 / 5 + 32, 1)


def _to_display(results, today):
    """从最新槽研判中提取 D0/D+1/D+2 展示用结构。"""
    by_key = {}
    for r in results:
        by_key[(r["lead_days"], r["target_date"])] = r
    disp = []
    cities = load_cities()
    for i in range(3):
        target = (today + timedelta(days=i)).isoformat()
        for city in cities:
            j = by_key.get((i, target))
            if not j:
                continue
            disp.append({
                "icao": city["icao"],
                "name": city["name"],
                "order": city["order"],
                "tz": city["tz"],
                "lead": i,
                "date": target,
                "tmax_c": j["corrected_median"],
                "tmax_f": c_to_f(j["corrected_median"]) if j["corrected_median"] is not None else None,
                "raw_median": j["raw_median"],
                "iqr": j["iqr"],
                "confidence": j["confidence"],
                "avg_bias": j["avg_bias"],
                "n_models": j["n_models"],
                "run_slot": j["run_slot"],
            })
    return disp


def write_md(disp, today, run_slot):
    lines = [
        "# 🌡️ 综合研判预报",
        "",
        f"*生成时间: {iso(utcnow())}  |  研判槽位: {run_slot}  |  基于 {today.isoformat()} 最新预报快照*",
        "",
        "> 融合 13 个气象模型预报 + 30 天滚动偏差修正，取去极端值后的中位数作为最终预测。",
        "> 每次预报采集的研判结果已永久写入 judgment_snapshot，供事后与实测比对。",
        "",
    ]
    for lead, label in enumerate(["📅 今天 (D0)", "📅 明天 (D+1)", "📅 后天 (D+2)"]):
        subset = [r for r in disp if r["lead"] == lead]
        target_date = subset[0]["date"] if subset else today.isoformat()
        lines.append(f"## {label} — {target_date}")
        lines.append("")
        lines.append("| # | ICAO | 城市 | 时区 | 修正后 °C | 修正后 °F | 原始中位数 | IQR | 置信度 | 平均偏差 | 模型数 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in subset:
            c_str = f"{r['tmax_c']:.1f}" if r["tmax_c"] is not None else "—"
            f_str = f"{r['tmax_f']:.1f}" if r["tmax_f"] is not None else "—"
            raw_str = f"{r['raw_median']:.1f}" if r["raw_median"] is not None else "—"
            iqr_str = f"{r['iqr']:.2f}" if r["iqr"] is not None else "—"
            bias_str = f"{r['avg_bias']:+.2f}" if r["avg_bias"] is not None else "—"
            conf = r["confidence"] or "—"
            n_str = str(r["n_models"]) if r["n_models"] > 0 else "—"
            lines.append(f"| {r['order']} | {r['icao']} | {r['name']} | {r['tz']} | {c_str} | {f_str} | {raw_str} | {iqr_str} | {conf} | {bias_str} | {n_str} |")
        lines.append("")
    lines += [
        "---",
        "*Powered by wxtrack — 多模型偏差修正预报系统*",
    ]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Markdown 已写入: {OUT_MD}")


def write_json(disp):
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(disp, f, ensure_ascii=False, indent=2)
    log.info(f"JSON 已写入: {OUT_JSON}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--backfill", action="store_true",
                    help="回填历史上所有采集槽的研判快照（幂等）")
    args = ap.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()

    conn = db()
    latest_rows = generate_judgments(conn, backfill=args.backfill)
    disp = _to_display(latest_rows, today)
    write_md(disp, today, latest_rows[0]["run_slot"] if latest_rows else "?")
    write_json(disp)

    d0 = [r for r in disp if r["lead"] == 0 and r["tmax_c"] is not None]
    print(f"\nD0 有效预测: {len(d0)} 城  | 研判槽位: {latest_rows[0]['run_slot'] if latest_rows else '-'}")
    if d0:
        s = d0[0]
        print(f"示例: {s['icao']} {s['name']} → 修正后 {s['tmax_c']}°C / {s['tmax_f']}°F (原始中位 {s['raw_median']}°C, IQR {s['iqr']}, {s['confidence']})")
    conn.close()


if __name__ == "__main__":
    main()
