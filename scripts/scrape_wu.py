#!/usr/bin/env python3
"""wunderground 日最高温抓取（playwright best-effort）。
任何失败记 N/A，不影响主流程。使用 stealth 规避 Cloudflare。
"""
import re
import sys
from datetime import date

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import setup_log

log = setup_log("scrape_wu")


def scrape_single(city, target_d):
    """抓取单个城市某日最高温，返回 float 或 None。
    city: dict with icao/name/lat/lon; target_d: date
    注意：wunderground.com 对部分 IP（含多数云服务）返回区域限制
    "This content is no longer available in your area"，此时直接返回 None。
    """
    icao = city["icao"]
    dstr = target_d.strftime("%Y-%m-%d")
    url = f"https://www.wunderground.com/history/daily/{icao}/date/{dstr}"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.debug("playwright 未安装，跳过 WU")
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ])
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=45000)

            # 等温度数据出现
            page.wait_for_selector("lib-city-history-observation", timeout=15000)

            content = page.content()
            browser.close()

            # 从内容中提取 Max Temperature
            # 通常结构: <span>Max Temperature</span><span>XX °C</span>
            # 或者表格中有 "Max Temperature" 行
            pattern = r"Max\s*Temperature[^>]*>.*?<[^>]*>\s*(\d+(?:\.\d+)?)\s*°\s*[CF]"
            m = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if m:
                val = float(m.group(1))
                return val

            # 备选: 找 temperature data-testid
            pattern2 = r'data-testid="temperatureValue"[^>]*>\s*(-?\d+(?:\.\d+)?)\s*°'
            m2 = re.search(pattern2, content)
            if m2:
                return float(m2.group(1))

            log.debug(f"{icao} {dstr} WU 页面结构未匹配到温度")
            return None
    except Exception as e:
        log.debug(f"{icao} {dstr} WU 抓取异常: {e}")
        return None


if __name__ == "__main__":
    from common import load_cities
    cities = [c for c in load_cities() if c["icao"] == "ZBAA"]
    r = scrape_single(cities[0], date.today())
    print(f"ZBAA today WU tmax: {r}")
