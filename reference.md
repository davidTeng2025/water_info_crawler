# Water Info Crawler - 参考

本仓库采用 **Agent Skill 结构**：根目录为技能说明（SKILL.md、reference.md），可执行代码在 `scripts/`，输出在 `output/`。

## 目录结构

```
water_info_crawler/
├── SKILL.md              # 本技能说明（Agent 必读）
├── reference.md           # 本参考
├── README.md              # 安装与使用说明
├── CRAWL_PLAN.md          # 抓取计划
├── requirements.txt
├── scripts/               # 可执行脚本（均在项目根目录下执行）
│   ├── config.py          # 配置（OUTPUT_DIR 指向 项目根/output）
│   ├── analyze_page.py    # 页面结构分析
│   ├── main.py            # 抓取入口
│   ├── crawler.py         # 抓取逻辑
│   ├── export_excel.py    # Excel 导出
│   ├── water_db.py        # SQLite 建表/更新/查询/serve
│   ├── geo_search.py      # 地理编码与按地名查最近（可独立于 DB）
│   └── geo.py             # 地理工具
└── output/                # 输出目录（Excel、DB、缓存等）
```

## 入口脚本

- **scripts/analyze_page.py**：分析目标页面 DOM/iframe，输出 `output/page_structure.json`
- **scripts/main.py**：执行抓取并导出 Excel（依赖 crawler、export_excel、config）
- **scripts/water_db.py**：建表、全量更新（抓取/跳过抓取）、最近邻查询、HTTP serve
- **scripts/geo_search.py**：独立地理编码与按地名查最近（可配合缓存，不依赖 DB）

执行方式：在项目根目录运行 `python scripts/<脚本名> [参数]`。

## 配置

- **scripts/config.py**：`RUN_MODE`、`BASE_URL`、超时、选择器、prod 间隔等；`OUTPUT_DIR` 已设为项目根目录下的 `output`。
- 环境变量：`WATER_CRAWLER_MODE`（test/prod）、`AMAP_KEY`（高德）、可选 `PLAYWRIGHT_BROWSERS_PATH`、`WATER_CRAWLER_CHROMIUM_PATH`。

## 数据流

1. 抓取 → `output/water_info_*.xlsx`
2. 地理编码（省份+断面名称）→ 可选 `output/geo_cache.json`
3. 入库 → `output/water_data.db`（表 `water_data`），更新时先写 `water_data_new` 再原子切换
4. 查询：地名 → 高德/离线坐标 → KD-tree 最近邻 → 返回完整记录 + `_distance_km`

详细说明见 [README.md](README.md)。
