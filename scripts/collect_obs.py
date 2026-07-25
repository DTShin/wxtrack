#!/usr/bin/env python3
"""实测采集：METAR 逐时最高温推算（T+1 原则：每次采集前一日完整本地日）
每日 11:05 UTC 执行（北京 19:05），统计所有城市"昨日"实测最高温。
用法:
  python3 collect_obs.py [--date 2026-07-22] [--cities ZBAA,RJTT] [--skip-wu]
"""
import argparse
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, http_get, iso, load_cities, setup_log, utcnow

log = setup_log("collect_obs")
METAR_URL = "https://aviationweather.gov/api/data/metar"


def _local_date_for_obs(city, obs_dt_utc):
    """给定城市和 UTC 观测时刻，返回该观测所属的本地日期"""
    try:
        tz = ZoneInfo(city["tz"])
    except Exception:
        tz = ZoneInfo("UTC")
    local = obs_dt_utc.astimezone(tz)
    return local.date()


def _target_date(city, now_utc=None):
    """确定本次采集的目标本地日（T+1 原则）：
    在 11:00 UTC 执行时，所有城市都统计"昨日"（已完整结束的本地日）。
    11:00 UTC 对应每个城市的本地时间恰好是该城市指定的观测时刻：
      UTC+12惠灵顿23:00 → 本地日即将结束，取当日（等于昨日UTC）
      UTC-7洛杉矶04:00 → 本地日刚开始，取前一日
    实际上所有城市的 11:00 UTC 锚点对应 local_date(11:00 UTC)，
    对于 offset>=0 城市此时已近尾声（准终值），offset<0 城市已过完整日。
    统一取昨日：local_date(11:00 UTC) - 1，保证所有城市统计完整本地日。
    次日复核任务会自动修正 offset>=0 城市的终值差异。
    """
    now = now_utc or utcnow()
    tz = ZoneInfo(city["tz"])
    local_now = now.astimezone(tz)
    # T+1：永远统计前一天
    return (local_now - timedelta(days=1)).date()


def fetch_metar_tmax(city, target_d):
    """获取指定城市 METAR 逐时观测，推算本地日最高温。
    返回 (tmax, obs_count, ok)"""
    try:
        r = http_get(METAR_URL, params={
            "ids": city["icao"], "format": "json", "hours": 48
        }, timeout=30)
        data = r.json()
    except Exception as e:
        log.warning(f"{city['icao']} METAR 请求失败: {e}")
        return None, 0, False
    if not data:
        log.info(f"{city['icao']} 无 METAR 数据（可能缺报）")
        return None, 0, False
    try:
        tz = ZoneInfo(city["tz"])
    except Exception:
        tz = ZoneInfo("UTC")
    temps = []
    for obs in data:
        t = obs.get("temp")
        if t is None:
            continue
        rt = obs.get("reportTime")
        if not rt:
            continue
        try:
            obs_dt = datetime.fromisoformat(rt)
        except Exception:
            continue
        if obs_dt.tzinfo is None:
            obs_dt = obs_dt.replace(tzinfo=timezone.utc)
        if obs_dt.astimezone(tz).date() == target_d:
            temps.append(t)
    if not temps:
        return None, len(temps), False
    return max(temps), len(temps), True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--cities", default="")
    ap.add_argument("--skip-wu", action="store_true")
    args = ap.parse_args()

    now = utcnow()
    now_iso = iso(now)
    cities = load_cities()
    if args.cities:
        keep = set(args.cities.upper().split(","))
        cities = [c for c in cities if c["icao"] in keep]

    conn = db()
    n_ok, n_miss, n_conflict = 0, 0, 0

    for city in cities:
        icao = city["icao"]
        if args.date:
            target_d = date.fromisoformat(args.date)
        else:
            target_d = _target_date(city, now)

        tmax_metar, obs_cnt, ok = fetch_metar_tmax(city, target_d)
        if ok:
            log.info(f"{icao} {target_d} METAR tmax={tmax_metar}°C (n={obs_cnt})")
            n_ok += 1
        else:
            log.warning(f"{icao} {target_d} METAR 无有效观测")
            n_miss += 1

        tmax_wu = None
        conflict = 0
        if not args.skip_wu and ok:
            try:
                import scrape_wu
                tmax_wu = scrape_wu.scrape_single(city, target_d)
                if tmax_wu is not None:
                    log.info(f"{icao} {target_d} WU tmax={tmax_wu}°C")
                    if abs(tmax_wu - tmax_metar) > 2:
                        conflict = 1
                        n_conflict += 1
                        log.warning(f"{icao} 冲突: METAR={tmax_metar} vs WU={tmax_wu}")
            except Exception as e:
                log.warning(f"{icao} WU 抓取失败: {e}")

        final = tmax_metar
        src = "metar"
        if tmax_metar is None and tmax_wu is not None:
            final = tmax_wu
            src = "wu"
        conn.execute(
            "INSERT OR REPLACE INTO obs_tmax"
            "(city,local_date,tmax_metar,tmax_wu,tmax_final,src_final,conflict,collected_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (icao, str(target_d), tmax_metar, tmax_wu, final, src, conflict, now_iso))
        time.sleep(0.3)
    conn.commit()
    log.info(f"实测完成: OK={n_ok} MISS={n_miss} CONFLICT={n_conflict}")
    conn.close()


if __name__ == "__main__":
    main()
