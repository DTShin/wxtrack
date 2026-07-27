#!/usr/bin/env python3
"""采集各城市「中午到下午高温时段(本地 11:00-17:00)的持续降雨概率」。
数据源：Open-Meteo 免费接口 hourly=precipitation_probability（无需 key）。
输出：
  - data/precip_cache.json：{icao: {date: {peak_prob, mean_prob, sustained, win}}}
  - 若带 --patch：把 rain 字段写回 dashboard/data.json 的每个 day 条目
判定（持续降雨）：
  window = 本地 11:00-17:00 的逐小时降水概率
  sustained = 窗口内 >=50% 的小时数 >= ceil(窗口小时数/2) 且 峰值>=50
"""
import sys, json, os, time, urllib.request, urllib.parse
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "precip_cache.json")
DATA = os.path.join(ROOT, "dashboard", "data.json")
WIN_START, WIN_END = 11, 17          # 本地高温时段
THRESH = 50                          # 降雨概率阈值(%)


def load_cities():
    with open(os.path.join(ROOT, "config", "cities.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("cities", cfg if isinstance(cfg, list) else [])


def fetch(lat, lon, tz, days=3):
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
           "&hourly=precipitation_probability&forecast_days=%d&timezone=%s"
           % (lat, lon, days, urllib.parse.quote(tz)))
    req = urllib.request.Request(url, headers={"User-Agent": "wxtrack/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def window_stats(hourly):
    times = hourly.get("time", [])
    probs = hourly.get("precipitation_probability", [])
    by_date = {}
    for t, p in zip(times, probs):
        date, hh = t.split("T")
        h = int(hh[:2])
        by_date.setdefault(date, []).append((h, p if p is not None else 0))
    out = {}
    for date, arr in by_date.items():
        win = [(h, p) for h, p in arr if WIN_START <= h <= WIN_END]
        if not win:
            continue
        vals = [p for _, p in win]
        peak = max(vals)
        mean = sum(vals) / len(vals)
        sustained = (sum(1 for p in vals if p >= THRESH) >= (len(vals) + 1) // 2) and peak >= THRESH
        out[date] = {"peak_prob": round(peak), "mean_prob": round(mean, 1),
                     "sustained": bool(sustained),
                     "win": [{"h": h, "p": p} for h, p in win]}
    return out


def main():
    patch = "--patch" in sys.argv
    cities = load_cities()
    cache = {}
    fails = []
    for c in cities:
        icao = c["icao"]
        try:
            h = fetch(c["lat"], c["lon"], c["tz"])
            cache[icao] = window_stats(h.get("hourly", {}))
        except Exception as e:
            fails.append((icao, str(e)[:80]))
        time.sleep(0.12)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[precip] 采集 {len(cities)} 城，成功 {len(cities)-len(fails)}，失败 {len(fails)}")
    for icao, e in fails:
        print(f"  FAIL {icao}: {e}")

    if patch:
        if not os.path.exists(DATA):
            print("[precip] 未找到 dashboard/data.json，跳过 patch"); return
        d = json.load(open(DATA, encoding="utf-8"))
        n = 0
        for c in d["cities"]:
            pc = cache.get(c["icao"], {})
            for day in c.get("days", []):
                st = pc.get(day.get("date"))
                day["rain"] = st if st else {"peak_prob": None, "mean_prob": None,
                                             "sustained": False, "win": []}
                n += 1
        json.dump(d, open(DATA, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[precip] 已写回 rain 字段：{n} 个 day 条目")


if __name__ == "__main__":
    main()
