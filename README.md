# 国家水质自动综合监管平台 - 数据抓取项目

通过模拟点击抓取 [国家水质自动综合监管平台](https://szzdjc.cnemc.cn:8070/GJZ/Business/Publish/Main.html) 的页面数据，并保存为 Excel 表格。

## 功能

- **页面分析**：`analyze_page.py` 打开目标页面，枚举可点击元素与表格，结果保存到 `output/page_structure.json`，便于制定/调整抓取计划。
- **模拟点击抓取**：`crawler.py` 使用 Playwright 打开主页面，优先点击「实时数据」「发布说明」等入口，再遍历同源链接，提取所有表格与文本。
- **Excel 导出**：`export_excel.py` 将抓取结果按页面/表格分 Sheet 写入 `output/water_info_data.xlsx`。
- **一键运行**：`main.py` 执行抓取并导出 Excel。

## 环境要求

- Python 3.8+
- Chromium（由 `playwright install chromium` 安装）
- **推荐**：使用项目配套的 conda 环境 `water_crawler`，已包含 Playwright 所需库（atk、at-spi2-atk、xorg-libxdamage 等），激活时会自动设置 `LD_LIBRARY_PATH`，无需再装系统依赖。
- 若不用 conda、且 Chromium 报错缺少 `libatk-1.0` 等，可安装系统依赖后重试，例如：
  - Ubuntu/Debian: `sudo apt-get install -y libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2`
  - 或在本地/有完整图形库的环境中运行

## 安装

### 方式一：使用 conda 环境（推荐，已解决 libatk/libXdamage 等依赖）

```bash
conda create -n water_crawler python=3.11 -y
conda activate water_crawler
conda install -y -c conda-forge atk at-spi2-atk at-spi2-core xorg-libxdamage xorg-libxfixes xorg-libxrandr xorg-libxcomposite xorg-libxext libxcb
pip install -r requirements.txt
playwright install chromium
```

环境内已配置激活脚本，激活 `water_crawler` 后会自动设置 `LD_LIBRARY_PATH`，无需再手动设置。

### 方式二：仅 pip

```bash
cd water_info_crawler
pip install -r requirements.txt
playwright install chromium
```

## 使用

### 1. 仅分析页面结构（可选）

```bash
python analyze_page.py
```

查看 `output/page_structure.json` 中的链接、按钮、表格、iframe，必要时修改 `config.py` 中的选择器或优先点击文本。

### 2. 执行抓取并导出 Excel

```bash
python main.py
```

结果文件：`output/water_info_data.xlsx`。

### 3. 运行模式（test / prod）

- **test（默认）**：仅抓取「全国」数据，写入单 sheet「水质实时数据」。
- **prod**：按省/市逐个选择再抓取，同一批数据写入同一 sheet，并增加「一级区域」「二级区域」两列；每个区域之间间隔 `PROD_INTERVAL_MS`（默认 5 秒）防反爬。可通过 `PROD_TOP_N` 只抓前 N 个一级地域以缩短调试时间。

通过环境变量切换：`WATER_CRAWLER_MODE=prod python main.py`。或在 `config.py` 中设置 `RUN_MODE = "prod"`。

### 4. 调试时显示浏览器窗口

在 `config.py` 中设置 `HEADLESS = False`，再运行 `python main.py`。

### 5. 按地名查最近记录（geo_search.py）

在已抓取 `output/water_info_*.xlsx` 的前提下，可将数据中的位置（省份、断面名称等）转成地理坐标，并支持按地名查物理距离最近的记录。

- **地理编码方案**：`amap`（高德，需环境变量 `AMAP_KEY`）、`offline`（本地 CSV：`output/geo_cache.csv`，列 `address,lat,lon`）。
- **首次或数据更新后**：`python geo_search.py --build-cache [--scheme amap|offline]` 构建坐标缓存。
- **按地名查最近 N 条**：`python geo_search.py "郑州市" --top 10 [--scheme amap|offline]`。
- **启动 HTTP 接口**：`python geo_search.py --serve --port 5000`，访问 `http://127.0.0.1:5000/nearest?place=郑州&top=5`。

依赖：环境变量 `AMAP_KEY`（高德 Web 服务 key）；可选 `flask`（`--serve`）。

### 6. 水质数据 SQLite + 最近邻（water_db.py）

在抓取得到 `output/water_info_*.xlsx` 后，可将「省份 + 断面名称」地理编码并写入 SQLite（`output/water_data.db`），再用 KD-tree 做最近邻查询。

| 命令 | 说明 |
|------|------|
| `python water_db.py init [--db output/water_data.db]` | 仅建表，不抓取、不导入数据。 |
| `python water_db.py update [--scheme amap\|offline]` | 全量更新：执行 prod 抓取 → 地理编码 → 写库并原子切换。 |
| `python water_db.py update --skip-crawl [--scheme amap\|offline]` | 跳过抓取，仅从已有 `output/water_info_*.xlsx` 做地理编码与入库（数据已抓完时使用）。 |
| `python water_db.py query --place "地名" [--top 10] [--scheme amap\|offline]` | 命令行最近邻查询，返回距离该地名最近的水质断面。 |
| `python water_db.py serve [--port 5001]` | 启动 HTTP 接口，GET `/nearest?place=地名&top=5&scheme=amap` 查询最近邻。 |

**示例**：数据已存在于 `output/` 时只做入库：

```bash
python water_db.py update --skip-crawl --scheme amap
```

查询距离「南京南京工业大学」最近的断面（命令行或 HTTP）：

```bash
python water_db.py query --place "南京南京工业大学" --top 10 --scheme amap
# 或先启动服务：python water_db.py serve
# 再访问：http://127.0.0.1:5001/nearest?place=南京南京工业大学&top=10&scheme=amap
```

**无缝切换**：更新时先写 `water_data_new`，再在单事务内执行 `DROP water_data; ALTER TABLE water_data_new RENAME TO water_data`，查询始终读 `water_data`，无中间空表或半新半旧。

**依赖**：`scipy`（KD-tree）、环境变量 `AMAP_KEY`（高德地理编码，`scheme=amap` 时需要）。

## 配置说明

`config.py` 中可调整：

- `RUN_MODE`：`"test"` 仅抓全国，`"prod"` 按省/市逐个抓（可由环境变量 `WATER_CRAWLER_MODE` 覆盖）。
- `PROD_INTERVAL_MS`：prod 模式下每个区域之间的间隔（毫秒）。
- `PROD_TOP_N`：prod 下只抓前 N 个一级地域（如 3 表示只抓前 3 个省/市）；`None` 或 0 表示不限制。
- `SINGLE_SHEET_NAME`：汇总表 sheet 名；`REGION_COLUMN_1` / `REGION_COLUMN_2`：区域列名。

- `BASE_URL`：目标主页面地址
- `HEADLESS`：是否无头模式
- `TIMEOUT_MS` / `CLICK_WAIT_MS`：超时与点击后等待时间
- `IGNORE_HTTPS_ERRORS`：是否忽略 HTTPS 证书错误（内网或自签名证书时建议 True）
- `PRIORITY_LINK_TEXTS`：优先点击的链接文本（如「实时数据」「发布说明」）
- `OUTPUT_EXCEL` / `OUTPUT_DIR`：输出文件名与目录

## 抓取计划

详见 [CRAWL_PLAN.md](CRAWL_PLAN.md)。

## 注意事项

- 目标站点若需登录，请在 `config.py` 中配置 Cookie 或账号，并在爬虫中增加登录步骤。
- 请合理控制访问频率，避免对目标服务器造成压力。
