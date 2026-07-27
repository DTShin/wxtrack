#!/usr/bin/env python3
"""整数分档准确率分析（对齐 Polymarket 整数档）。

Polymarket 天气市场按整数温度分档（美国城市用 °F、其余用 °C）。
本脚本把每次研判(corrected_median)与实测(tmax_final)都四舍五入到
各自分档单位的整数，再计算「同档命中 / ±1 档 / ±2 档」命中率，
以及整数档 MAE。连续 MAE 仅作对照保留。
"""
import json
import sqlite3
import statistics
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "wxtrack.db"
OUT_JSON = ROOT / "data" / "exports" / "judgment_verify_integer.json"
OUT_HTML = ROOT / "docs" / "研判准确率报告_整数分档.html"

# 美国城市（ICAO K 前缀）走 °F 分档，其余走 °C
US_SET = {
    "KMIA", "KLGA", "KATL", "KORD", "KDAL", "KHOU",
    "KAUS", "KBKF", "KLAX", "KSFO", "KSEA",
}

F = 9 / 5  # °C -> °F 系数


def to_bin(v_c, is_us):
    """把 °C 值转成分档单位的整数档。"""
    x = v_c * F + 32 if is_us else v_c
    return round(x)


def analyze():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    obs = con.execute(
        "SELECT city, local_date, tmax_final FROM obs_tmax WHERE tmax_final IS NOT NULL"
    ).fetchall()
    pairs = []
    for o in obs:
        city, ld, ov = o["city"], o["local_date"], o["tmax_final"]
        is_us = city in US_SET
        judgs = con.execute(
            "SELECT * FROM judgment_snapshot WHERE city=? AND target_date=?",
            (city, ld),
        ).fetchall()
        for j in judgs:
            cm = j["corrected_median"]
            if cm is None:
                continue
            fb = to_bin(cm, is_us)
            ob = to_bin(ov, is_us)
            bin_err = fb - ob
            pairs.append({
                "city": city, "is_us": is_us, "target_date": ld,
                "lead_days": j["lead_days"], "confidence": j["confidence"],
                "corrected_median": cm, "obs": ov,
                "f_bin": fb, "o_bin": ob, "bin_err": bin_err,
                "abs_bin_err": abs(bin_err),
                "cont_err": round(cm - ov, 2),
                "abs_cont_err": abs(cm - ov),
            })
    con.close()
    return pairs


def pct(n, d):
    return round(100 * n / d, 1) if d else 0.0


def summarize(subset):
    n = len(subset)
    if n == 0:
        return {"n": 0}
    exact = sum(1 for r in subset if r["bin_err"] == 0)
    w1 = sum(1 for r in subset if r["abs_bin_err"] <= 1)
    w2 = sum(1 for r in subset if r["abs_bin_err"] <= 2)
    # 整数档 MAE（按各自单位）；°C 等价：美国档误差 /1.8
    mae_native = round(statistics.mean(r["abs_bin_err"] for r in subset), 3)
    mae_c = round(statistics.mean(
        (r["abs_bin_err"] / F if r["is_us"] else r["abs_bin_err"]) for r in subset), 3)
    cont_mae = round(statistics.mean(r["abs_cont_err"] for r in subset), 3)
    return {
        "n": n, "exact": pct(exact, n), "w1": pct(w1, n), "w2": pct(w2, n),
        "mae_native": mae_native, "mae_c": mae_c, "cont_mae": cont_mae,
    }


def main():
    pairs = analyze()
    overall = summarize(pairs)
    us = summarize([r for r in pairs if r["is_us"]])
    non_us = summarize([r for r in pairs if not r["is_us"]])

    by_lead = {}
    for r in pairs:
        by_lead.setdefault(r["lead_days"], []).append(r)
    lead_stats = {l: summarize(v) for l, v in sorted(by_lead.items())}

    by_conf = {}
    for r in pairs:
        by_conf.setdefault(r["confidence"], []).append(r)
    conf_stats = {c: summarize(v) for c, v in sorted(by_conf.items())}

    # 误差分布
    dist = {}
    for r in pairs:
        k = r["bin_err"]
        dist[k] = dist.get(k, 0) + 1
    dist_sorted = {k: dist[k] for k in sorted(dist)}

    # 城市排名（按同档命中率，至少 10 条）
    by_city = {}
    for r in pairs:
        by_city.setdefault(r["city"], []).append(r)
    city_rank = []
    for c, v in by_city.items():
        if len(v) >= 10:
            s = summarize(v)
            city_rank.append((c, s["n"], s["exact"], s["w1"], s["mae_c"]))
    city_rank.sort(key=lambda x: -x[2])
    best = city_rank[:8]
    worst = city_rank[-8:][::-1]

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall, "us": us, "non_us": non_us,
        "by_lead": lead_stats, "by_conf": conf_stats,
        "dist": dist_sorted, "best": best, "worst": worst,
        "n": len(pairs),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    build_html(result)
    print(f"配对 {len(pairs)} 条 | 同档命中 {overall['exact']}% | ±1档 {overall['w1']}% | "
          f"整数档MAE(°C等价) {overall['mae_c']} | 连续MAE {overall['cont_mae']}°C")
    print(f"  美国({us['n']}条): 同档 {us['exact']}% ±1 {us['w1']}% | "
          f"其他({non_us['n']}条): 同档 {non_us['exact']}% ±1 {non_us['w1']}%")


def build_html(r):
    o = r["overall"]; us = r["us"]; nu = r["non_us"]
    lead = r["by_lead"]; conf = r["by_conf"]; dist = r["dist"]
    # 误差分布 SVG
    maxc = max(dist.values()) if dist else 1
    bars = []
    for k in sorted(dist):
        h = dist[k] / maxc * 120
        col = "#2f9e44" if k == 0 else ("#f59f00" if abs(k) == 1 else "#e03131")
        bars.append((k, dist[k], h, col))
    svg_w = len(bars) * 38 + 40
    rects = ""
    for i, (k, c, h, col) in enumerate(bars):
        x = 30 + i * 38
        y = 150 - h
        rects += (f'<rect x="{x}" y="{y:.0f}" width="26" height="{h:.0f}" fill="{col}">'
                  f'<title>档差 {k:+d}: {c} 条</title></rect>')
        rects += f'<text x="{x+13}" y="145" font-size="10" text-anchor="middle" fill="#555">{k:+d}</text>'
        rects += f'<text x="{x+13}" y="{y-4:.0f}" font-size="9" text-anchor="middle" fill="#333">{c}</text>'
    dist_svg = (f'<svg viewBox="0 0 {svg_w} 170" width="100%" style="max-width:640px">'
                f'<line x1="30" y1="150" x2="{svg_w-10}" y2="150" stroke="#ccc"/>'
                f'{rects}'
                f'<text x="14" y="20" font-size="11" fill="#333">误差分布（研判档 − 实测档）</text></svg>')

    def card(label, val, sub):
        return (f'<div class="card"><div class="v">{val}</div>'
                f'<div class="l">{label}</div><div class="s">{sub}</div></div>')

    cards = (
        card("同档命中率", f"{o['exact']}%", f"研判整数档 == 实测整数档（{o['n']} 条）")
        + card("±1 档命中率", f"{o['w1']}%", "研判与实测整数档相差 ≤1")
        + card("±2 档命中率", f"{o['w2']}%", "研判与实测整数档相差 ≤2")
        + card("整数档 MAE", f"{o['mae_c']}°C", f"等价连续 MAE {o['cont_mae']}°C")
    )

    def table(rows, headers):
        th = "".join(f"<th>{h}</th>" for h in headers)
        body = ""
        for row in rows:
            body += "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"

    seg_rows = [
        ["美国城市（°F 分档）", us["n"], f"{us['exact']}%", f"{us['w1']}%", f"{us['w2']}%", f"{us['mae_native']}°F"],
        ["其他城市（°C 分档）", nu["n"], f"{nu['exact']}%", f"{nu['w1']}%", f"{nu['w2']}%", f"{nu['mae_c']}°C"],
    ]
    seg_tbl = table(seg_rows, ["分段", "条数", "同档", "±1档", "±2档", "整数档MAE"])

    lead_rows = []
    for l in sorted(lead):
        s = lead[l]
        lab = {0: "D0 当天", 1: "D+1", 2: "D+2", 3: "D+3"}.get(l, f"D+{l}")
        lead_rows.append([lab, s["n"], f"{s['exact']}%", f"{s['w1']}%", f"{s['w2']}%"])
    lead_tbl = table(lead_rows, ["提前期", "条数", "同档命中", "±1档", "±2档"])

    conf_rows = []
    for c in sorted(conf):
        s = conf[c]
        conf_rows.append([c, s["n"], f"{s['exact']}%", f"{s['w1']}%", f"{s['w2']}%"])
    conf_tbl = table(conf_rows, ["置信度", "条数", "同档命中", "±1档", "±2档"])

    best_rows = [[c, n, f"{e}%", f"{w}%", f"{m}°C"] for c, n, e, w, m in r["best"]]
    worst_rows = [[c, n, f"{e}%", f"{w}%", f"{m}°C"] for c, n, e, w, m in r["worst"]]

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>研判准确率 · 整数分档</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,'PingFang SC',sans-serif;margin:0;background:#f6f8fa;color:#222}}
.wrap{{max-width:900px;margin:0 auto;padding:28px 18px}}
h1{{font-size:22px;margin:0 0 4px}}
.meta{{color:#777;font-size:13px;margin-bottom:18px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:22px}}
.card{{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:16px;text-align:center}}
.card .v{{font-size:28px;font-weight:700;color:#1971c2}}
.card .l{{font-size:13px;margin-top:4px;font-weight:600}}
.card .s{{font-size:11px;color:#888;margin-top:4px}}
section{{background:#fff;border:1px solid #e6e8eb;border-radius:12px;padding:16px 18px;margin-bottom:16px}}
section h2{{font-size:16px;margin:0 0 12px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #eef0f2;padding:6px 8px;text-align:center}}
th{{background:#f1f3f5;font-weight:600}}
td:first-child,th:first-child{{text-align:left}}
.note{{font-size:12px;color:#777;line-height:1.6}}
.row2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:680px){{.row2{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<h1>🔬 研判准确率报告 · 整数分档对齐 Polymarket</h1>
<div class="meta">生成于 {r['generated_at'][:19].replace('T',' ')} UTC ｜ 配对样本 {o['n']} 条 ｜ 口径：研判与实测各按城市分档单位（美国°F / 其他°C）四舍五入取整后比较</div>
<div class="cards">{cards}</div>

<section><h2>分段对比（美国 °F 分档 vs 其他 °C 分档）</h2>{seg_tbl}
<div class="note">美国城市（K 前缀共 11 个）的 Polymarket 市场按 °F 整数分档，其余按 °C；整数档 MAE 按各自单位给出，整体以 °C 等价对照连续 MAE。</div></section>

<section><h2>误差分布（研判整数档 − 实测整数档）</h2>{dist_svg}
<div class="note">绿=同档(0)、橙=差1档、红=差≥2档。绝大多数研判落在同档或 ±1 档内。</div></section>

<div class="row2">
<section><h2>按提前期</h2>{lead_tbl}</section>
<section><h2>按置信度</h2>{conf_tbl}</section>
</div>

<div class="row2">
<section><h2>同档命中率最高城市</h2>{table(best_rows,["城市","条数","同档","±1档","MAE(°C)"])}</section>
<section><h2>同档命中率最低城市</h2>{table(worst_rows,["城市","条数","同档","±1档","MAE(°C)"])}</section>
</div>

<section><h2>口径说明</h2>
<div class="note">
• <b>整数分档</b>：Polymarket 天气市场按整数温度档（如「between 80-81°F」「98°F or higher」）。为对齐，本报告的研判准确率以整数档为基准：将每次研判的修正中位数与实测最高温，各自按城市分档单位四舍五入到最近整数档，再比较。<br>
• <b>同档命中</b>：研判整数档 == 实测整数档。<b>±1档</b>：两者相差 ≤1 个整数档。<br>
• <b>整数档 MAE</b>：平均整数档误差；整体以 °C 等价（美国 °F 档误差 ÷1.8）与连续 MAE 并列对照。<br>
• <b>连续 MAE</b>（对照）：沿用原口径（修正后研判 °C − 实测 °C 的绝对值均值），仅作参考。<br>
• 配对来源：<code>judgment_snapshot.corrected_median</code> × <code>obs_tmax.tmax_final</code>，同一实测日全部采集槽/提前期记录均计入。
</div></section>
</div></body></html>"""
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
