# meteoblue 网页抓取技术文档

> 版本: v1.0 | 更新: 2026-07-23 | 脚本: `scripts/scrape_meteoblue.py`

---

## 一、背景与设计原则

meteoblue（瑞士 meteoblue AG）是一家商业气象服务商，**不提供免费 API**。但其网站上的周预报页面是公开可访问的，因此采用 **网页抓取（Web Scraping）** 方式获取每日最高温预报。

### 设计原则

- **Best-Effort（尽力而为）**：任何环节失败只记日志，不中断主流程
- **温和限速**：城市间 sleep 1.0s，搜索 API 间 sleep 0.5s，避免触发反爬
- **缓存优先**：搜索 API 的结果（城市 slug）持久化到 YAML 缓存，避免重复查询
- **幂等写入**：同一 `(city, model, target_date, run_slot)` 不重复记录

---

## 二、技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                    meteoblue 抓取流程                         │
│                                                             │
│  ① 搜索 API                      ② 周预报页                   │
│  ┌──────────────────┐           ┌──────────────────────┐    │
│  │ /en/server/      │           │ /en/weather/week/    │    │
│  │ search/query3    │  ──────►  │ {slug}               │    │
│  │ ?query=ZBAA      │  slug     │                      │    │
│  │ &itemsPerPage=8  │           │ <div class="tab"     │    │
│  │ &lang=en         │           │   id="day1">         │    │
│  │ &iso=none        │           │   <time class="date" │    │
│  └──────────────────┘           │     datetime="...">   │    │
│                                 │   <div class="        │    │
│                                 │     tab-temp-max">    │    │
│                                 │     28 °C             │    │
│                                 └──────────────────────┘    │
│                                                             │
│  ③ 缓存层（YAML）              ④ 写入 SQLite                 │
│  ┌──────────────────┐           ┌──────────────────────┐    │
│  │ meteoblue_urls   │           │ forecast_snapshot    │    │
│  │ .yaml            │           │ model='meteoblue'    │    │
│  │ ZBAA: beijing... │           │ source='meteoblue'   │    │
│  │ ZSPD: shanghai.. │           │                      │    │
│  └──────────────────┘           └──────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、两步抓取详解

### 3.1 第一步：搜索 API — 获取城市页面 slug

#### 接口

```
GET https://www.meteoblue.com/en/server/search/query3
```

#### 请求参数

| 参数 | 值 | 说明 |
|------|---|------|
| `query` | `ZBAA` 或 `Beijing airport` | 搜索关键词 |
| `itemsPerPage` | `8` | 每页结果数 |
| `lang` | `en` | 语言（英语） |
| `iso` | `none` | 不限制国家 |

#### 请求头

```
User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0
Accept-Language: en-US,en;q=0.9
```

#### 响应示例

```json
{
  "results": [
    {
      "name": "Beijing Capital International Airport",
      "country": "China",
      "lat": "40.0801",
      "lon": "116.585",
      "url": "beijing-capital-international-airport_china_6301354",
      "type": "airport"
    }
  ]
}
```

#### 匹配策略

采用 **两级搜索 + 坐标匹配** 策略：

```
策略 A: query = ICAO 代码（如 "ZBAA"）
策略 B: query = 城市名 + "airport"（如 "Beijing airport"）
         ↓
从搜索结果中选坐标最接近者（曼哈顿距离 < 0.5°）
         ↓
       成功 → 返回 url（即 slug）
       失败 → 返回 None（记 N/A）
```

坐标匹配精度 `d = |lat_result - lat_city| + |lon_result - lon_city|`，阈值 0.5° 约 55km，足以区分同一城市的不同机场。

#### 搜索查询策略

对每个城市依次尝试两个查询：

```python
queries = [
    f"{city['icao']}",                          # "ZBAA"
    f"{city.get('site') or city['name']} airport"  # "Beijing airport"
]
```

第一个用 ICAO 精确匹配（成功率最高），第二个用城市名兜底。

### 3.2 第二步：周预报页 — 解析每日最高温

#### 页面 URL

```
https://www.meteoblue.com/en/weather/week/{slug}
```

例如北京：`https://www.meteoblue.com/en/weather/week/beijing-capital-international-airport_china_6301354`

#### 目标 HTML 结构

meteoblue 的周预报页使用 **标签页（Tab）** 布局，每天一个 `<div class="tab" id="dayN">`：

```html
<div class="tab" id="day1">
  <time class="date" datetime="2026-07-23">Thu 23.07.</time>
  <div class="tab-temp-max">28 °C</div>
  <div class="tab-temp-min">22 °C</div>
  ...
</div>
<div class="tab" id="day2">
  <time class="date" datetime="2026-07-24">Fri 24.07.</time>
  <div class="tab-temp-max">30 °C</div>
  <div class="tab-temp-min">23 °C</div>
  ...
</div>
<!-- ...共 14 天（day1 ~ day14）... -->
```

#### 解析逻辑

```python
def parse_week(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for tab in soup.select("div.tab[id^='day']"):
        # ① 提取日期
        time_el = tab.select_one("time.date")
        dstr = time_el.get("datetime", "").strip()  # "2026-07-23"
        
        # ② 提取最高温
        tmax_el = tab.select_one(".tab-temp-max")
        text = tmax_el.get_text(" ", strip=True)    # "28 °C"
        text = text.replace("−", "-")               # 统一负号（Unicode→ASCII）
        m = re.search(r"(-?\d+(?:\.\d+)?)", text)   # 正则提取数字
        
        if dstr and m:
            out[dstr] = float(m.group(1))           # {"2026-07-23": 28.0}
    return out
```

#### CSS 选择器清单

| 选择器 | 目标元素 | 示例值 |
|--------|---------|--------|
| `div.tab[id^='day']` | 每天预报的容器 | 14 个 tab |
| `time.date` | 日期元素（含 datetime 属性） | `datetime="2026-07-23"` |
| `.tab-temp-max` | 最高温文本 | `"28 °C"` |
| `.tab-temp-min` | 最低温文本（当前未使用） | `"22 °C"` |

#### 数据范围

- meteoblue 周预报页展示 **14 天**（day1 ~ day14）
- 本系统只取 **D0 ~ D+3**（今天到 3 天后），共 4 天
- `lead_days` 根据城市本地时区计算

---

## 四、缓存机制

### 缓存文件

```
/workspace/config/meteoblue_urls.yaml
```

### 缓存结构

```yaml
ZBAA: beijing-capital-international-airport_china_6301354
ZSPD: shanghai-pudong-international-airport_china_6301386
RJTT: tokyo-international-airport_japan_6300412
...
```

### 缓存策略

```
第一次采集某城市:
  ① 查缓存 → 未命中
  ② 调用搜索 API → 成功
  ③ 写入缓存（YAML 持久化）
  ④ 抓取周预报页

后续采集同一城市:
  ① 查缓存 → 命中
  ② 跳过搜索 API，直接用缓存 slug 抓取
```

### 缓存优势

- **减少 API 调用**：搜索 API 对每个城市只调用一次
- **加速采集**：跳过搜索步骤，每城节省约 1 秒
- **容错**：即使搜索 API 暂时故障，已有缓存的城市仍可正常抓取

---

## 五、数据写入

### 写入目标

SQLite 表 `forecast_snapshot`，与其他模型（Open-Meteo）统一存储：

```sql
INSERT OR REPLACE INTO forecast_snapshot
  (city, model, target_date, lead_days, tmax, run_slot, collected_at, source)
VALUES
  ('ZBAA', 'meteoblue', '2026-07-23', 0, 28.0, '2026-07-23T08:30Z', '2026-07-23T08:30:15Z', 'meteoblue'),
  ('ZBAA', 'meteoblue', '2026-07-24', 1, 30.0, '2026-07-23T08:30Z', '2026-07-23T08:30:15Z', 'meteoblue'),
  ...
```

### 唯一约束

```
UNIQUE(city, model, target_date, run_slot)
```

同一 `run_slot` 内不会重复写入同一城市同一目标日的 meteoblue 数据。

---

## 六、错误处理与容错

### 错误分级

| 级别 | 场景 | 处理方式 |
|------|------|---------|
| **搜索失败** | 搜索 API HTTP 非 200 / 无结果 / 坐标不匹配 | `log.warning` + 跳过该城（记 N/A） |
| **页面请求失败** | 周预报页 HTTP 非 200 / 超时 / 网络错误 | `log.warning` + 跳过该城 |
| **解析失败** | 页面结构改版 / CSS 选择器无匹配 / 正则无结果 | `log.warning` + 跳过该城 |
| **部分城市失败** | 某个城市 slug 缺失或抓取失败 | 继续处理下一个城市，不影响其他城市 |

### 关键设计

```python
# meteoblue 失败不影响主流程
if not args.skip_meteoblue:
    try:
        import scrape_meteoblue
        n = scrape_meteoblue.run(conn, cities, slot, now_iso)
    except Exception as e:
        log.error(f"meteoblue 采集失败（不影响主流程）: {e}")
```

### 无数据城市的 N/A 标记

对于搜索失败的城市（无 slug），**不写入 forecast_snapshot**（因为 meteoblue 行数在其他 Open-Meteo 模型采集时已经自动生成 N/A 记录）。

---

## 七、限速策略

| 操作 | 间隔 | 说明 |
|------|------|------|
| 搜索 API 调用 | 0.5s | 每个搜索查询之间 |
| 周预报页抓取 | 1.0s | 每个城市之间 |
| 重试退避 | 指数退避 | 由 `common.http_get()` 统一处理（未对 meteoblue 单独重试） |

---

## 八、当前覆盖状态

| 指标 | 数值 |
|------|------|
| 总城市数 | 48 |
| slug 缓存命中 | 42 城（87.5%） |
| 成功抓取 | 42 城 |
| 预报快照 | 336 行 |
| 最新数据 | 2026-07-23T11:45:15Z |

### 缺失城市（6 个，无 slug）

| ICAO | 城市 | 可能原因 |
|------|------|---------|
| FACT | 开普敦 | 南非站点命名不一致 |
| KAUS | 奥斯汀 | 美国中小机场覆盖弱 |
| KHOU | 休斯顿 | 可能仅有 Hobby 而非 IAH |
| KORD | 芝加哥 | 可能与 Midway 混淆 |
| VILK | 勒克瑙 | 印度非一线城市 |
| ZUCK | 重庆 | 中文名匹配困难 |

---

## 九、与其他模型的对比

| 维度 | meteoblue（网页抓取） | 其他 12 个模型（Open-Meteo API） |
|------|----------------------|--------------------------------|
| 获取方式 | 网页抓取（BeautifulSoup） | REST API（JSON） |
| 可靠性 | 依赖页面结构稳定性 | API 契约稳定 |
| 速度 | 慢（1s/城 × 42 城 ≈ 42s） | 快（批量请求，<10s） |
| 覆盖 | 42/48 城 | 48/48 城（全球模型） |
| 维护成本 | 高（页面改版需更新选择器） | 低（API 向后兼容） |
| 数据丰富度 | 仅最高温/最低温 | 支持数百个气象变量 |
| 失败影响 | 不影响主流程 | — |

---

## 十、维护指南

### 当 meteoblue 页面改版时

1. **症状**：`parse_week()` 返回空字典，日志显示 "解析为空（页面结构可能改版）"
2. **诊断**：手动访问周预报页，检查 HTML 结构
   ```bash
   curl -H "User-Agent: ..." "https://www.meteoblue.com/en/weather/week/beijing-capital-international-airport_china_6301354" | grep -o 'tab-temp-max[^<]*'
   ```
3. **修复**：更新 `parse_week()` 中的 CSS 选择器

### 当搜索 API 改版时

1. **症状**：`resolve_slug()` 全部返回 None，日志显示 "未找到 meteoblue 页面"
2. **诊断**：手动测试搜索 API
   ```bash
   curl "https://www.meteoblue.com/en/server/search/query3?query=ZBAA&itemsPerPage=8&lang=en&iso=none"
   ```
3. **修复**：更新 `resolve_slug()` 中的响应解析逻辑

### 补充缺失城市

1. 手动在 meteoblue.com 搜索对应城市
2. 确认 URL 中的 slug（如 `beijing-capital-international-airport_china_6301354`）
3. 添加到 `config/meteoblue_urls.yaml`：
   ```yaml
   ZUCK: chongqing-jiangbei-international-airport_china_XXXXXXX
   ```

---

## 十一、在管线中的位置

```
run_pipeline.sh forecast
  │
  ├── python3 collect_forecast.py          ← 12 个 Open-Meteo 模型
  │     │
  │     └── (最后自动调用)
  │           import scrape_meteoblue
  │           scrape_meteoblue.run(...)     ← meteoblue（第 13 个模型）
  │
  ├── python3 predict.py                   ← 综合研判（含 meteoblue 数据）
  ├── python3 gen_dashboard.py             ← Dashboard 数据
  └── bash push_dashboard.sh               ← GitHub Pages 推送
```

---

## 十二、代码清单

| 文件 | 用途 |
|------|------|
| `scripts/scrape_meteoblue.py` | 主抓取脚本（搜索+解析+写入） |
| `config/meteoblue_urls.yaml` | 城市 slug 缓存（42 条） |
| `scripts/collect_forecast.py` | 调用方（在 Open-Meteo 采集后自动触发） |

---

*文档维护: wxtrack 项目组 | 基于 scrape_meteoblue.py v1.0*
