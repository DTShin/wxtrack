#!/usr/bin/env python3
"""研判准确性验证：把 judgment_snapshot 中每一次研判结果与实测气温逐条比对。

目的（Phase B 核心诉求）：永久保留的每一次研判都能量化其准确度，
用于长期偏差修正与模型可信度评估。

输出:
  - docs/研判准确性验证_<date>.md   人工可读报告
  - data/exports/judgment_verify.json  供腾讯文档同步的结构化数据
"""
import argparse
import json
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, load_cities, setup_log, utcnow, iso

log = setup_log("verify_judgment")
ROOT = Path(__file__).resolve().parent.parent
OUT_MD = ROOT / "docs" / f"研判准确性验证_{date.today().isoformat()}.md"
OUT_JSON = ROOT / "data" / "exports" / "judgment_verify.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    today = date.fromisoformat(args.date) if args.date else date.today()

    conn = db()
    cities = {c["icao"]: c for c in load_cities()}

    # 取有实测的 (city, target_date)
    obs = conn.execute(
        "SELECT city, local_date, tmax_final, src_final FROM obs_tmax"
        " WHERE tmax_final IS NOT NULL"
    ).fetchall()

    pair_rows = []  # 每条研判 vs 实测
    for o in obs:
        city, local_date, obs_v, src = o["city"], o["local_date"], o["tmax_final"], o["src_final"]
        judgs = conn.execute(
            "SELECT * FROM judgment_snapshot WHERE city=? AND target_date=?",
            (city, local_date),
        ).fetchall()
        for j in judgs:
            cm = j["corrected_median"]
            if cm is None:
                continue
            err = round(cm - obs_v, 2)
            pair_rows.append({
                "city": city,
                "name": cities.get(city, {}).get("name", city),
                "target_date": local_date,
                "lead_days": j["lead_days"],
                "run_slot": j["run_slot"],
                "corrected_median": cm,
                "raw_median": j["raw_median"],
                "obs": obs_v,
                "src": src,
                "error": err,
                "abs_error": abs(err),
                "confidence": j["confidence"],
                "n_models": j["n_models"],
            })

    if not pair_rows:
        log.warning("无可用配对（研判 vs 实测），跳过报告生成")
        print("无可用配对数据")
        return

    # 聚合：按 run_slot
    def mae(subset):
        aes = [r["abs_error"] for r in subset]
        return round(statistics.mean(aes), 3) if aes else None

    by_slot = {}
    for r in pair_rows:
        by_slot.setdefault(r["run_slot"], []).append(r)
    slot_stats = [(s, len(v), mae(v)) for s, v in sorted(by_slot.items())]

    by_lead = {}
    for r in pair_rows:
        by_lead.setdefault(r["lead_days"], []).append(r)
    lead_stats = [(l, len(v), mae(v)) for l, v in sorted(by_lead.items())]

    overall_mae = mae(pair_rows)
    # 置信度分层
    by_conf = {}
    for r in pair_rows:
        by_conf.setdefault(r["confidence"], []).append(r)
    conf_stats = [(c, len(v), mae(v)) for c, v in sorted(by_conf.items())]

    log.info(f"配对 {len(pair_rows)} 条 | 总体 MAE={overall_mae}°C")

    # ---- 写 Markdown ----
    lines = [
        f"# 🔬 研判准确性验证报告",
        "",
        f"*生成时间: {iso(utcnow())}  |  配对样本: {len(pair_rows)} 条研判 vs 实测*",
        "",
        "> 本报告将 `judgment_snapshot` 中**每一次**研判结果（按采集槽永久保留）与"
        "> `obs_tmax` 实测最高温逐条比对，量化各次预报的误差，支撑长期偏差修正。",
        "",
        f"## 总体指标",
        "",
        f"- **配对样本数**: {len(pair_rows)} 条",
        f"- **总体 MAE（修正后研判 vs 实测）**: **{overall_mae}°C**",
        f"- **覆盖实测日期**: {min(r['target_date'] for r in pair_rows)} ~ {max(r['target_date'] for r in pair_rows)}",
        "",
        f"## 按采集槽（run_slot）统计 MAE",
        "",
        "| 采集槽 | 研判条数 | MAE (°C) |",
        "|---|---|---|",
    ]
    for s, n, m in slot_stats:
        lines.append(f"| {s} | {n} | {m} |")
    lines += [
        "",
        f"## 按提前期（lead_days）统计 MAE",
        "",
        "| 提前期 | 研判条数 | MAE (°C) |",
        "|---|---|---|",
    ]
    for l, n, m in lead_stats:
        label = {0: "D0 当天", 1: "D+1", 2: "D+2", 3: "D+3"}.get(l, f"D+{l}")
        lines.append(f"| {label} | {n} | {m} |")
    lines += [
        "",
        f"## 按置信度分层 MAE",
        "",
        "| 置信度 | 研判条数 | MAE (°C) |",
        "|---|---|---|",
    ]
    for c, n, m in conf_stats:
        lines.append(f"| {c} | {n} | {m} |")
    lines += [
        "",
        f"## 逐条明细（前 40 条，按误差绝对值降序）",
        "",
        "| 城市 | 日期 | 提前期 | 采集槽 | 修正后 °C | 实测 °C | 误差 | 置信度 | 模型数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(pair_rows, key=lambda x: -x["abs_error"])[:40]:
        label = {0: "D0", 1: "D+1", 2: "D+2", 3: "D+3"}.get(r["lead_days"], f"D+{r['lead_days']}")
        lines.append(
            f"| {r['name']}({r['city']}) | {r['target_date']} | {label} | {r['run_slot']} | "
            f"{r['corrected_median']} | {r['obs']} | {r['error']:+} | {r['confidence']} | {r['n_models']} |"
        )
    lines += [
        "",
        "---",
        "*Powered by wxtrack — 研判可追溯性验证*",
    ]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"验证报告已写: {OUT_MD}")

    # ---- 写 JSON（腾讯文档同步用）---
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_at": iso(utcnow()),
        "overall_mae": overall_mae,
        "n_pairs": len(pair_rows),
        "by_slot": [{"run_slot": s, "n": n, "mae": m} for s, n, m in slot_stats],
        "by_lead": [{"lead_days": l, "n": n, "mae": m} for l, n, m in lead_stats],
        "by_confidence": [{"confidence": c, "n": n, "mae": m} for c, n, m in conf_stats],
        "pairs": pair_rows,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info(f"验证 JSON 已写: {OUT_JSON}")

    print(f"\n验证完成: {len(pair_rows)} 条配对 | 总体 MAE={overall_mae}°C")
    print(f"按槽 MAE: " + "  ".join(f"{s.split('T')[1]}={m}" for s, n, m in slot_stats))
    print(f"按提前期 MAE: " + "  ".join(f"D+{l}={m}" for l, n, m in lead_stats))

    conn.close()


if __name__ == "__main__":
    main()
