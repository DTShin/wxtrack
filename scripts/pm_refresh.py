#!/usr/bin/env python3
"""定时抓取 Polymarket 各城市天气事件实时价格，生成 dashboard/polymarket_prices.json 并推回 GitHub。

由 GitHub Actions 云端每 15 分钟运行，完全不依赖本机/浏览器联网。
前端(GitHub Pages)只读同源 polymarket_prices.json，彻底无 CORS、无本机网络依赖。

本地调试:
  python3 scripts/pm_refresh.py --sample      # 用内置样本事件，不联网，仅验证解析/生成/推送链路
  python3 scripts/pm_refresh.py --no-push     # 仅本地生成文件，不推送
"""
import os
import sys
import json
import base64
import time
import re
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

ROOT = os.environ.get("GITHUB_WORKSPACE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard")
REPO = os.environ.get("WX_DASHBOARD_REPO", "DTShin/weather-dashboard")
BRANCH = "main"
API = "https://api.github.com"
GAMMA = "https://gamma-api.polymarket.com"
UA = "wxtrack-pm-bot/1.0"


def get_token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t.strip()
    p = os.path.join(ROOT, ".secrets", "github_token")
    if os.path.exists(p):
        return open(p).read().strip()
    return None


def slug_of(pm_url):
    if not pm_url:
        return None
    return pm_url.split("/event/")[-1] if "/event/" in pm_url else None


def deg_from_question(q):
    if not q:
        return None
    m = re.search(r"(\d+)\s*°?\s*C", q, re.IGNORECASE)
    return int(m.group(1)) if m else None


def parse_event(ev):
    if not ev or not ev.get("markets"):
        return {"ok": False, "error": "no markets", "title": (ev or {}).get("title"),
                "resolved": False, "markets": []}
    markets = []
    for m in ev["markets"]:
        outcomes, prices = [], []
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
        except Exception:
            pass
        try:
            prices = json.loads(m.get("outcomePrices") or "[]")
        except Exception:
            pass
        yi = outcomes.index("Yes") if "Yes" in outcomes else -1
        p_yes = float(prices[yi]) if (yi >= 0 and yi < len(prices) and prices[yi] is not None) else None
        deg = deg_from_question(m.get("question"))
        if deg is None or p_yes is None:
            continue
        markets.append({
            "degree": deg,
            "p_yes": round(p_yes, 4),
            "bestBid": m.get("bestBid"),
            "bestAsk": m.get("bestAsk"),
            "lastTradePrice": m.get("lastTradePrice"),
        })
    markets.sort(key=lambda x: x["degree"])
    return {"ok": True, "title": ev.get("title"), "resolved": bool(ev.get("automaticallyResolved")),
            "markets": markets}


def best_bin(markets, tmax):
    if not markets:
        return None
    best, bd = markets[0], abs(markets[0]["degree"] - (tmax or 0))
    for m in markets[1:]:
        d = abs(m["degree"] - (tmax or 0))
        if d < bd:
            bd, best = d, m
    return best


def fetch_events(slug, retries=3):
    url = f"{GAMMA}/events?slug={urllib.parse.quote(slug)}"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
            ev = data[0] if isinstance(data, list) and data else None
            if not ev:
                return {"ok": False, "error": "empty", "markets": []}
            return parse_event(ev)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"ok": False, "error": "HTTP 404 (event not found)", "markets": []}
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)[:80]
        time.sleep(1.5 * (i + 1))
    return {"ok": False, "error": last or "unknown", "markets": []}


SAMPLE_EVENTS = {
    "Wellington": {
        "title": "Highest temperature in Wellington on July 25?", "automaticallyResolved": False,
        "markets": [
            {"question": "Will the highest temperature in Wellington be 7°C or below on July 25?", "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]', "bestAsk": 0.001, "lastTradePrice": 0.001},
            {"question": "Will the highest temperature in Wellington be 11°C on July 25?", "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]', "bestAsk": 0.002, "lastTradePrice": 0.002},
            {"question": "Will the highest temperature in Wellington be 12°C on July 25?", "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]', "bestBid": 0.999, "bestAsk": 1, "lastTradePrice": 0.999},
            {"question": "Will the highest temperature in Wellington be 13°C on July 25?", "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]', "bestAsk": 0.001, "lastTradePrice": 0.001},
            {"question": "Will the highest temperature in Wellington be 17°C or higher on July 25?", "outcomes": '["Yes","No"]', "outcomePrices": '["0","1"]', "bestAsk": 0.001, "lastTradePrice": 0.001},
        ]
    }
}


def build_prices(data, use_sample=False, workers=8):
    tasks = []
    for c in data.get("cities", []):
        icao = c.get("icao") or c.get("name")
        for dy in c.get("days", []):
            slug = slug_of(dy.get("pm_url"))
            date = dy.get("date")
            if slug and date:
                tasks.append((icao, date, slug, dy.get("tmax"), c.get("name")))
    results = {}

    def work(t):
        icao, date, slug, tmax, cname = t
        if use_sample:
            ev = SAMPLE_EVENTS.get(cname) or list(SAMPLE_EVENTS.values())[0]
            parsed = parse_event(ev)
        else:
            parsed = fetch_events(slug)
        parsed["slug"] = slug
        bb = best_bin(parsed.get("markets", []), tmax)
        return (icao, date, {
            "slug": slug,
            "resolved": parsed.get("resolved", False),
            "title": parsed.get("title"),
            "ok": parsed.get("ok", False),
            "error": parsed.get("error"),
            "best_bin": bb,
            "markets": parsed.get("markets", []),
        })

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for fu in as_completed(futs):
            icao, date, val = fu.result()
            results.setdefault(icao, {"days": {}})
            results[icao]["days"][date] = val
    for c in data.get("cities", []):
        icao = c.get("icao") or c.get("name")
        results.setdefault(icao, {"days": {}})["name"] = c.get("name")
    ok_count = sum(1 for co in results.values() for d in co["days"].values() if d.get("ok"))
    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "github-actions" if not use_sample else "sample",
        "note": "Polymarket 实时价格：best_bin 为最接近预测 tmax 的温度档位及其 Yes 概率",
        "ok_count": ok_count,
        "total": len(tasks),
        "cities": results,
    }
    return out


def get_sha(path, token):
    url = f"{API}/repos/{REPO}/contents/{urllib.parse.quote(path)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()).get("sha")
    except Exception:
        return None


def put_file(path, content, token):
    b64 = base64.b64encode(content.encode("utf-8")).decode()
    sha = get_sha(path, token)
    body = {"message": f"pm-prices: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}",
            "content": b64, "branch": BRANCH}
    if sha:
        body["sha"] = sha
    url = f"{API}/repos/{REPO}/contents/{urllib.parse.quote(path)}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": UA}, method="PUT")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status_code in (200, 201)


def main():
    use_sample = "--sample" in sys.argv
    no_push = "--no-push" in sys.argv
    data_path = os.path.join(DASH, "data.json")
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)
    prices = build_prices(data, use_sample=use_sample)
    local = os.path.join(DASH, "polymarket_prices.json")
    with open(local, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=1)
    print(f"生成 polymarket_prices.json: {prices['ok_count']}/{prices['total']} 城市-日成功, {len(prices['cities'])} 城")
    if no_push:
        print("（--no-push）未推送")
        return
    tk = get_token()
    if not tk:
        print("WARN: 未找到 GITHUB_TOKEN，仅本地生成（不推送）")
        return
    ok = put_file("dashboard/polymarket_prices.json", json.dumps(prices, ensure_ascii=False), tk)
    print("推送 prices.json:", "OK" if ok else "FAIL")


if __name__ == "__main__":
    main()
