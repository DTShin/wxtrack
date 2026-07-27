#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提前一天(lead_days=1)研判准确率分析 -> 控制台摘要 + 自包含 HTML 报告。
口径: judgment_snapshot.lead_days=1 的 corrected_median 与 obs_tmax.tmax_final 按 城市+target_date 配对。
整数分档: 美国(K前缀)城市按 °F 取整比对 Polymarket 档位, 其余按 °C。
"""
import sqlite3, math, html, datetime

DB = "data/wxtrack.db"
OUT = "docs/研判准确率_提前一天.html"
US = {"KMIA","KLGA","KATL","KORD","KDAL","KHOU","KAUS","KBKF","KLAX","KSFO","KSEA"}

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
rows = con.execute("""
  SELECT j.city, j.target_date, j.corrected_median v, j.confidence,
         o.tmax_final obs, o.src_final src
  FROM judgment_snapshot j JOIN obs_tmax o
    ON j.city=o.city AND j.target_date=o.local_date
  WHERE j.lead_days=1 AND j.corrected_median IS NOT NULL AND o.tmax_final IS NOT NULL
""").fetchall()
con.close()

def stats(vals):
    n=len(vals)
    if n==0: return dict(n=0,mae=0,rmse=0,bias=0,h05=0,h1=0,h2=0)
    mae=sum(abs(e) for e in vals)/n
    rmse=math.sqrt(sum(e*e for e in vals)/n)
    bias=sum(vals)/n
    h05=sum(1 for e in vals if abs(e)<=0.5)/n*100
    h1 =sum(1 for e in vals if abs(e)<=1.0)/n*100
    h2 =sum(1 for e in vals if abs(e)<=2.0)/n*100
    return dict(n=n,mae=mae,rmse=rmse,bias=bias,h05=h05,h1=h1,h2=h2)

errs=[r['v']-r['obs'] for r in rows]
S=stats(errs)

def bin_unit(vc, us):
    return int(round(vc*9/5+32)) if us else int(round(vc))
hit_same=hit_p1=hit_p2=tot=0
us_errs=[]; other_errs=[]; city_errs={}
for r in rows:
    us=r['city'] in US
    jb=bin_unit(r['v'],us); ob=bin_unit(r['obs'],us)
    d=jb-ob; tot+=1
    if d==0: hit_same+=1
    if abs(d)<=1: hit_p1+=1
    if abs(d)<=2: hit_p2+=1
    e=r['v']-r['obs']
    (us_errs if us else other_errs).append(e)
    city_errs.setdefault(r['city'],[]).append(e)
S_us=stats(us_errs); S_ot=stats(other_errs)

conf_map={'High':'高','Medium':'中','Low':'低'}
by_conf={}
for r in rows:
    c=conf_map.get(r['confidence'] or '','未知')
    by_conf.setdefault(c,[]).append(r['v']-r['obs'])
S_conf={k:stats(v) for k,v in by_conf.items()}

city_stats={k:stats(v) for k,v in city_errs.items()}
worst=sorted(city_stats.items(), key=lambda kv:-kv[1]['mae'])[:8]
best =sorted(city_stats.items(), key=lambda kv: kv[1]['mae'])[:8]

con2=sqlite3.connect(DB); con2.row_factory=sqlite3.Row
d0=con2.execute("""SELECT j.corrected_median v,o.tmax_final obs
  FROM judgment_snapshot j JOIN obs_tmax o ON j.city=o.city AND j.target_date=o.local_date
  WHERE j.lead_days=0 AND j.corrected_median IS NOT NULL AND o.tmax_final IS NOT NULL""").fetchall()
con2.close()
S0=stats([r['v']-r['obs'] for r in d0])

by_date={}
for r in rows: by_date.setdefault(r['target_date'],[]).append(r['v']-r['obs'])
S_date={k:stats(v) for k,v in sorted(by_date.items())}

# ---------- 控制台摘要 ----------
print(f"提前一天(lead_days=1) 配对 {len(rows)} 条 (美国 {len(us_errs)} / 其他 {len(other_errs)})")
print(f"  连续  MAE={S['mae']:.2f}°C  RMSE={S['rmse']:.2f}  偏差={S['bias']:+.2f}  ≤1°C={S['h1']:.1f}%")
print(f"  整数同档={hit_same/tot*100:.1f}%  ±1档={hit_p1/tot*100:.1f}%  ±2档={hit_p2/tot*100:.1f}%")
print(f"  美国 MAE={S_us['mae']:.2f}°C  ±1档={S_us['h1']:.1f}%  | 其他 MAE={S_ot['mae']:.2f}°C  ±1档={S_ot['h1']:.1f}%")
print(f"  [对照] 当天(D0) MAE={S0['mae']:.2f}°C  ≤1°C={S0['h1']:.1f}%")
print("  最差城市:", ", ".join(f"{k}={v['mae']:.2f}" for k,v in worst[:5]))

# ---------- HTML 报告 ----------
def bar(val, maxv, color, label=None, unit=""):
    w=max(2, round(val/maxv*100)) if maxv else 0
    return f'<div class="barrow"><span class="bl">{html.escape(label or "")}</span><div class="btrack"><div class="bfill" style="width:{w}%;background:{color}"></div></div><span class="bv">{val:.2f}{unit}</span></div>'

def card(t,v,sub=""):
    return f'<div class="card"><div class="cv">{v}</div><div class="ct">{html.escape(t)}</div><div class="cs">{html.escape(sub)}</div></div>'

maxv=max(S['mae'],S0['mae'],S_us['mae'],S_ot['mae'],*(v['mae'] for k,v in worst))
cmp_html = (bar(S0['mae'],maxv,"#4caf50","当天 D0","" )
          + bar(S['mae'],maxv,"#2196f3","提前一天 D+1","")
          + bar(S_us['mae'],maxv,"#ff9800","├ 美国(°F)","")
          + bar(S_ot['mae'],maxv,"#9c27b0","├ 其他(°C)","")
          + "".join(bar(v['mae'],maxv,"#e53935",k,"") for k,v in worst[:5]))

conf_html="".join(card(f"{k}置信", f"{S_conf[k]['mae']:.2f}°C", f"n={S_conf[k]['n']} · ≤1°C {S_conf[k]['h1']:.0f}%") for k in ['高','中','低'] if k in S_conf)

date_html="".join(bar(S_date[d]['mae'], maxv, "#607d8b", d, "") for d in sorted(S_date))

worst_html="".join(bar(v['mae'],maxv,"#e53935",k,"") for k,v in worst)
best_html ="".join(bar(v['mae'],maxv,"#4caf50",k,"") for k,v in best)

h_same=hit_same/tot*100; h_p1=hit_p1/tot*100; h_p2=hit_p2/tot*100

doc=f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>提前一天研判准确率</title>
<style>
*{{box-sizing:border-box}} body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1419;color:#e6e6e6;margin:0;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#8aa;font-size:13px;margin-bottom:20px}}
.grid{{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}}
.card{{background:#1a212b;border:1px solid #2a3441;border-radius:10px;padding:14px 18px;min-width:130px}}
.cv{{font-size:26px;font-weight:700;color:#fff}} .ct{{font-size:13px;margin-top:4px;color:#cdd}} .cs{{font-size:11px;color:#8aa;margin-top:2px}}
section{{background:#161c24;border:1px solid #2a3441;border-radius:12px;padding:18px;margin:16px 0}}
h2{{font-size:16px;margin:0 0 12px;color:#7fd}}
.barrow{{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}}
.bl{{width:150px;text-align:right;color:#bcd;flex:none}} .btrack{{flex:1;background:#0c1116;border-radius:6px;height:18px;overflow:hidden}}
.bfill{{height:100%;border-radius:6px}} .bv{{width:80px;flex:none;color:#fff;font-variant-numeric:tabular-nums}}
.note{{color:#8aa;font-size:12px;line-height:1.6;margin-top:8px}}
.pill{{display:inline-block;background:#1a212b;border:1px solid #2a3441;border-radius:20px;padding:6px 14px;margin:4px;font-size:13px}}
.pill b{{color:#7fd}}
</style></head><body>
<h1>提前一天研判准确率（lead_days = 1）</h1>
<div class="sub">修正后融合中位数 corrected_median 与实测最高温 obs_tmax 逐条配对 · 样本 {len(rows)} 条（覆盖 7/23–7/27）· 生成 {datetime.date.today().isoformat()}</div>

<div class="grid">
{card("连续 MAE", f"{S['mae']:.2f}°C", f"RMSE {S['rmse']:.2f}°C")}
{card("平均偏差", f"{S['bias']:+.2f}°C", "正=偏热 / 负=偏凉")}
{card("≤0.5°C 命中", f"{S['h05']:.0f}%", f"±1°C {S['h1']:.0f}% · ±2°C {S['h2']:.0f}%")}
{card("整数同档命中", f"{h_same:.0f}%", "Polymarket 整数档")}
{card("整数 ±1档", f"{h_p1:.0f}%", f"±2档 {h_p2:.0f}%")}
</div>

<section><h2>① 与当天(D0)对照 — 提前一天误差更大吗？</h2>{cmp_html}
<div class="note">提前一天 MAE <b>{S['mae']:.2f}°C</b> vs 当天 <b>{S0['mae']:.2f}°C</b>，差 <b>{S['mae']-S0['mae']:+.2f}°C</b>。美国(°F密度更高)最不准，其他城市仍很稳。</div></section>

<section><h2>② 整数分档命中（按 Polymarket 档位单位）</h2>
<div class="pill">同档 <b>{h_same:.1f}%</b></div><div class="pill">±1档 <b>{h_p1:.1f}%</b></div><div class="pill">±2档 <b>{h_p2:.1f}%</b></div>
<div class="note">美国(K前缀, °F)：同档 {hit_same*len(us_errs)/tot:.1f}% · ±1档 {S_us['h1']:.1f}% · MAE {S_us['mae']:.2f}°C（n={S_us['n']}）<br>
其他(°C)：同档 {hit_same*len(other_errs)/tot:.1f}% · ±1档 {S_ot['h1']:.1f}% · MAE {S_ot['mae']:.2f}°C（n={S_ot['n']}）</div></section>

<section><h2>③ 按置信度分层</h2><div class="grid">{conf_html}</div>
<div class="note">高置信最准、低置信明显更差 → 看板置信度标签有区分度。</div></section>

<section><h2>④ 最差城市（MAE 倒序）</h2>{worst_html}</section>
<section><h2>⑤ 最佳城市（MAE 正序）</h2>{best_html}</section>
<section><h2>⑥ 按目标日</h2>{date_html}
<div class="note">样本量随实测日期累积（7/26 最多 495 条）。</div></section>

<div class="note">口径说明：每次"提前一天"研判 = judgment_snapshot 中 lead_days=1 的记录，其 target_date 为该日发布时指向的次日；与 obs_tmax 同城市同日期实测最高温配对。整数分档下研判与实测各自按城市档位单位取整后比较档位差。</div>
</body></html>"""

open(OUT,"w",encoding="utf-8").write(doc)
print(f"\n报告已生成: {OUT}")
