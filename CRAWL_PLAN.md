# 国家水质自动综合监管平台 - 抓取计划

## 目标站点
- **URL**: https://szzdjc.cnemc.cn:8070/GJZ/Business/Publish/Main.html
- **名称**: 国家水质自动综合监管平台（中国环境监测总站）

## 页面结构分析（基于 analyze_page 结果）

### 确认：实际数据在 iframe RealDatas.html
- 主页面 Main.html 为壳：仅有「实时数据」「发布说明」入口和少量 UI，body 含 `loading..`。
- **iframe id="MF" src="RealDatas.html"** 为数据容器，同源地址：`.../Publish/RealDatas.html`。
- 主页面上的表格多为空或弹窗，**真实水质数据在 iframe 内**，需进入该 frame 做解析与点击。

### 已知入口
1. **实时数据** - 水质自动监测的实时数据（主数据源，在 iframe 内展示）
2. **发布说明** - 发布说明文档（可能为 iframe 内 Tab 或独立页）

### 抓取策略（含 iframe 专项）
1. **阶段一：主页面 + 等待 iframe**
   - 打开 Main.html，等待 iframe#MF（RealDatas.html）加载完成。
   - 可选：在主页面点击「实时数据」「发布说明」以切换 iframe 内容或 Tab。

2. **阶段二：iframe 内解析与点击**
   - 定位数据 frame：`page.frames` 中 url 含 `RealDatas` 或 name/id 为 MF 的 frame。
   - 在 **frame 上下文**内：提取所有 `<table>` 为二维数据；枚举链接/按钮（Tab、分页等）。
   - 在 frame 内依次点击「发布说明」等 Tab/链接，每次点击后等待再提取表格，避免遗漏多 Tab 数据。

3. **阶段三：数据提取**
   - 主页面：识别 `<table>` 与列表，按行列/项提取。
   - **iframe**：在同一 frame 内执行 `frame.evaluate()` 提取 table/列表；若有分页则翻页后重复提取。

4. **阶段四：导出**
   - 按「主页 / iframe_RealDatas / Tab 名」分 Sheet，统一保存为 Excel。

4. **阶段四：导出**
   - 按「页面/模块」分 Sheet 或分文件
   - 统一保存为 Excel（.xlsx）

## 数据获取逻辑说明（当前 vs 应有）

**当前实际逻辑**：打开 Main → 等待 iframe RealDatas → **直接**在 iframe 内抓取所有 table（**未点击「选择区域」、未选择任何省/市**）→ 再点击 Tab「发布说明」「实时数据」后各抓一次表。因此默认只拿到表头 + 1 条或 0 条数据。

**为何只有 1 条数据**：站点通常要求**先选区域（如全国/某省）再按区域请求并渲染表格**。未选区域时只展示默认视图，故只有 1 条。

**应有逻辑**：在 iframe 内**先点击「选择区域」→ 选择「全国」或某省/市 → 等待表格刷新**，再抓取 table；若有分页则翻页后继续抓。

**表头与数据拆成两个 sheet**：页面用两个 `<table>` 分别放表头和数据，脚本按「每个 table 一个 sheet」导出。后续会合并为「表头+数据」一张表再导出。

## 技术选型
- **浏览器自动化**: Playwright（支持 Chromium，可处理证书与动态内容）
- **Excel 导出**: openpyxl + pandas
- **配置**: 选择器与 URL 写在 scripts/config.py，便于站点改版后调整

## 风险与应对
- **HTTPS 证书**: 若遇证书错误，Playwright 可设置 `ignore_https_errors=True`
- **登录**: 当前为公开页面，若需登录则需在 scripts/config.py 中配置账号或 Cookie
- **反爬**: 控制请求间隔，使用单浏览器会话，不并发
