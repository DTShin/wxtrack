#!/usr/bin/env python3
"""通用 GitHub 文件推送：将给定相对路径的文件 PUT 到仓库（绕过 git，走 Contents API）。

用法:
  python3 scripts/push_files.py <rel-path-1> [rel-path-2 ...]
依赖: requests（wxtrack venv 已装）
"""
import os
import sys
import base64
import json
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("WX_DASHBOARD_REPO", "DTShin/weather-dashboard")
BRANCH = "main"
API = "https://api.github.com"


def token():
    p = os.path.join(ROOT, ".secrets", "github_token")
    if not os.path.exists(p):
        sys.exit(f"ERROR: 未找到令牌文件 {p}")
    return open(p).read().strip()


def main():
    tk = token()
    headers = {"Authorization": f"Bearer {tk}", "Accept": "application/vnd.github+json",
               "User-Agent": "wxtrack-bot"}
    ok, fail = 0, 0
    for rel in sys.argv[1:]:
        local = os.path.join(ROOT, rel)
        if not os.path.exists(local):
            print(f"  SKIP {rel} (本地不存在)")
            continue
        with open(local, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode()
        url = f"{API}/repos/{REPO}/contents/{requests.utils.quote(rel)}"
        r = requests.get(url, headers=headers, timeout=30)
        sha = r.json().get("sha") if r.status_code == 200 else None
        body = {"message": f"deploy: {rel}", "content": b64, "branch": BRANCH}
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=headers, json=body, timeout=60)
        if r.status_code in (200, 201):
            print(f"  OK   {rel}  ({len(raw)} bytes, {'update' if sha else 'create'})")
            ok += 1
        else:
            print(f"  FAIL {rel}  HTTP {r.status_code}: {r.text[:200]}")
            fail += 1
    print(f"\n完成: {ok} 成功, {fail} 失败")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
