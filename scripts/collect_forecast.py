#!/usr/bin/env python3
"""预报采集：Open-Meteo 多模型日最高温快照 + meteoblue 网页（best-effort）
用法:
  python3 collect_forecast.py [--slot auto|YYYY-MM-DDTHH:30Z] [--cities ZBAA,RJTT]
                              [--models ecmwf_ifs025,...] [--skip-meteoblue] [--dry-run]
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, http_get, iso, load_cities, load_models, setup_log, utcnow, current_slot

log = setup_log("collect_forecast")
OM_URL = "https://api.open-meteo.com/v1/forecast"


def om_request(city, model_ids):
    """一次请求某城市的多个模型，返回 {model_id: {date: tmax}}"""
    r = http_get(OM_URL, params={
        "latitude": city["lat"], "longitude": city["lon"],
        "daily": "temperature_2m_max",
        "models": ",".join(model_ids),
        "forecast_days": 4,
        "timezone": city["tz"],
    }, timeout=40)
    d = r.json()
    if d.get("error"):
        log.warning(f"{city['icao']} {model_ids}: {d.get('reason', '')[:100]}")
        return {m: {} for m in model_ids}
    daily = d.get("daily", {})
    times = daily.get("time", [])
    out = {}
    for m in model_ids:
        key = f"temperature_2m_max_{m}"
        if key not in daily:  # 单模型时键名不带后缀
            key = "temperature_2m_max" if len(model_ids) == 1 else None
        vals = daily.get(key) if key else None
        out[m] = {t: v for t, v in zip(times, vals or []) if v is not None}
    return out


def collect_city(conn, city, models_cfg, regions, slot, now_iso, dry=False):
    icao = city["icao"]
    rows = []
    global_models = [m["id"] for m in models_cfg["models"]
                     if m["scope"] == "global" and m["source"] == "open-meteo"]
    regional = [m["id"] for m in models_cfg["models"]
                if m["source"] == "open-meteo" and m["scope"] in ("europe", "japan")
                and icao in regions.get(m["scope"], [])]
    try:
        res = om_request(city, global_models + regional)
    except Exception as e:
        log.error(f"{icao} Open-Meteo 请求失败: {e}")
        res = {}
    today_local = datetime.now(ZoneInfo(city["tz"])).date()
    for m in global_models + regional:
        mres = res.get(m, {})
        if mres:
            for dstr, tmax in mres.items():
                lead = (date.fromisoformat(dstr) - today_local).days
                if 0 <= lead <= 3:
                    rows.append((icao, m, dstr, lead, tmax, slot, now_iso, "open-meteo"))
        else:  # 无数据：记一条 N/A（D+1 为代表），便于追踪模型覆盖
            d1 = (today_local + timedelta(days=1)).isoformat()
            rows.append((icao, m, d1, 1, None, slot, now_iso, "open-meteo"))
    if not dry:
        conn.executemany(
            "INSERT OR REPLACE INTO forecast_snapshot"
            "(city,model,target_date,lead_days,tmax,run_slot,collected_at,source)"
            " VALUES(?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="auto")
    ap.add_argument("--cities", default="")
    ap.add_argument("--models", default="")
    ap.add_argument("--skip-meteoblue", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    slot = current_slot() if args.slot == "auto" else args.slot
    now_iso = iso(utcnow())
    cities = load_cities()
    if args.cities:
        keep = set(args.cities.upper().split(","))
        cities = [c for c in cities if c["icao"] in keep]
    cfg = load_models()
    if args.models:
        keep = set(args.models.split(","))
        cfg["models"] = [m for m in cfg["models"] if m["id"] in keep]
    regions = cfg.get("regions", {})

    conn = db()
    total = 0
    for c in cities:
        total += collect_city(conn, c, cfg, regions, slot, now_iso, args.dry_run)
        time.sleep(0.3)  # 温和限速
    conn.commit()
    log.info(f"Open-Meteo 快照完��� slot={slot}，写入 {total} 行（{len(cities)} 城）")

    if not args.skip_meteoblue and any(m["source"] == "meteoblue" for m in cfg["models"]):
        try:
            import scrape_meteoblue
            n = scrape_meteoblue.run(conn, cities, slot, now_iso, dry=args.dry_run)
            log.info(f"meteoblue 快照完成，写入 {n} 行")
        except Exception as e:
            log.error(f"meteoblue 采集失败（不影响主流程）: {e}")
    conn.close()


if __name__ == "__main__":
    main()
