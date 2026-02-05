---
name: water-info-crawler
description: Crawls the national water quality monitoring platform (CN), exports Excel, geocodes sites, and provides SQLite + nearest-neighbor query by place name. Use when working with water quality data, the water_info_crawler project, crawling szzdjc.cnemc.cn, or querying nearest monitoring sites by location.
---

# 水质数据抓取与地理查询 (Water Info Crawler)

本 Skill 指导 Agent 在本项目中执行：页面分析、抓取、Excel 导出、地理编码、SQLite 入库与按地名最近邻查询。**所有可执行脚本位于 `scripts/`，请在项目根目录执行命令。**

## 何时使用

- 用户需要抓取/更新国家水质自动综合监管平台数据
- 用户需要按地名查询最近的水质断面或启动查询接口
- 用户询问或修改本仓库（water_info_crawler）的用法、配置或排错

## 入口与命令

在**项目根目录**执行：

| 目标 | 命令 |
|-----|------|
| 分析页面结构 | `python scripts/analyze_page.py` → 结果在 `output/page_structure.json` |
| 抓取并导出 Excel | `python scripts/main.py`（test 仅全国；prod 需 `WATER_CRAWLER_MODE=prod` 或改 `config.RUN_MODE`） |
| 全量更新 DB（抓取+地理编码+入库） | `python scripts/water_db.py update [--scheme amap\|offline]` |
| 跳过抓取、仅从已有 Excel 入库 | `python scripts/water_db.py update --skip-crawl [--scheme amap]` |
| 按地名查最近 N 条（完整数据） | `python scripts/water_db.py query --place "地名" [--top 10] [--scheme amap]` |
| 启动最近邻 HTTP 接口 | `python scripts/water_db.py serve [--port 5001]` → `GET /nearest?place=地名&top=5` |
| 仅建表 | `python scripts/water_db.py init [--db output/water_data.db]` |

地理编码/缓存（独立于 DB）：`python scripts/geo_search.py`，见 [reference.md](reference.md)。

## 关键配置

- **scripts/config.py**：`RUN_MODE`（test/prod）、`BASE_URL`、超时与选择器；prod 全量抓取时需在调用前设 `config.RUN_MODE = "prod"`（如 water_db 的 full_update）。
- **地理编码**：`amap` 需环境变量 `AMAP_KEY`；`offline` 使用本地缓存。
- **输出**：固定为项目根目录下 `output/`（Excel、DB、page_structure.json 等）。

## 排错要点

- 若控制台显示 `RUN_MODE=test` 但期望 prod：在调用抓取前设置 `os.environ["WATER_CRAWLER_MODE"] = "prod"` 且 `config.RUN_MODE = "prod"`（因 config 在导入时已绑定）。
- Chromium 缺库（如 libatk）：使用 conda 环境或安装系统依赖（见 README）。
- 抓取结果省份/条数异常：检查区域下拉选择是否与表格一致（crawler 中按 DOM 索引选省、从表格列取区域标签）。

## 更多说明

- 完整安装、环境与配置见 [README.md](README.md)。
- 抓取计划与结构见 [CRAWL_PLAN.md](CRAWL_PLAN.md)。
- 入口与数据流详见 [reference.md](reference.md)。
