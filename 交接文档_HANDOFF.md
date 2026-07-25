# wxtrack 交接文档（给接管 Agent / Hermes）

> 适用场景：将整套"全球 48 城多模型最高气温追踪"工作流部署到 **VPS 本地环境**，
> 由接管 agent 在本机直接运行与维护。**不使用沙箱、不通过网盘做交接代理**，本地项目目录即资产库。

---

## 1. 目标与范围

- 每日自动采集 13+ 气象模型的**最高气温预报**（Open-Meteo 12 模型 + meteoblue 抓取）
- 与 **aviationweather.gov** 的 METAR 实测最高温比对
- 记录每个模型的**偏差（bias）与准确性（MAE/RMSE）**
- 用 30 天滚动偏差修正，生成**综合研判预报**（修正后中位数 + 置信度）
- 把每次采集、每次研判**永久留存**，可事后与实测逐条复核
- 输出到：GitHub Pages 看板、Obsidian 知识库、（可选）腾讯文档智能表

**项目目的**：通过长期记录 + 差值修正，持续提升各地最高气温预测准确度。

---

## 2. 环境要求

| 项 | 要求 |
|---|---|
| OS | Linux（Ubuntu 22.04+ 验证） |
| Python | ≥ 3.10，**推荐 3.11**（用到 `statistics.quantiles(method='inclusive')`） |
| 网络 | 出网访问 open-meteo.com / aviationweather.gov / github.com |
| 权限 | 部署常驻服务建议 root；GitHub Pages 推送需 `repo` 权限 token |
| 依赖 | 见 `requirements.txt`（requests, pyyaml, pandas, openpyxl, beautifulsoup4, lxml） |

---

## 3. 目录结构与文件职责

```
wxtrack/
├── 交接文档_HANDOFF.md        ← 你正在读的文件
├── README.md                  总览
├── requirements.txt           pip 依赖
├── setup.sh                   一键部署（依赖/密钥/初始化/systemd）
├── systemd/wxtrack.service    systemd 常驻服务模板（setup.sh 会渲染真实路径）
├── config/
│   ├── cities.yaml            48 城定义（ICAO/名称/时区/经纬度）
│   ├── models.yaml            13 个模型定义（id/label/scope/source）
│   ├── meteoblue_urls.yaml     meteoblue 页面 URL 映射
│   └── smartsheet.yaml         腾讯文档智能表字段映射（可选）
├── scripts/
│   ├── common.py              公共库：SQLite 建表/连接、配置加载、HTTP 重试、current_slot()
│   ├── collect_forecast.py    采集 13 模型预报 → forecast_snapshot
│   ├── collect_obs.py          采集 METAR 实测 → obs_tmax
│   ├── predict.py              综合研判 → judgment_snapshot（--backfill 回填历史）
│   ├── stats_bias.py           计算偏差/MAE/RMSE → bias_stat
│   ├── gen_vault.py            生成 Obsidian 知识库
│   ├── gen_dashboard.py        生成 dashboard/data.json（含 judgment_evolution）
│   ├── verify_judgment.py      每次研判 vs 实测 验证 → docs/研判准确性验证_*.md
│   ├── run_pipeline.sh         单命令管线入口（forecast/obs/stats/verify/health）
│   ├── push_dashboard.sh       推送 dashboard/ 到 GitHub Pages
│   ├── loop_collector.py       常驻守护：每小时唤醒一轮采集（含自信号处理）
│   ├── run_loop_forever.sh     无 systemd 时的崩溃自拉起兜底
│   ├── healthcheck.py          健康检查
│   ├── init_cities.py          初始化 cities 配置
│   ├── scrape_meteoblue.py     meteoblue 网页抓取
│   ├── scrape_wu.py            Weather Underground 抓取（备用）
│   └── sync_smartsheet.py      腾讯文档智能表同步（可选，需 MCP）
├── dashboard/                 GitHub Pages 前端（index.html + data.json + .nojekyll）
├── docs/                      分析/状态/运行手册
├── vault/                     Obsidian 知识库（生成产物）
├── data/wxtrack.db            SQLite 主库（首次运行自动建表）
├── logs/                      运行日志（loop_collector.log / loop_stdout.log / keeper.log）
└── .secrets/                  github_token（setup.sh 写入，勿入 git）
```

> 路径约定：所有脚本均以"脚本所在目录的上一级"为项目根（`ROOT`），**不写死绝对路径**，
> 因此本包可放在任意路径（如 `/workspace` 或 `/opt/wxtrack`）。

---

## 4. 数据模型（核心：永久留存）

数据库 `data/wxtrack.db`，4 张表，**均无删除/清理逻辑**：

| 表 | 唯一约束 | 写入方式 | 留存内容 |
|---|---|---|---|
| `forecast_snapshot` | `UNIQUE(city,model,target_date,run_slot)` | `INSERT OR REPLACE` | 每次采集的**原始多模型预报**（48城×13模型×多提前期） |
| `judgment_snapshot` | `UNIQUE(city,target_date,lead_days,run_slot)` | `INSERT OR REPLACE` | 每次采集的**综合研判**（原始中位/修正后中位/IQR/置信度/模型数/平均偏差） |
| `obs_tmax` | `UNIQUE(city,local_date)` | `INSERT OR REPLACE`（幂等） | 实测最高温（METAR） |
| `bias_stat` | `UNIQUE(stat_date,city,model,lead_days,window_days)` | `INSERT OR REPLACE` | 偏差统计（30天 + 全期双窗口） |

- **run_slot（采集槽）**：`common.current_slot()` 返回 `YYYY-MM-DDTHH:30Z`（**按小时一个独立槽**）。
  每小时唤醒都会生成新槽 → 每小时一份独立永久版本（满足"保留每个不同时间的更新版本"）。
- **lead_days（提前期）**：按时区敏感的本地日期差计算。同一采集槽对美洲城市可能产生 D+1 而非 D0（时区差异，属正常）。

---

## 5. 工作流（管线模式）

`bash scripts/run_pipeline.sh <mode>`：

| 模式 | 动作 | 说明 |
|---|---|---|
| `forecast` | collect_forecast → predict → gen_dashboard → push | 每小时主流程 |
| `obs` | collect_obs → verify | 补实测 + 验证 |
| `stats` | stats_bias → predict --backfill → gen_vault → gen_dashboard → push | 每日/周期：重算偏差并补齐所有历史研判 |
| `verify` | verify_judgment | 研判 vs 实测 准确性验证 |
| `health` | healthcheck | 连通性健康检查 |

各脚本也可单独运行（见 `docs/运行手册.md`）。

---

## 6. 部署步骤

### 方式 A：一键部署（推荐）

```bash
cd /path/to/wxtrack
WX_GITHUB_TOKEN=ghp_xxx  bash setup.sh --systemd
```

`setup.sh` 会：装依赖（venv）→ 写 `.secrets/github_token` → 初始化 `dashboard/` git 仓库 →
建库 → 冒烟测试 → 安装并启用 `wxtrack.service`。

### 方式 B：手动

```bash
cd /path/to/wxtrack
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p .secrets
printf '%s' "ghp_xxx" > .secrets/github_token && chmod 600 .secrets/github_token
( cd dashboard && git init -q && git remote add origin https://oauth2:$(cat ../.secrets/github_token)@github.com/DTShin/weather-dashboard.git )
python3 -c "import sys;sys.path.insert(0,'scripts');import common;common.db()"
```

### GitHub Pages 仓库

- 默认推送到 `DTShin/weather-dashboard`（可用环境变量 `WX_DASHBOARD_REPO=owner/repo` 覆盖）。
- `push_dashboard.sh` 读取 `.secrets/github_token`，把 `dashboard/` 推到该仓库的 `main` 分支，
  GitHub Pages 从该分支根目录服务 `index.html` + `data.json`。
- 看板地址：`https://<owner>.github.io/<repo>/`

---

## 7. 常驻运行（每小时采集）

**推荐 systemd**（VPS 持久、崩溃自动拉起）：

```bash
systemctl status wxtrack      # 查看
journalctl -u wxtrack -f      # 实时日志
systemctl restart wxtrack     # 重启
```

`wxtrack.service` 运行 `loop_collector.py --interval 3600`，行为：
- 每小时：forecast 管线（采集→研判→看板→推送）
- 每 3 轮：obs 管线（补实测，幂等，对 aviationweather 温和）
- 每轮：verify（研判 vs 实测，纯本地）
- 每 6 轮：stats 管线（重算偏差 + 研判回填 + vault + 看板）

**无 systemd 兜底**（`run_loop_forever.sh`，每 60s 检查并自拉起）：
```bash
nohup bash scripts/run_loop_forever.sh > logs/keeper.log 2>&1 &
```

> 注意：`loop_collector.py` 写 PID 文件 `data/loop_collector.pid`，并以 SIGTERM/SIGINT 优雅退出。
> 勿同时用 systemd 与 run_loop_forever 启动两份，避免重复采集。

---

## 8. 密钥与配置

- **`.secrets/github_token`**：一行纯 token（`repo` 权限）。被 `push_dashboard.sh` 使用。
- **腾讯文档（可选）**：由 `tdocs-app` MCP 连接器提供，需接管 agent 环境注入该 MCP。
  相关脚本 `sync_smartsheet.py` + `config/smartsheet.yaml`。无 MCP 时可跳过，不影响核心闭环。
- **模型扩展**：在 `config/models.yaml` 增删模型即可；`config/cities.yaml` 增删城市。

---

## 9. Obsidian 知识库

`gen_vault.py` 把数据库渲染为 Markdown 知识库到 `vault/`：
- `00_MOC/Home.md`、`综合研判预报.md`、`研判留存与验证.md`、`城市索引.md`
- `10_Cities/<ICAO>-<城市>.md`（含"研判演进（按采集槽）"区块）
- `20_Models/`、`30_Daily/`、`40_Stats/`

可用 Obsidian 直接打开 `vault/` 目录浏览与关联。每次 `stats` 模式会刷新。

---

## 10. 运维与监控

| 项 | 命令 |
|---|---|
| 健康检查 | `python3 scripts/run_pipeline.sh health` |
| 看板数据 | `python3 scripts/gen_dashboard.py`（输出 `dashboard/data.json`） |
| 验证报告 | `python3 scripts/verify_judgment.py` → `docs/研判准确性验证_*.md` |
| 日志 | `logs/loop_collector.log`（守护进程）、`logs/loop_stdout.log`（子进程输出） |
| DB 规模 | `sqlite3 data/wxtrack.db "SELECT COUNT(*) FROM forecast_snapshot"` |
| 手动单轮 | `python3 scripts/loop_collector.py --once` |

**数据增长预期**：每小时采集约 1900 行预报 + 192 行研判 → 约 4.5 万行/天。
SQLite 完全胜任（单文件，数月后数百 MB，可按需归档旧槽）。

---

## 11. 排错 FAQ

- **推送 GitHub Pages 失败**：检查 `.secrets/github_token` 是否有效、`WX_DASHBOARD_REPO` 是否可写；
  看 `push_dashboard.sh` 报错（常因 token 失效或分支保护）。
- **采集为空 / 超时**：Open-Meteo 限流时 `common.http_get` 自动退避重试；检查出网与 `logs/`。
- **meteoblue 部分城市 N/A**：约 6 城页面抓取偶尔失败（已知：重庆/勒克瑙/开普敦/芝加哥/休斯顿/奥斯汀），不影响其余 42 城。
- **systemd 服务起不来**：看 `journalctl -u wxtrack`；确认 `WorkingDirectory`、python 路径（venv）正确，`.secrets` 可读。
- **DB 锁**：SQLite 写并发低，单守护进程顺序写入不会锁；勿同时跑多份采集进程。
- **路径问题**：所有脚本已改为相对路径（基于脚本位置），**不要**把脚本移出 `scripts/` 或改 `ROOT` 推导逻辑。

---

## 12. 给接管 Agent 的部署 Checklist

- [ ] 把 `wxtrack/` 整个目录放到 VPS（如 `/workspace` 或 `/opt/wxtrack`）
- [ ] `bash setup.sh --systemd`（或手动步骤），提供 `WX_GITHUB_TOKEN`
- [ ] 确认 `systemctl status wxtrack` 为 active(running)
- [ ] 浏览器打开看板地址，确认 `index.html` 右上角显示"北京时间"更新时间
- [ ] 跑一次 `python3 scripts/run_pipeline.sh forecast` 验证全链路（采集→研判→看板→推送）
- [ ] 跑一次 `python3 scripts/run_pipeline.sh verify` 确认 `docs/研判准确性验证_*.md` 生成
- [ ] （可选）若环境有 `tdocs-app` MCP，运行 `sync_smartsheet.py` 同步腾讯文档
- [ ] 用 Obsidian 打开 `vault/` 确认知识库关联正常
- [ ] 设置外部监控（如看板 5 分钟自刷新 + 服务存活告警）按需

---

## 13. 重要设计说明（避免回归）

1. **不要给脚本写死绝对路径**——已统一用"脚本位置推导 ROOT"。改路径请用相对方式。
2. **不要加任何 DELETE/清理逻辑**到 `forecast_snapshot` / `judgment_snapshot`——永久留存是项目核心诉求。
3. **采集槽粒度为"每小时"**——若改回 4 小时槽会丢失中间版本；如需调整改 `common.current_slot()`。
4. **GitHub Pages 的真实服务目录是 `dashboard/` 子仓库**（不是根目录游离副本）。改 `index.html` 请改 `dashboard/index.html`，再 `push_dashboard.sh` 推送。
5. **偏差修正冷启动**：样本 < ~10 天时 bias 不稳（过拟合），MAE 可能虚低；属正常，随积累收敛。
