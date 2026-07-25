#!/usr/bin/env python3
"""初始化 48 城配置：从 aviationweather.gov stationinfo 批量拉取坐标，生成 config/cities.yaml"""
import sys, time, json
import requests
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "cities.yaml"

# 种子数据：按用户指定���时区顺序（UTC+12 → UTC-7）排序
# (icao, 中文名, IANA 时区)
SEED = [
    ("NZWN", "惠灵顿", "Pacific/Auckland"),
    ("RKSI", "首尔", "Asia/Seoul"),
    ("RJTT", "东京", "Asia/Tokyo"),
    ("RKPK", "釜山", "Asia/Seoul"),
    ("ZBAA", "北京", "Asia/Shanghai"),
    ("ZUUU", "成都", "Asia/Shanghai"),
    ("ZUCK", "重庆", "Asia/Shanghai"),
    ("ZGGG", "广州", "Asia/Shanghai"),
    ("WMKK", "吉隆坡", "Asia/Kuala_Lumpur"),
    ("RPLL", "马尼拉", "Asia/Manila"),
    ("ZSQD", "青岛", "Asia/Shanghai"),
    ("ZSPD", "上海", "Asia/Shanghai"),
    ("ZGSZ", "深圳", "Asia/Shanghai"),
    ("RCSS", "台北", "Asia/Taipei"),
    ("ZHHH", "武汉", "Asia/Shanghai"),
    ("WSSS", "新加坡", "Asia/Singapore"),
    ("VILK", "勒克瑙", "Asia/Kolkata"),
    ("OPKC", "卡拉奇", "Asia/Karachi"),
    ("LTAC", "安卡拉", "Europe/Istanbul"),
    ("OEJN", "吉达", "Asia/Riyadh"),
    ("EFHK", "赫尔辛基", "Europe/Helsinki"),
    ("LTFM", "伊斯坦布尔", "Europe/Istanbul"),
    ("UUWW", "莫斯科", "Europe/Moscow"),
    ("LLBG", "特拉维夫", "Asia/Jerusalem"),
    ("EHAM", "阿姆斯特丹", "Europe/Amsterdam"),
    ("FACT", "开普敦", "Africa/Johannesburg"),
    ("EPWA", "华沙", "Europe/Warsaw"),
    ("LEMD", "马德里", "Europe/Madrid"),
    ("LIMC", "米兰", "Europe/Rome"),
    ("EDDM", "慕尼黑", "Europe/Berlin"),
    ("LFPB", "巴黎", "Europe/Paris"),
    ("EGLC", "伦敦", "Europe/London"),
    ("SAEZ", "布宜诺斯艾利斯", "America/Argentina/Buenos_Aires"),
    ("SBGR", "圣保罗", "America/Sao_Paulo"),
    ("CYYZ", "多伦多", "America/Toronto"),
    ("KMIA", "迈阿密", "America/New_York"),
    ("KLGA", "纽约", "America/New_York"),
    ("KATL", "亚特兰大", "America/New_York"),
    ("MPMG", "巴拿马城", "America/Panama"),
    ("KORD", "芝加哥", "America/Chicago"),
    ("KDAL", "达拉斯", "America/Chicago"),
    ("KHOU", "休斯顿", "America/Chicago"),
    ("KAUS", "奥斯汀", "America/Chicago"),
    ("KBKF", "丹佛", "America/Denver"),
    ("MMMX", "墨西哥城", "America/Mexico_City"),
    ("KLAX", "洛杉矶", "America/Los_Angeles"),
    ("KSFO", "旧金山", "America/Los_Angeles"),
    ("KSEA", "西雅图", "America/Los_Angeles"),
]

UA = {"User-Agent": "wxtrack-research/1.0 (weather forecast bias study)"}


def fetch_stationinfo(icaos):
    """分批调用 stationinfo，返回 {icao: {lat, lon, elev, site}}"""
    out = {}
    for i in range(0, len(icaos), 30):
        batch = icaos[i:i + 30]
        url = "https://aviationweather.gov/api/data/stationinfo"
        r = requests.get(url, params={"ids": ",".join(batch), "format": "json"},
                         headers=UA, timeout=30)
        r.raise_for_status()
        for st in r.json():
            out[st["icaoId"]] = {
                "lat": st.get("lat"), "lon": st.get("lon"),
                "elev": st.get("elev"), "site": st.get("site"),
            }
        time.sleep(1)
    return out


def main():
    icaos = [s[0] for s in SEED]
    print(f"拉取 {len(icaos)} 个站点信息…")
    info = fetch_stationinfo(icaos)
    cities, missing = [], []
    for order, (icao, name, tz) in enumerate(SEED, start=1):
        st = info.get(icao)
        if not st or st.get("lat") is None:
            missing.append(icao)
            cities.append({"icao": icao, "name": name, "tz": tz, "order": order,
                           "lat": None, "lon": None, "elev": None, "site": None})
            continue
        cities.append({"icao": icao, "name": name, "tz": tz, "order": order,
                       "lat": round(float(st["lat"]), 4),
                       "lon": round(float(st["lon"]), 4),
                       "elev": st.get("elev"), "site": st.get("site")})
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        yaml.safe_dump({"cities": cities}, f, allow_unicode=True, sort_keys=False)
    print(f"已写入 {CONFIG}，共 {len(cities)} 城")
    if missing:
        print(f"⚠️ 未取到坐标: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
