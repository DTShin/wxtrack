#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纠正口径: 提前一天研判准确率。
正确定义: 研判的发布时刻(run_slot)换算到目标城市当地时区后, 落在目标日 T 的前一天(T-1) -> 这才是"提前一天发布的预测"。
对每个 (城市, T) 取 T-1 当地日内【最晚】一次发布的研判作为"用户提前一天看到的预测"(最优视角);
另算【最早】一次发布作为保守下界。与"当天(lead0)"及"之前错误口径(lead1全混)"对照。
整数分档: 美国(K前缀)用°F取整, 其余°C; 同档命中率≈Polymarket下注胜率代理。
"""
import sqlite3, math, html, datetime, yaml
from zoneinfo import ZoneInfo
from collections import defaultdict

DB="data/wxtrack.db"; OUT="docs/研判准确率_提前一天_纠正.html"
US={"KMIA","KLGA","KATL","KORD","KDAL","KHOU","KAUS","KBKF","KLAX","KSFO","KSEA"}

cfg=yaml.safe_load(open("config/cities.yaml"))
cities=cfg.get("cities", cfg if isinstance(cfg,list) else [])
tzmap={c["icao"]:c.get("tz") or "UTC" for c in cities}

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
rows=con.execute("""SELECT city,target_date,run_slot,corrected_median v,confidence
  FROM judgment_snapshot WHERE corrected_median IS NOT NULL""").fetchall()
obs={}
for r in con.execute("SELECT city,local_date,tmax_final FROM obs_tmax WHERE tmax_final IS NOT NULL"):
    obs[(r["city"], r["local_date"])]=r["tmax_final"]
con.close()

def local_pub_date(run_slot, icao):
    rs=datetime.datetime.fromisoformat(run_slot.replace("Z","+00:00"))
    tz=ZoneInfo(tzmap.get(icao,"UTC"))
    return rs.astimezone(tz).date()

# 收集候选: 所有发布在目标日之前的研判(任意提前量), 存 offset=(T-当地发布日)天数
cand=defaultdict(list)   # (city,T) -> list of (offset, run_slot, v, conf)
for r in rows:
    T=datetime.date.fromisoformat(r["target_date"])
    lp=local_pub_date(r["run_slot"], r["city"])
    off=(T-lp).days
    if off>=1:
        cand[(r["city"], r["target_date"])].append((off, r["run_slot"], r["v"], r["confidence"]))

def stats(vals):
    n=len(vals)
    if n==0: return dict(n=0,mae=0,rmse=0,bias=0,h05=0,h1=0,h2=0)
    mae=sum(abs(e) for e in vals)/n
    rmse=math.sqrt(sum(e*e for e in vals)/n)
    bias=sum(vals)/n
    return dict(n=n,mae=mae,rmse=rmse,bias=bias,
        h05=sum(1 for e in vals if abs(e)<=0.5)/n*100,
        h1=sum(1 for e in vals if abs(e)<=1.0)/n*100,
        h2=sum(1 for e in vals if abs(e)<=2.0)/n*100)

def bin_unit(vc, us): return int(round(vc*9/5+32)) if us else int(round(vc))

def eval_pick(pick):
    """pick: 'latest' 最晚 / 'earliest' 最早。返回 (errs, 同档, ±1, ±2, tot, us_errs, ot_errs)"""
    return _eval_with_offset(pick, 1)

def _eval_with_offset(pick, offset):
    """offset=1 提前一天 / 2 提前两天。run_slot当地日期 == T-offset 天。"""
    errs=[]; us_e=[]; ot_e=[]; same=p1=p2=tot=0
    for key,lst in cand.items():
        city,T=key
        if key not in obs: continue
        lst2=[x for x in lst if x[0]==offset]
        if not lst2: continue
        lst2.sort(key=lambda x:x[1])
        off,slot,v,conf = lst2[-1] if pick=="latest" else lst2[0]
        o=obs[key]
        e=v-o; errs.append(e)
        (us_e if city in US else ot_e).append(e)
        jb=bin_unit(v, city in US); ob=bin_unit(o, city in US); d=jb-ob; tot+=1
        if d==0: same+=1
        if abs(d)<=1: p1+=1
        if abs(d)<=2: p2+=1
    return errs,same,p1,p2,tot,us_e,ot_e

e_lat,slat,pl1at,p2lat,tot_lat,us_lat,ot_lat=eval_pick("latest")
e_ear,eear,pear,p2ear,tot_ear,us_ear,ot_ear=eval_pick("earliest")
S_lat=stats(e_lat); S_ear=stats(e_ear)

# 提前两天(偏移2, 最晚发布)
e2,s2,p12,p22,tot2,us2,ot2=_eval_with_offset("latest",2)
S2=stats(e2)

# 对照: 当天(lead0)
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
d0=con.execute("""SELECT j.city,j.target_date,j.run_slot,j.corrected_median v,o.tmax_final obs
  FROM judgment_snapshot j JOIN obs_tmax o ON j.city=o.city AND j.target_date=o.local_date
  WHERE j.lead_days=0 AND j.corrected_median IS NOT NULL AND o.tmax_final IS NOT NULL""").fetchall()
con.close()
S0=stats([r["v"]-r["obs"] for r in d0])

# 错误口径(lead1全混, 不区分run_slot)
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
bad=con.execute("""SELECT j.city,j.target_date,j.corrected_median v,o.tmax_final obs
  FROM judgment_snapshot j JOIN obs_tmax o ON j.city=o.city AND j.target_date=o.local_date
  WHERE j.lead_days=1 AND j.corrected_median IS NOT NULL AND o.tmax_final IS NOT NULL""").fetchall()
con.close()
S_bad=stats([r["v"]-r["obs"] for r in bad])

print("=== 纠正口径: 提前一天(当地T-1发布) ===")
print(f"  [最晚发布] 配对 {tot_lat}  MAE={S_lat['mae']:.2f}°C  RMSE={S_lat['rmse']:.2f}  偏差={S_lat['bias']:+.2f}  ≤1°C={S_lat['h1']:.1f}%")
print(f"      整数同档(胜率)={slat/tot_lat*100:.1f}%  ±1档={pl1at/tot_lat*100:.1f}%  ±2档={p2lat/tot_lat*100:.1f}%")
print(f"  [最早发布] 配对 {tot_ear}  MAE={S_ear['mae']:.2f}°C  ≤1°C={S_ear['h1']:.1f}%  (保守下界)")
print(f"  [提前两天] 配对 {tot2}  MAE={S2['mae']:.2f}°C  ≤1°C={S2['h1']:.1f}%")
print(f"  [对照] 当天D0 MAE={S0['mae']:.2f}°C  ≤1°C={S0['h1']:.1f}%")
print(f"  [错误口径 lead1全混] MAE={S_bad['mae']:.2f}°C  (此前报告的数字, 已被稀释)")
print(f"  美国(最晚) MAE={stats(us_lat)['mae']:.2f} ±1档={stats(us_lat)['h1']:.1f}% | 其他 MAE={stats(ot_lat)['mae']:.2f} ±1档={stats(ot_lat)['h1']:.1f}%")

# ---------- HTML ----------
def bar(val,maxv,color,label,unit=""):
    w=max(2,round(val/maxv*100)) if maxv else 0
    return f'<div class="barrow"><span class="bl">{html.escape(label)}</span><div class="btrack"><div class="bfill" style="width:{w}%;background:{color}"></div></div><span class="bv">{val:.2f}{unit}</span></div>'
def card(t,v,sub=""):
    return f'<div class="card"><div class="cv">{v}</div><div class="ct">{html.escape(t)}</div><div class="cs">{html.escape(sub)}</div></div>'

maxv=max(S_lat['mae'],S_ear['mae'],S2['mae'],S0['mae'],S_bad['mae'])
cmp=bar(S2['mae'],maxv,"#795548","提前两天·最晚发布","")
cmp+=bar(S_lat['mae'],maxv,"#2196f3","提前一天·最晚发布(正确)","")
cmp+=bar(S_ear['mae'],maxv,"#ff5722","提前一天·最早发布(保守)","")
cmp+=bar(S0['mae'],maxv,"#4caf50","当天 D0","")
cmp+=bar(S_bad['mae'],maxv,"#9e9e9e","之前错误口径(lead1全混)","")
note=f"""此前报告用 <code>lead_days=1 AND target_date=实测日</code> 配对, 把 <b>实测当天发布</b>(差0天)和<b>提前两天发布</b>(差2天)的研判也混了进来, 稀释了误差(MAE 仅 {S_bad['mae']:.2f}°C)。
正确口径: 研判的 run_slot 换算到城市<b>当地时区</b>后, 落在目标日<b>前一天</b>的, 才是真正的"提前一天预测"。这样 MAE 升到 <b>{S_lat['mae']:.2f}°C</b>(最晚发布) / <b>{S_ear['mae']:.2f}°C</b>(最早发布)。"""

doc=f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>提前一天研判准确率(纠正)</title><style>
*{{box-sizing:border-box}} body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f1419;color:#e6e6e6;margin:0;padding:24px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#8aa;font-size:13px;margin-bottom:16px}}
.grid{{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}}
.card{{background:#1a212b;border:1px solid #2a3441;border-radius:10px;padding:14px 18px;min-width:140px}}
.cv{{font-size:26px;font-weight:700;color:#fff}} .ct{{font-size:13px;margin-top:4px;color:#cdd}} .cs{{font-size:11px;color:#8aa;margin-top:2px}}
section{{background:#161c24;border:1px solid #2a3441;border-radius:12px;padding:18px;margin:14px 0}}
h2{{font-size:16px;margin:0 0 12px;color:#7fd}}
.barrow{{display:flex;align-items:center;gap:10px;margin:6px 0;font-size:13px}}
.bl{{width:240px;text-align:right;color:#bcd;flex:none}} .btrack{{flex:1;background:#0c1116;border-radius:6px;height:18px;overflow:hidden}}
.bfill{{height:100%;border-radius:6px}} .bv{{width:80px;flex:none;color:#fff;font-variant-numeric:tabular-nums}}
.note{{color:#ffb74d;font-size:12.5px;line-height:1.7;margin-top:10px;background:#221a0d;border:1px solid #4a3410;border-radius:8px;padding:12px}}
code{{background:#000;padding:1px 5px;border-radius:4px;color:#ffd54f}}
.pill{{display:inline-block;background:#1a212b;border:1px solid #2a3441;border-radius:20px;padding:6px 14px;margin:4px;font-size:13px}} .pill b{{color:#7fd}}
</style></head><body>
<h1>提前一天研判准确率（纠正口径）</h1>
<div class="sub">用 run_slot 当地时区落在目标日 T-1 的研判, 比对 T 日实测 · 生成 {datetime.date.today().isoformat()}</div>

<div class="grid">
{card("提前一天 MAE(最晚)", f"{S_lat['mae']:.2f}°C", f"RMSE {S_lat['rmse']:.2f}°C · n={tot_lat}")}
{card("平均偏差", f"{S_lat['bias']:+.2f}°C", "正=偏热")}
{card("Polymarket胜率(同档)", f"{slat/tot_lat*100:.0f}%", f"±1档 {pl1at/tot_lat*100:.0f}% · ±2档 {p2lat/tot_lat*100:.0f}%")}
{card("提前一天 MAE(最早)", f"{S_ear['mae']:.2f}°C", "保守下界 · n={tot_ear}".replace("{tot_ear}",str(tot_ear)))}
{card("对照·当天 D0", f"{S0['mae']:.2f}°C", f"≤1°C {S0['h1']:.0f}%")}
</div>

<section><h2>① 正确口径 vs 错误口径</h2>{cmp}
<div class="note">{note}</div></section>

<section><h2>② 整数分档命中（Polymarket 胜率代理）</h2>
<div class="pill">同档(赢) <b>{slat/tot_lat*100:.1f}%</b></div>
<div class="pill">±1档 <b>{pl1at/tot_lat*100:.1f}%</b></div>
<div class="pill">±2档 <b>{p2lat/tot_lat*100:.1f}%</b></div>
<div class="note">美国(K,°F)：同档 {slat*len(us_lat)/tot_lat:.1f}% · ±1档 {stats(us_lat)['h1']:.1f}% · MAE {stats(us_lat)['mae']:.2f}°C（n={len(us_lat)}）<br>
其他(°C)：同档 {slat*len(ot_lat)/tot_lat:.1f}% · ±1档 {stats(ot_lat)['h1']:.1f}% · MAE {stats(ot_lat)['mae']:.2f}°C（n={len(ot_lat)}）</div></section>

<div class="note">说明: "提前一天·最晚发布"= 你在 T-1 当天能看到的最后一次更新研判(最可能下单价位); "最早发布"= T-1 第一次出研判(信息最少, 误差更大)。若你在 T-1 更早点下单, 真实误差更接近保守下界。之前报告的 0.65°C 因混入当天/提前两天的研判而被低估。</div>
</body></html>"""
open(OUT,"w",encoding="utf-8").write(doc)
print(f"\n纠正报告已生成: {OUT}")
