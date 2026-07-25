#!/usr/bin/env python3
"""腾讯文档智能表格同步：通过 tdocs-app MCP 工具同步 3 张表。
注意：此脚本需要 MCP 工具上下文，由 Agent 在会话中通过 MCP 调用完成。
本脚本负责准备数据（输出 JSON 中间文件），Agent 负责执行 MCP 操作。
用法（由 Agent 调用，需先建表后落盘 smartsheet.yaml）:
  1. 数据准备: python3 sync_smartsheet.py --prepare --date 2026-07-22
  2. Agent 读取中间 JSON 文件，执行 MCP smartsheet.add_records / update_records
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from common import db, load_cities, load_models, setup_log, utcnow

log = setup_log("sync_smartsheet")
TMP = __file__.rsplit("/", 1)[0] + "/../data/exports"


def prepare_t1(conn, target_date):
    """T1 今日预报快览: 每城一行，各模型×lead D0 预报"""
    cities = load_cities()
    models_cfg = load_models()
    models = [m for m in models_cfg["models"]]
    rows = []
    for city in cities:
        row = {"city": city["icao"], "name": city["name"], "date": target_date}
        for m in models:
            fc = conn.execute(
                "SELECT tmax FROM forecast_snapshot"
                " WHERE city=? AND model=? AND target_date=? AND lead_days=0 AND tmax IS NOT NULL"
                " ORDER BY collected_at DESC LIMIT 1",
                (city["icao"], m["id"], target_date)
            ).fetchone()
            row[f"{m['label']}_D0"] = round(fc[0], 1) if fc else None
        rows.append(row)
    return rows


def prepare_t2(conn, target_date):
    """T2 实测与 D0 误差: 每城一行，obs + 各模型 D0 误差"""
    cities = load_cities()
    models_cfg = load_models()
    models = [m for m in models_cfg["models"]]
    rows = []
    for city in cities:
        obs = conn.execute(
            "SELECT tmax_final,src_final,conflict FROM obs_tmax"
            " WHERE city=? AND local_date=?",
            (city["icao"], target_date)
        ).fetchone()
        if not obs or obs["tmax_final"] is None:
            continue
        row = {"city": city["icao"], "name": city["name"], "date": target_date,
               "obs_final": round(obs["tmax_final"], 1), "src": obs["src_final"],
               "conflict": obs["conflict"]}
        for m in models:
            fc = conn.execute(
                "SELECT tmax FROM forecast_snapshot"
                " WHERE city=? AND model=? AND target_date=? AND lead_days=0 AND tmax IS NOT NULL"
                " ORDER BY collected_at DESC LIMIT 1",
                (city["icao"], m["id"], target_date)
            ).fetchone()
            if fc:
                row[f"{m['label']}_err"] = round(fc[0] - obs["tmax_final"], 1)
            else:
                row[f"{m['label']}_err"] = None
        rows.append(row)
    return rows


def prepare_t3(conn):
    """T3 模型表现榜: model×lead 的 30 天指标"""
    rows = conn.execute(
        "SELECT city,model,lead_days,n,bias,mae,rmse FROM bias_stat"
        " WHERE window_days=30"
        " ORDER BY mae ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def prepare_long(conn, target_date):
    """长表(城市×模型)行：匹配腾讯在线表字段(模型用ID)，并合并实测与偏差。
    在线表字段: 城市ICAO/城市名称/预报日期/模型/预报最高温(°C)/实测最高温(°C)/偏差(°C)/提前天数/采集批次
    """
    cities = load_cities()
    models_cfg = load_models()
    models = [m for m in models_cfg["models"]]
    batch = datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
    rows = []
    for city in cities:
        obs = conn.execute(
            "SELECT tmax_final FROM obs_tmax WHERE city=? AND local_date=?",
            (city["icao"], target_date)).fetchone()
        obs_v = round(obs[0], 1) if obs and obs[0] is not None else None
        for m in models:
            fc = conn.execute(
                "SELECT tmax FROM forecast_snapshot"
                " WHERE city=? AND model=? AND target_date=? AND lead_days=0 AND tmax IS NOT NULL"
                " ORDER BY collected_at DESC LIMIT 1",
                (city["icao"], m["id"], target_date)).fetchone()
            fc_v = round(fc[0], 1) if fc else None
            bias = round(fc_v - obs_v, 1) if (fc_v is not None and obs_v is not None) else None
            rows.append({
                "城市ICAO": city["icao"], "城市名称": city["name"], "预报日期": target_date,
                "模型": m["id"], "预报最高温(°C)": fc_v, "实测最高温(°C)": obs_v,
                "偏差(°C)": bias, "提前天数": 0, "采集批次": batch,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--date", default="")
    ap.add_argument("--table", default="T1,T2,T3")
    args = ap.parse_args()

    target_date = args.date or datetime.now().date().isoformat()
    conn = db()

    TMP_dir = __file__.rsplit("/", 1)[0] + "/../data/exports"
    from pathlib import Path
    Path(TMP_dir).mkdir(parents=True, exist_ok=True)

    tables = [t.strip() for t in args.table.split(",")]
    for t in tables:
        if t == "T1":
            data = prepare_t1(conn, target_date)
        elif t == "T2":
            data = prepare_t2(conn, target_date)
        elif t == "T3":
            data = prepare_t3(conn)
        elif t == "LONG":
            data = prepare_long(conn, target_date)
        else:
            continue
        out = f"{TMP_dir}/smartsheet_{t}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(f"{t}: {len(data)} 行 → {out}")

    conn.close()


if __name__ == "__main__":
    main()
