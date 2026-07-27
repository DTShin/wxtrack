#!/usr/bin/env python3
"""
通过 GitHub REST Contents API 推送 dashboard/ 到 GitHub Pages。

适用场景：当 git-over-HTTPS 的 CONNECT 隧道被代理/防火墙阻断（如本机沙箱代理对
github.com:443 返回 502），而 api.github.com 仍可访问时，用 API 提交文件绕过 git。

用法:
  python3 scripts/push_github_api.py
依赖:
  $ROOT/.secrets/github_token  (classic PAT, 需 repo 权限)
  $WX_DASHBOARD_REPO          (可选, 默认 DTShin/weather-dashboard)
  Python 包: requests
"""
import os
import sys
import base64
import json
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard")
TOKEN_FILE = os.path.join(ROOT, ".secrets", "github_token")
REPO = os.environ.get("WX_DASHBOARD_REPO", "DTShin/weather-dashboard")
BRANCH = "main"
API = "https://api.github.com"


def token():
    p = TOKEN_FILE
    if not os.path.exists(p):
        sys.exit(f"ERROR: 未找到令牌文件 {p}")
    return open(p).read().strip()


def walk_files():
    out = []
    for name in sorted(os.listdir(DASH)):
        full = os.path.join(DASH, name)
        if name == ".git":
            continue
        if os.path.isfile(full):
            out.append(name)
        elif os.path.isdir(full):
            # 递归保留子目录结构（Pages 支持子路径）
            for dp, _, fns in os.walk(full):
                if ".git" in dp.split(os.sep):
                    continue
                for fn in sorted(fns):
                    rel = os.path.relpath(os.path.join(dp, fn), DASH)
                    out.append(rel)
    return out


def main():
    tk = token()
    headers = {
        "Authorization": f"Bearer {tk}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "wxtrack-bot",
    }
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    files = walk_files()
    print(f"待推送文件 ({len(files)}): {files}")

    ok, fail = 0, 0
    for rel in files:
        local = os.path.join(DASH, rel)
        with open(local, "rb") as f:
            raw = f.read()
        b64 = base64.b64encode(raw).decode()
        url = f"{API}/repos/{REPO}/contents/{requests.utils.quote(rel)}"
        # 获取现有 sha（用于更新）
        r = requests.get(url, headers=headers, timeout=30)
        sha = r.json().get("sha") if r.status_code == 200 else None
        body = {
            "message": f"data: {ts}",
            "content": b64,
            "branch": BRANCH,
        }
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
    if ok:
        print(f"Pages URL: https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[1]}/")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
