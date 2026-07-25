#!/usr/bin/env python3
"""生成 Obsidian 知识库：从 SQLite 渲染 Markdown 文件（幂等覆盖写）
用法:
  python3 gen_vault.py [--date 2026-07-22]
"""
import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, load_cities, load_models, setup_log

log = setup_log("gen_vault")
ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"


def md_home(conn, today):
    """00_MOC/Home.md"""
    lines = [
        "# 🌍 气象预报追踪知识库",
        "",
        f"*最后更新: {today.isoformat()}*",
        "",
        "## 📌 快速导航",
        "",
        "- [[综合研判预报]] — 融合所有模型+偏差修正的最终温度预测（今日/明日/后日）",
        "- [[研判留存与验证]] — 每一次预报采集与研判结果的永久留存机制及准确性验证",
        "- [[模型榜]] — 各气象模型表现排行榜",
        "- [[修正建议]] — 基于偏差的预报修正建议",
        "- [[城市索引]] — 48 个监测城市",
        "",
        "## 📅 最近 7 天日报",
        "",
    ]
    for i in range(7):
        d = today - timedelta(days=i)
        lines.append(f"- [[{d.isoformat()}]]")
    lines += [
        "",
        "## 🔗 外部数据源",
        "",
        "- [Open-Meteo](https://open-meteo.com/) — 免费多模型预报 API",
        "- [Aviation Weather](https://aviationweather.gov/) — METAR 实测数据",
        "- [腾讯文档（在线）](https://docs.qq.com/) — 智能表格实时同步",
    ]
    return "\n".join(lines)


def md_retention(conn, today):
    """00_MOC/研判留存与验证.md — 描述每次预报采集与研判的永久留存机制"""
    # 统计各表规模
    fc_n = conn.execute("SELECT COUNT(*) FROM forecast_snapshot").fetchone()[0]
    jud_n = conn.execute("SELECT COUNT(*) FROM judgment_snapshot").fetchone()[0]
    slots = [r[0] for r in conn.execute(
        "SELECT DISTINCT run_slot FROM forecast_snapshot ORDER BY run_slot")]
    obs_n = conn.execute("SELECT COUNT(*) FROM obs_tmax WHERE tmax_final IS NOT NULL").fetchone()[0]
    pairs = conn.execute(
        "SELECT COUNT(*) FROM judgment_snapshot j JOIN obs_tmax o"
        " ON j.city=o.city AND j.target_date=o.local_date WHERE o.tmax_final IS NOT NULL"
    ).fetchone()[0]
    # 总体 MAE（若验证 JSON 已生成）
    import json as _json
    mae_overall = "—"
    vj = ROOT / "data" / "exports" / "judgment_verify.json"
    if vj.exists():
        try:
            mae_overall = _json.loads(vj.read_text(encoding="utf-8")).get("overall_mae", "—")
        except Exception:
            pass
    lines = [
        "# 研判留存与验证",
        "",
        f"*最后更新: {today.isoformat()}*",
        "",
        "> **设计目标**：保留每一次预报采集与研判结果，使任何历史预报事后都可被实测气温复核，",
        "> 支撑长期偏差修正，逐步提升各地最高气温预测准确度。",
        "",
        "## 留存机制",
        "",
        "- **`forecast_snapshot`**：每次采集（run_slot）对每个 (城市×模型×目标日×提前期) 的原始预报，"
        "使用 `INSERT OR REPLACE` + `UNIQUE(city,model,target_date,run_slot)`，**永久保留，无删除逻辑**。",
        "- **`judgment_snapshot`**：每次采集的综合研判（中位数+偏差修正+IQR 置信度），"
        "按 `UNIQUE(city,target_date,lead_days,run_slot)` 永久留存，可供逐次复核。",
        "- **`obs_tmax`**：实测最高温（METAR / Weather Underground），按 `UNIQUE(city,local_date)` 幂等更新。",
        "",
        "## 当前规模",
        "",
        f"- 采集槽（run_slot）数量: **{len(slots)}**",
        f"- 原始预报快照: **{fc_n}** 行",
        f"- 研判快照: **{jud_n}** 行",
        f"- 实测记录（含最高温）: **{obs_n}** 条",
        f"- 可配对核验（研判 vs 实测）: **{pairs}** 条",
        f"- 总体 MAE（修正后研判 vs 实测）: **{mae_overall}°C**",
        "",
        "## 采集槽列表",
        "",
        "| 采集槽 |",
        "|---|",
    ]
    for s in slots:
        lines.append(f"| {s} |")
    lines += [
        "",
        "## 关联",
        "",
        "- [[综合研判预报]] — 最新综合研判",
        "- [[修正建议]] — 偏差修正公式",
        "- 外部验证报告: `docs/研判准确性验证_*.md`",
        "",
    ]
    return "\n".join(lines)


def md_cities_index(conn, today):
    """00_MOC/城市索引.md"""
    cities = load_cities()
    lines = ["# 城市索引", "", f"共 {len(cities)} 个监测城市，按时区排序", ""]
    lines.append("| # | ICAO | 城市 | 时区 | 最近最高温 |")
    lines.append("|---|---|---|---|---|")
    for c in cities:
        obs = conn.execute(
            "SELECT local_date,tmax_final FROM obs_tmax WHERE city=? ORDER BY local_date DESC LIMIT 1",
            (c["icao"],)
        ).fetchone()
        tmax = f"{obs['tmax_final']:.0f}°C ({obs['local_date']})" if obs else "—"
        lines.append(f"| {c['order']} | [[{c['icao']}-{c['name']}\\|{c['icao']}]] | {c['name']} | {c['tz']} | {tmax} |")
    return "\n".join(lines)


def md_city(conn, city, today):
    """10_Cities/<ICAO>-<城市名>.md"""
    icao = city["icao"]
    name = city["name"]
    models_cfg = load_models()
    lines = [
        "---",
        f"icao: {icao}",
        f"name: {name}",
        f"tz: {city['tz']}",
        f"lat: {city['lat']}",
        f"lon: {city['lon']}",
        f"elev: {city.get('elev')}",
        f"order: {city['order']}",
        f"tags: [city, {icao}]",
        "---",
        "",
        f"# {name} ({icao})",
        "",
        f"**时区**: {city['tz']}  |  **坐标**: {city['lat']}, {city['lon']}  |  **海拔**: {city.get('elev', '—')} m",
        "",
        "## 近 14 天实测与 D0 误差",
        "",
    ]
    # 表头
    models = [m for m in models_cfg["models"]]
    header = "| 日期 | 实测 | " + " | ".join(m["label"][:8] for m in models) + " |"
    sep = "|---|---|" + "|".join("---" for _ in models) + "|"
    lines.append(header)
    lines.append(sep)
    for i in range(14):
        d = (today - timedelta(days=i)).isoformat()
        obs = conn.execute(
            "SELECT tmax_final,src_final FROM obs_tmax WHERE city=? AND local_date=?",
            (icao, d)
        ).fetchone()
        obs_str = f"{obs['tmax_final']:.0f}°C" if obs and obs["tmax_final"] else "—"
        cols = [d, obs_str]
        for m in models:
            fc = conn.execute(
                "SELECT tmax FROM forecast_snapshot"
                " WHERE city=? AND model=? AND target_date=? AND lead_days=0 AND tmax IS NOT NULL"
                " ORDER BY collected_at DESC LIMIT 1",
                (icao, m["id"], d)
            ).fetchone()
            if fc and obs and obs["tmax_final"]:
                err = fc[0] - obs["tmax_final"]
                cols.append(f"{fc[0]:.0f}({err:+.0f})")
            elif fc:
                cols.append(f"{fc[0]:.0f}")
            else:
                cols.append("—")
        lines.append("| " + " | ".join(cols) + " |")
    lines += [
        "",
        "## 关联模型",
        "",
    ]
    for m in models:
        lines.append(f"- [[{m['label']}]]")
    lines += [
        "",
        "## 研判演进（按采集槽，目标=今日 D0）",
        "",
        "> 每次预报采集（run_slot）都会生成一条研判并永久留存于 `judgment_snapshot`，",
        "> 下方展示同一目标日在不同采集槽下的修正后预测如何随时间演进，便于与实测比对。",
        "",
        "| 采集槽 | 提前期 | 修正后 °C | 原始中位 | IQR | 置信度 | 模型数 |",
        "|---|---|---|---|---|---|---|",
    ]
    target = today.isoformat()
    judgs = conn.execute(
        "SELECT run_slot,lead_days,corrected_median,raw_median,iqr,confidence,n_models"
        " FROM judgment_snapshot WHERE city=? AND target_date=?"
        " ORDER BY run_slot",
        (icao, target)
    ).fetchall()
    if judgs:
        for j in judgs:
            cm = f"{j['corrected_median']:.1f}" if j['corrected_median'] is not None else "—"
            rm = f"{j['raw_median']:.1f}" if j['raw_median'] is not None else "—"
            iqr = f"{j['iqr']:.2f}" if j['iqr'] is not None else "—"
            lines.append(
                f"| {j['run_slot']} | D+{j['lead_days']} | {cm} | {rm} | {iqr} | {j['confidence']} | {j['n_models']} |"
            )
    else:
        lines.append("| — | — | — | — | — | — | — |")
    lines += [
        "",
        "> 关联: [[研判留存与验证]] — 查看全量研判 vs 实测的可追溯性验证报告",
        "",
    ]
    return "\n".join(lines)


def md_model(conn, model_cfg, today):
    """20_Models/<label>.md"""
    m = model_cfg
    lines = [
        "---",
        f"id: {m['id']}",
        f"scope: {m['scope']}",
        f"source: {m['source']}",
        f"tags: [model, {m['id']}]",
        "---",
        "",
        f"# {m['label']} ({m['id']})",
        "",
        f"**范围**: {m['scope']}  |  **来源**: {m['source']}",
        "",
        "## 30 天偏差指标",
        "",
        "| 城市 | L | n | Bias | MAE | RMSE |",
        "|---|---|---|---|---|---|",
    ]
    stats = conn.execute(
        "SELECT city,lead_days,n,bias,mae,rmse FROM bias_stat"
        " WHERE model=? AND window_days=30"
        " ORDER BY mae ASC",
        (m["id"],)
    ).fetchall()
    for s in stats[:20]:
        lines.append(f"| [[{s['city']}]] | {s['lead_days']} | {s['n']} | {s['bias']:+.1f} | {s['mae']:.1f} | {s['rmse']:.1f} |")
    if not stats:
        lines.append("| — | — | — | — | — | — |")
    return "\n".join(lines)


def md_daily(conn, today):
    """30_Daily/YYYY-MM-DD.md"""
    d = today
    lines = [
        f"# {d.isoformat()} 日报",
        "",
        f"采集时间: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## 实测汇总",
        "",
        "| 城市 | 实测 | 来源 | 冲突 | 各模型D0误差 |",
        "|---|---|---|---|---|",
    ]
    cities = load_cities()
    models_cfg = load_models()
    models = [m for m in models_cfg["models"]]
    for c in cities:
        obs = conn.execute(
            "SELECT tmax_final,src_final,conflict FROM obs_tmax WHERE city=? AND local_date=?",
            (c["icao"], d.isoformat())
        ).fetchone()
        if not obs or obs["tmax_final"] is None:
            continue
        obs_str = f"{obs['tmax_final']:.0f}°C"
        src_str = obs["src_final"]
        cf_str = "⚠️" if obs["conflict"] else ""
        errs = []
        for m in models:
            fc = conn.execute(
                "SELECT tmax FROM forecast_snapshot"
                " WHERE city=? AND model=? AND target_date=? AND lead_days=0 AND tmax IS NOT NULL"
                " ORDER BY collected_at DESC LIMIT 1",
                (c["icao"], m["id"], d.isoformat())
            ).fetchone()
            if fc:
                errs.append(f"{m['label'][:6]}={fc[0]-obs['tmax_final']:+.0f}")
        lines.append(f"| [[{c['icao']}-{c['name']}\\|{c['icao']}]] | {obs_str} | {src_str} | {cf_str} | {', '.join(errs[:5])} |")
    lines += [
        "",
        "## 模型表现概览",
        "",
    ]
    return "\n".join(lines)


def md_stats(conn, today):
    """40_Stats/模型榜.md 和 修正建议.md"""
    # 模型榜
    lines = ["# 模型表现排行榜", "", f"*统计日期: {today.isoformat()}*", "",
             "## 全球模型 30 天 MAE 排行", "",
             "| 排名 | 模型 | L | 城市 | n | Bias | MAE | RMSE |",
             "|---|---|---|---|---|---|---|---|"]
    stats = conn.execute(
        "SELECT model,lead_days,city,n,bias,mae,rmse FROM bias_stat"
        " WHERE window_days=30"
        " ORDER BY mae ASC LIMIT 20"
    ).fetchall()
    for i, s in enumerate(stats, 1):
        lines.append(f"| {i} | {s['model']} | {s['lead_days']} | {s['city']} | {s['n']} | {s['bias']:+.1f} | {s['mae']:.1f} | {s['rmse']:.1f} |")
    model_rank = "\n".join(lines)

    # 修正建议
    lines2 = ["# 预报修正建议", "", f"*基于 30 天滚动偏差，fc_corrected = fc − bias*", "",
              "> ⚠️ 数据积累初期（n 较小），bias 将随样本增多趋于稳定可信。", "",
              "| 城市 | 模型 | L | n | Bias | 修正公式 |",
              "|---|---|---|---|---|---|"]
    corrections = conn.execute(
        "SELECT city,model,lead_days,n,bias FROM bias_stat"
        " WHERE window_days=30 AND n>=1"
        " ORDER BY ABS(bias) DESC LIMIT 20"
    ).fetchall()
    for c in corrections:
        sign = "-" if c["bias"] > 0 else "+"
        lines2.append(f"| [[{c['city']}]] | {c['model']} | {c['lead_days']} | {c['n']} | {c['bias']:+.1f} | fc {sign}{abs(c['bias']):.1f}°C |")
    corrections_md = "\n".join(lines2)

    return model_rank, corrections_md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()

    conn = db()

    VAULT.mkdir(exist_ok=True)
    for d in ["00_MOC", "10_Cities", "20_Models", "30_Daily", "40_Stats"]:
        (VAULT / d).mkdir(exist_ok=True)

    # MOC
    (VAULT / "00_MOC" / "Home.md").write_text(md_home(conn, today), encoding="utf-8")
    (VAULT / "00_MOC" / "研判留存与验证.md").write_text(md_retention(conn, today), encoding="utf-8")
    (VAULT / "00_MOC" / "城市索引.md").write_text(md_cities_index(conn, today), encoding="utf-8")

    # 城市页
    for c in load_cities():
        (VAULT / "10_Cities" / f"{c['icao']}-{c['name']}.md").write_text(md_city(conn, c, today), encoding="utf-8")

    # 模型页
    for m in load_models()["models"]:
        safe_name = m["label"].replace("/", "_").replace(" ", "_")
        (VAULT / "20_Models" / f"{safe_name}.md").write_text(md_model(conn, m, today), encoding="utf-8")

    # 日报
    (VAULT / "30_Daily" / f"{today.isoformat()}.md").write_text(md_daily(conn, today), encoding="utf-8")

    # 统计
    rank, corrections = md_stats(conn, today)
    (VAULT / "40_Stats" / "模型榜.md").write_text(rank, encoding="utf-8")
    (VAULT / "40_Stats" / "修正建议.md").write_text(corrections, encoding="utf-8")

    log.info(f"Vault 已生成: {VAULT}（日期: {today.isoformat()}）")
    conn.close()


if __name__ == "__main__":
    main()
