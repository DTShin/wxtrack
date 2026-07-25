#!/usr/bin/env python3
"""meteoblue 网页抓取（best-effort）：搜索接口解析城市页 URL -> 周预报页解析每日最高温
任何失败只记日志/N/A，不中断主流程。
"""
import re
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from bs4 import BeautifulSoup

import sys
sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import ROOT, load_models, setup_log

log = setup_log("scrape_meteoblue")
CACHE = ROOT / "config" / "meteoblue_urls.yaml"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
SEARCH = "https://www.meteoblue.com/en/server/search/query3"
WEEK = "https://www.meteoblue.com/en/weather/week/{slug}"


def _load_cache():
    if CACHE.exists():
        return yaml.safe_load(open(CACHE, encoding="utf-8")) or {}
    return {}


def _save_cache(c):
    CACHE.parent.mkdir(exist_ok=True)
    yaml.safe_dump(c, open(CACHE, "w", encoding="utf-8"), allow_unicode=True)


def resolve_slug(city):
    """用搜索接口找到城市页 slug；优先机场名，其次城市名+国家"""
    queries = [f"{city['icao']}", f"{city.get('site') or city['name']} airport"]
    for q in queries:
        try:
            r = requests.get(SEARCH, params={"query": q, "itemsPerPage": 8,
                                             "lang": "en", "iso": "none"},
                             headers=UA, timeout=20)
            if r.status_code != 200:
                continue
            results = r.json().get("results", [])
            if not results:
                continue
            # 优先选坐标最接近的结果（误差 < 0.5°）
            best, bestd = None, 99
            for res in results:
                try:
                    d = abs(float(res["lat"]) - city["lat"]) + abs(float(res["lon"]) - city["lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                if d < bestd:
                    best, bestd = res, d
            if best and bestd < 0.5:
                return best["url"]
        except Exception as e:
            log.debug(f"{city['icao']} 搜索失败 {q}: {e}")
        time.sleep(0.5)
    return None


def parse_week(html):
    """解析周预报页 -> {YYYY-MM-DD: tmax(float)}"""
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tab in soup.select("div.tab[id^='day']"):
        t = tab.select_one("time.date")
        mx = tab.select_one(".tab-temp-max")
        if not t or not mx:
            continue
        dstr = t.get("datetime", "").strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)", mx.get_text(" ", strip=True).replace("−", "-"))
        if dstr and m:
            out[dstr] = float(m.group(1))
    return out


def run(conn, cities, slot, now_iso, dry=False):
    cache = _load_cache()
    changed = False
    n = 0
    for city in cities:
        icao = city["icao"]
        slug = cache.get(icao)
        if not slug:
            slug = resolve_slug(city)
            if slug:
                cache[icao] = slug
                changed = True
                log.info(f"{icao} -> {slug}")
        if not slug:
            log.warning(f"{icao} 未找到 meteoblue 页面，记 N/A")
            continue
        try:
            r = requests.get(WEEK.format(slug=slug), headers=UA, timeout=30)
            if r.status_code != 200:
                log.warning(f"{icao} 周预报页 HTTP {r.status_code}")
                continue
            data = parse_week(r.text)
        except Exception as e:
            log.warning(f"{icao} 抓取失败: {e}")
            continue
        if not data:
            log.warning(f"{icao} 解析为空（页面结构可能改版）")
            continue
        today_local = datetime.now(ZoneInfo(city["tz"])).date()
        rows = []
        for dstr, tmax in data.items():
            lead = (date.fromisoformat(dstr) - today_local).days
            if 0 <= lead <= 3:
                rows.append((icao, "meteoblue", dstr, lead, tmax, slot, now_iso, "meteoblue"))
        if rows and not dry:
            conn.executemany(
                "INSERT OR REPLACE INTO forecast_snapshot"
                "(city,model,target_date,lead_days,tmax,run_slot,collected_at,source)"
                " VALUES(?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        time.sleep(1.0)  # 温和限速
    if changed:
        _save_cache(cache)
    conn.commit()
    return n


if __name__ == "__main__":
    from common import db, iso, utcnow, current_slot, load_cities
    conn = db()
    n = run(conn, load_cities(), current_slot(), iso(utcnow()))
    print(f"meteoblue 写入 {n} 行")
