#!/usr/bin/env python3
"""wxtrack 小时级采集守护进程（常驻前台脚本，可后台运行）。

不依赖任何外部定时任务系统，直接在本环境循环：
  - 每小时唤醒一次，运行 forecast 管线（采集预报 + 研判 + 看板推送）
  - 每 3 轮补采一次实测（obs，幂等、对 aviationweather 较温和）
  - 每轮运行 verify（研判 vs 实测 准确度验证，纯本地 DB，轻量）
  - 每 6 轮运行一次 stats（重算偏差 + 研判回填 + vault + 看板）

特性：
  - 时间戳日志 -> logs/loop_collector.log
  - 单实例保护（PID 文件）
  - 捕获异常自愈（单轮失败不影响下一轮）
  - SIGINT/SIGTERM 优雅停止（写入停止标记）
用法:
  python3 scripts/loop_collector.py            # 前台（Ctrl-C 停止）
  python3 scripts/loop_collector.py --once    # 只跑一轮（测试用）
  python3 scripts/loop_collector.py --interval 3600
"""
import argparse
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "loop_collector.log"
PID = ROOT / "data" / "loop_collector.pid"

stop = False


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def handle_stop(signum, frame):
    global stop
    stop = True
    log(f"收到信号 {signum}，将在本轮结束后停止")


def run_pipeline(mode):
    log(f">>> 启动管线: {mode}")
    try:
        r = subprocess.run(
            ["bash", "scripts/run_pipeline.sh", mode],
            cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
        )
        tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
        log(f"<<< 完成 {mode} (rc={r.returncode})\n" + "\n".join("    " + t for t in tail))
    except subprocess.TimeoutExpired:
        log(f"!!! 管线 {mode} 超时(>1800s)，跳过本轮")
    except Exception as e:
        log(f"!!! 管线 {mode} 异常: {e}")


def main():
    global stop
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=3600, help="唤醒间隔(秒)，默认 3600")
    ap.add_argument("--once", action="store_true", help="只跑一轮即退出（测试）")
    args = ap.parse_args()

    # 单实例保护
    if PID.exists():
        old = PID.read_text(encoding="utf-8").strip()
        log(f"发现已有 PID 文件: {old}（若确认无进程运行请删除 data/loop_collector.pid 后重试）")
        # 不强制退出，仅提示；真实防重入由调用方保证
    PID.write_text(str(__import__("os").getpid()), encoding="utf-8")

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    if args.once:
        log("单次模式：运行 forecast + obs + verify")
        run_pipeline("forecast")
        run_pipeline("obs")
        run_pipeline("verify")
        PID.unlink(missing_ok=True)
        return

    log(f"loop_collector 启动 | 间隔={args.interval}s | PID={__import__('os').getpid()}")
    cycle = 0
    try:
        while not stop:
            cycle += 1
            log(f"===== 第 {cycle} 轮唤醒 =====")
            run_pipeline("forecast")
            if cycle % 3 == 0:
                run_pipeline("obs")
            run_pipeline("verify")
            if cycle % 6 == 0:
                run_pipeline("stats")
            if stop:
                break
            log(f"休眠 {args.interval}s 至下一轮…")
            for _ in range(args.interval):
                if stop:
                    break
                time.sleep(1)
    finally:
        PID.unlink(missing_ok=True)
        log("loop_collector 已停止")


if __name__ == "__main__":
    main()
