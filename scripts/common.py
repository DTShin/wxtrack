#!/usr/bin/env python3
"""公共工具：配置加载、SQLite 连接与建表、日志、HTTP 重试"""
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "wxtrack.db"
LOG_DIR = ROOT / "logs"
UA = {"User-Agent": "wxtrack-research/1.0 (weather forecast bias study)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_snapshot(
  id INTEGER PRIMARY KEY,
  city TEXT NOT NULL,
  model TEXT NOT NULL,
  target_date TEXT NOT NULL,
  lead_days INTEGER NOT NULL,
  tmax REAL,
  run_slot TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  source TEXT NOT NULL,
  UNIQUE(city, model, target_date, run_slot)
);
CREATE INDEX IF NOT EXISTS idx_fc ON forecast_snapshot(city, target_date, model);

CREATE TABLE IF NOT EXISTS obs_tmax(
  city TEXT NOT NULL,
  local_date TEXT NOT NULL,
  tmax_metar REAL,
  tmax_wu REAL,
  tmax_final REAL,
  src_final TEXT,
  conflict INTEGER DEFAULT 0,
  revised INTEGER DEFAULT 0,
  collected_at TEXT,
  UNIQUE(city, local_date)
);

CREATE TABLE IF NOT EXISTS bias_stat(
  stat_date TEXT, city TEXT, model TEXT, lead_days INTEGER,
  n INTEGER, bias REAL, mae REAL, rmse REAL, window_days INTEGER,
  UNIQUE(stat_date, city, model, lead_days, window_days)
);

CREATE TABLE IF NOT EXISTS judgment_snapshot(
  city TEXT NOT NULL,
  target_date TEXT NOT NULL,
  lead_days INTEGER NOT NULL,
  run_slot TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  raw_median REAL,
  corrected_median REAL,
  iqr REAL,
  confidence TEXT,
  n_models INTEGER,
  avg_bias REAL,
  UNIQUE(city, target_date, lead_days, run_slot)
);
CREATE INDEX IF NOT EXISTS idx_jud ON judgment_snapshot(city, target_date, lead_days);
"""


def setup_log(name):
    LOG_DIR.mkdir(exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)
        fh = logging.FileHandler(LOG_DIR / f"{name}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


def load_cities():
    with open(ROOT / "config" / "cities.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["cities"]


def load_models():
    with open(ROOT / "config" / "models.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url, params=None, retries=3, timeout=30, headers=None):
    """带重试的 GET，429/5xx 指数退避"""
    hdrs = dict(UA)
    if headers:
        hdrs.update(headers)
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(2 ** i * 2)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 ** i * 2)
    raise RuntimeError(f"GET {url} 失败: {last}")


def current_slot(dt=None):
    """把当前 UTC 时间归并到「按小时」采集槽，每小时一个独立永久版本。
    槽位 = 当前小时，格式 YYYY-MM-DDTHH:30Z（如 2026-07-24T12:30Z）。
    每个不同小时都会生成一条新的 run_slot，从而永久保留每次时间版本的预报/研判，
    满足「保留每个不同时间的更新版本」的要求。
    返回如 2026-07-24T12:30Z"""
    dt = dt or utcnow()
    h = dt.hour
    day = dt.date()
    return f"{day.isoformat()}T{h:02d}:30Z"
