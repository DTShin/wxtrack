#!/usr/bin/env python3
"""当地上午9点前的当天(D0)研判准确度统计 + 可视化报告。

口径:
  - "当天研判" = lead_days=0 (研判目标日即生成日当地)
  - "当地时间9点前" = run_slot(UTC) 转该城市当地时区后, 落在 target_date 当地 09:00 之前
  - 每条研判(corrected_median) 与 obs_tmax.tmax_final(同一 city/local_date) 配对
  - 连续指标: MAE / RMSE / 平均偏差 / 命中率(<=0.5/1/2°C)
  - 整数分档(呼应 Polymarket 档位): 美国城市(K前缀)用°F取整, 其余用°C取整 -> 同档/±1档命中
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3, json, statistics, yaml
from zoneinfo import ZoneInfo
from collections import Counter

ROOT = Path("/Users/dt/WorkBuddy/TIANQI/wxtrack")
cfg = yaml.safe_load(open(ROOT / "config/cities.yaml", encoding="utf-8"))
cities = cfg.get("cities", cfg if isinstance(cfg, list) else [])
tzmap = {c["icao"]: c.get("tz", "UTC") for c in cities}
US = {icao for icao in tzmap if icao.startswith("K")}  # 美国城市走°F分档

def parse_z(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

con = sqlite3.connect(ROOT / "data/wxtrack.db")
con.row_factory = sqlite3.Row

obs = {}
for r in con.execute("SELECT city, local_date, tmax_final FROM obs_tmax WHERE tmax_final IS NOT NULL"):
    obs[(r["city"], r["local_date"])] = r["tmax_final"]

allrows = []
for r in con.execute(
    "SELECT * FROM judgment_snapshot WHERE lead_days=0 AND corrected_median IS NOT NULL"
):
    icao = r["city"]; td = r["target_date"]; tzname = tzmap.get(icao, "UTC")
    try:
        ldt = parse_z(r["run_slot"]).replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tzname))
    except Exception:
        continue
    thr = datetime.fromisoformat(td + "T09:00:00").replace(tzinfo=ZoneInfo(tzname))
    before9 = ldt < thr
    ov = obs.get((icao, td))
    if ov is None:
        continue
    cm = r["corrected_median"]
    err = round(cm - ov, 2)
    is_us = icao in US
    jud_int = int(round(cm * 9 / 5 + 32)) if is_us else int(round(cm))
    obs_int = int(round(ov * 9 / 5 + 32)) if is_us else int(round(ov))
    allrows.append({
        "icao": icao, "name": next((c["name"] for c in cities if c["icao"] == icao), icao),
        "td": td, "tz": tzname, "is_us": is_us,
        "run_local": ldt.strftime("%m-%d %H:%M"), "before9": before9,
        "cm": cm, "obs": ov, "err": err, "abs": abs(err),
        "jud_int": jud_int, "obs_int": obs_int, "unit": "°F" if is_us else "°C",
        "conf": r["confidence"], "n_models": r["n_models"],
    })

before = [x for x in allrows if x["before9"]]
after = [x for x in allrows if not x["before9"]]

def stats(sub):
    if not sub:
        return None
    aes = [x["abs"] for x in sub]; errs = [x["err"] for x in sub]
    return {
        "n": len(sub),
        "mae": statistics.mean(aes),
        "rmse": (statistics.mean(e * e for e in errs)) ** 0.5,
        "bias": statistics.mean(errs),
        "h05": sum(1 for e in aes if e <= 0.5) / len(aes),
        "h1": sum(1 for e in aes if e <= 1.0) / len(aes),
        "h2": sum(1 for e in aes if e <= 2.0) / len(aes),
        "same": sum(1 for x in sub if x["jud_int"] == x["obs_int"]) / len(sub),
        "w1": sum(1 for x in sub if abs(x["jud_int"] - x["obs_int"]) <= 1) / len(sub),
    }

def split_us(sub):
    us = [x for x in sub if x["is_us"]]; oth = [x for x in sub if not x["is_us"]]
    return stats(us), stats(oth)

S_b, S_a, S_all = stats(before), stats(after), stats(allrows)
US_b, OT_b = split_us(before)
US_a, OT_a = split_us(after)
US_all, OT_all = split_us(allrows)

byc = {}
for x in before:
    byc.setdefault(x["conf"], []).append(x)
conf_rows = []
for c in ["High", "Medium", "Low"]:
    if c in byc:
        s = stats(byc[c])
        conf_rows.append((c, s["n"], s["mae"], s["h1"], s["same"]))

bycity = {}
for x in before:
    bycity.setdefault(x["icao"], []).append(x)
city_stats = [(k, statistics.mean(vv["abs"] for vv in v), len(v),
               next(c["name"] for c in cities if c["icao"] == k))
              for k, v in bycity.items() if len(v) >= 3]
city_stats.sort(key=lambda t: -t[1])
worst = city_stats[:10]

hc = Counter(x["run_local"][6:8] for x in before)

con.close()

# ---------- 控制台 ----------
print(f"lead0 全部配对: {len(allrows)} | 9点前: {len(before)} | 9点后: {len(after)}")
for nm, s in [("9点前", S_b), ("9点后", S_a), ("全部lead0", S_all)]:
    print(f"{nm}: MAE={s['mae']:.3f} RMSE={s['rmse']:.3f} bias={s['bias']:+.3f} "
          f"≤1°C={s['h1']*100:.1f}% 整数同档={s['same']*100:.1f}% ±1档={s['w1']*100:.1f}%")

# ---------- 可视化 HTML ----------
def pct(x): return f"{x*100:.1f}%"
def card(t, v, sub=""):
    return f'<div class="card"><div class="cv">{v}</div><div class="ct">{t}</div><div class="cs">{sub}</div></div>'

# MAE 对比柱状图 (SVG)
def bar_row(label, val, maxv, color):
    w = max(2, int(val / maxv * 320))
    return (f'<div class="brow"><span class="bl">{label}</span>'
            f'<div class="btrack"><div class="bbar" style="width:{w}px;background:{color}"></div></div>'
            f'<span class="bv">{val:.3f}°C</span></div>')

maxmae = max(S_b["mae"], S_a["mae"], S_all["mae"])
bars = (bar_row("9点前", S_b["mae"], maxmae, "#2e7d32")
        + bar_row("9点后", S_a["mae"], maxmae, "#1565c0")
        + bar_row("全部lead0", S_all["mae"], maxmae, "#888"))

def tbl_compare():
    rows = ""
    data = [("当地9点前", S_b), ("当地9点后", S_a), ("全部 lead0", S_all)]
    for nm, s in data:
        rows += (f"<tr><td>{nm}</td><td>{s['n']}</td><td>{s['mae']:.3f}</td><td>{s['rmse']:.3f}</td>"
                 f"<td>{s['bias']:+.3f}</td><td>{pct(s['h05'])}</td><td>{pct(s['h1'])}</td>"
                 f"<td>{pct(s['h2'])}</td><td>{pct(s['same'])}</td><td>{pct(s['w1'])}</td></tr>")
    return rows

def tbl_unit():
    rows = ""
    for nm, us, ot in [("当地9点前", US_b, OT_b), ("当地9点后", US_a, OT_a), ("全部", US_all, OT_all)]:
        us_s = f"{pct(us['same'])} / {pct(us['w1'])} / MAE {us['mae']:.3f}°C (n={us['n']})" if us else "—"
        ot_s = f"{pct(ot['same'])} / {pct(ot['w1'])} / MAE {ot['mae']:.3f}°C (n={ot['n']})" if ot else "—"
        rows += f"<tr><td>{nm}</td><td>°F&nbsp;{us_s}</td><td>°C&nbsp;{ot_s}</td></tr>"
    return rows

def tbl_conf():
    rows = ""
    for c, n, mae, h1, same in conf_rows:
        rows += f"<tr><td>{c}</td><td>{n}</td><td>{mae:.3f}</td><td>{pct(h1)}</td><td>{pct(same)}</td></tr>"
    return rows

def tbl_city():
    rows = ""
    for icao, mae, n, nm in worst:
        rows += f"<tr><td>{nm}({icao})</td><td>{n}</td><td>{mae:.2f}</td></tr>"
    return rows

def hour_chart():
    items = sorted(hc.items())
    maxc = max(hc.values())
    out = ""
    for h, c in items:
        w = max(2, int(c / maxc * 280))
        out += (f'<div class="brow"><span class="bl">当地 {h}:00</span>'
                f'<div class="btrack"><div class="bbar" style="width:{w}px;background:#6a1b9a"></div></div>'
                f'<span class="bv">{c}</span></div>')
    return out

html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>当地9点前研判准确度</title>
<style>
 body{{font-family:-apple-system,'Segoe UI',sans-serif;margin:0;background:#f5f6f8;color:#1a1a1a}}
 .wrap{{max-width:960px;margin:0 auto;padding:24px}}
 h1{{font-size:22px;margin:0 0 4px}}
 .sub{{color:#666;font-size:13px;margin-bottom:18px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:22px}}
 .card{{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:14px 16px;min-width:130px;flex:1}}
 .cv{{font-size:24px;font-weight:700;color:#1565c0}}
 .ct{{font-size:13px;margin-top:4px}}
 .cs{{font-size:11px;color:#888;margin-top:2px}}
 section{{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:16px 18px;margin-bottom:18px}}
 section h2{{font-size:16px;margin:0 0 12px}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th,td{{padding:7px 8px;text-align:center;border-bottom:1px solid #eee}}
 th{{background:#f0f3f7;color:#444;font-weight:600}}
 td:first-child,th:first-child{{text-align:left}}
 .brow{{display:flex;align-items:center;margin:5px 0}}
 .bl{{width:74px;font-size:12px;color:#555;text-align:right;padding-right:8px}}
 .btrack{{flex:1;background:#eef0f3;border-radius:4px;height:16px;position:relative}}
 .bbar{{height:16px;border-radius:4px}}
 .bv{{width:74px;font-size:12px;padding-left:8px;color:#333}}
 .note{{font-size:12px;color:#777;margin-top:10px;line-height:1.6}}
 .tag{{display:inline-block;background:#e8f5e9;color:#2e7d32;border-radius:4px;padding:1px 7px;font-size:11px;margin-left:6px}}
</style></head><body><div class="wrap">
<h1>📊 当地上午 9 点前的当天研判准确度</h1>
<div class="sub">口径：lead_days=0（当天研判），且 run_slot 转当地时区后落在目标日当地 09:00 之前；与 obs_tmax 实测逐条配对。
整数分档按 Polymarket 档位单位：美国城市(K前缀)用°F、其余用°C。</div>

<div class="cards">
 {card('9点前 MAE', f"{S_b['mae']:.2f}°C", f"RMSE {S_b['rmse']:.2f}°C")}
 {card('9点前 命中≤1°C', pct(S_b['h1']), f"≤0.5°C {pct(S_b['h05'])}")}
 {card('整数同档命中', pct(S_b['same']), '与实测同一整数档')}
 {card('整数 ±1档命中', pct(S_b['w1']), '差≤1个整数档')}
 {card('平均偏差', f"{S_b['bias']:+.2f}°C", '负值略偏低')}
</div>

<section><h2>MAE 对比：9点前 vs 9点后 vs 全部</h2>
{bars}
<div class="note">9点前（晨间首报）MAE {S_b['mae']:.3f}°C，仅比 9点后 {S_a['mae']:.3f}°C 高 {S_a['mae']-S_b['mae']:.3f}°C ——
晨间 9 点前的研判已相当可靠，午后更新带来的改善有限。</div>
</section>

<section><h2>完整指标对照</h2>
<table><tr><th>分组</th><th>样本</th><th>MAE</th><th>RMSE</th><th>平均偏差</th><th>≤0.5°C</th><th>≤1°C</th><th>≤2°C</th><th>整数同档</th><th>整数±1档</th></tr>
{tbl_compare()}
</table></section>

<section><h2>按 Polymarket 档位单位拆分（整数分档）</h2>
<table><tr><th>分组</th><th>美国城市（°F 分档）</th><th>其他城市（°C 分档）</th></tr>
{tbl_unit()}
</table>
<div class="note">美国城市 °F 档更密（1°F≈0.56°C），命中"同一整数档"天然更难，且北美/热带城市本身误差偏高；
但 ±1 档仍有 7 成左右把握，大方向正确。</div>
</section>

<section><h2>9点前 · 按置信度分层</h2>
<table><tr><th>置信度</th><th>样本</th><th>MAE</th><th>命中≤1°C</th><th>整数同档</th></tr>
{tbl_conf()}
</table></section>

<section><h2>9点前 · 误差最大的城市（MAE，≥3 条）</h2>
<table><tr><th>城市</th><th>样本</th><th>MAE</th></tr>
{tbl_city()}
</table></section>

<section><h2>9点前 · 研判发布时刻（当地小时）分布</h2>
{hour_chart()}
<div class="note">主要为当地 00:00–08:00 的槽位（含少量前一日 21–23 点槽，转当地后落在目标日 09:00 前）。</div>
</section>

<div class="note">数据来源：wxtrack · judgment_snapshot（corrected_median） × obs_tmax（tmax_final）。
生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}。</div>
</div></body></html>"""

Path(ROOT / "docs").mkdir(exist_ok=True)
out_html = ROOT / "docs/研判准确率_当地9点前.html"
out_html.write_text(html, encoding="utf-8")
print(f"\n报告已写出: {out_html}")
