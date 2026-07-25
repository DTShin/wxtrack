# wxtrack — 全球 48 城多模型最高气温追踪系统

一套自动采集 13+ 气象模型预报、与实测气温比对、做偏差修正并生成综合研判的工作流。
数据全部保留在本地 SQLite（每个采集时刻独立快照），可长期积累以持续提升预测准确度。

> 本文件夹是自包含的**部署包**，可直接放到 VPS 的任意路径（推荐 `/workspace` 或 `/opt/wxtrack`）运行。
> 不依赖任何沙箱环境，也不使用网盘作为交接代理——本地项目目录即资产库。

## 一分钟上手

```bash
bash setup.sh --systemd        # 装依赖 + 配 token + 初始化 + 启用常驻服务
# 或仅本地验证：
bash setup.sh --no-venv
python3 scripts/run_pipeline.sh forecast
```

## 目录结构

```
wxtrack/
├── README.md                  本文件
├── 交接文档_HANDOFF.md         给接管 agent 的完整交接文档（必读）
├── requirements.txt           Python 依赖
├── setup.sh                   一键部署脚本
├── systemd/wxtrack.service    systemd 常驻服务模板
├── config/                    cities.yaml / models.yaml / smartsheet.yaml ...
├── scripts/                   全部 Python + Shell 脚本
├── dashboard/                 GitHub Pages 前端（index.html + data.json）
├── docs/                      分析/状态/运行手册等文档
├── vault/                     Obsidian 知识库（由 gen_vault.py 生成）
├── data/wxtrack.db            SQLite 数据库（自动建表）
├── logs/                      运行日志
└── .secrets/                  密钥（github_token），由 setup.sh 写入
```

## 核心能力

- **多模型采集**：Open-Meteo 12 模型 + meteoblue 抓取，覆盖 48 城
- **逐时刻留存**：每次采集按"小时槽"生成独立永久快照（`forecast_snapshot` + `judgment_snapshot`）
- **偏差修正**：`fc_corrected = fc − bias30`，综合研判 = 修正后中位数（IQR 过滤）
- **实测比对**：aviationweather.gov METAR 每日最高温
- **可追溯验证**：`verify_judgment.py` 把每一次历史研判与实测逐条比对
- **多端输出**：GitHub Pages 看板 / Obsidian 知识库 / （可选）腾讯文档智能表

## 详细文档

- **`交接文档_HANDOFF.md`** —— 架构、数据流、部署、运维、排错，给接管 agent 的完整指南
- `docs/运行手册.md` —— 各脚本用法
- `docs/项目状态与同步记录_20260724.md` —— 设计说明与当前状态
