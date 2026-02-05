# -*- coding: utf-8 -*-
"""抓取配置：URL、超时、选择器。站点改版时只需修改此处。"""
import glob
import os
from pathlib import Path

# 项目根目录（本文件在 scripts/ 下，parent.parent = 根目录），输出目录固定为根目录/output
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = str(_PROJECT_ROOT / "output")

# 目标站点
BASE_URL = "https://szzdjc.cnemc.cn:8070/GJZ/Business/Publish/Main.html"


def _find_full_chromium():
    """查找 Playwright 缓存的完整 Chromium（chrome-linux64/chrome），
    用于替代 headless shell，避免缺少 libatk 等依赖。
    """
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright")
    pattern = os.path.join(cache, "chromium-*", "chrome-linux64", "chrome")
    hits = glob.glob(pattern)
    return hits[0] if hits else None


# 使用完整 Chromium 可执行文件（避免 headless shell 的 libatk 依赖）；None 表示使用默认
CHROMIUM_EXECUTABLE_PATH = os.environ.get("WATER_CRAWLER_CHROMIUM_PATH") or _find_full_chromium()
BASE_DOMAIN = "https://szzdjc.cnemc.cn:8070"

# 浏览器与超时
HEADLESS = True  # 设为 False 可看到浏览器窗口，便于调试
TIMEOUT_MS = 30000  # 页面加载超时（毫秒）
CLICK_WAIT_MS = 2000  # 点击后等待内容加载（毫秒）
NAVIGATION_WAIT_MS = 3000  # 导航后额外等待（毫秒）

# 是否忽略 HTTPS 证书错误（内网或自签名证书时使用）
IGNORE_HTTPS_ERRORS = True

# 模拟真实浏览器的请求头（部分站点对无头或异常 UA 会返回空或拒绝）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
EXTRA_HTTP_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 可点击入口：用于优先点击的文本或部分匹配（如菜单名）
# 脚本会同时自动发现页面上的链接与按钮，此处仅作补充
PRIORITY_LINK_TEXTS = ["实时数据", "发布说明", "数据", "说明"]

# 表格与列表选择器（若站点使用固定 class/id，可在此指定，否则由脚本自动识别 table）
TABLE_SELECTOR = "table"
LIST_CONTAINER_SELECTOR = ""  # 例如 ".list-container"，空则不用

# 分页选择器（若有分页）
PAGINATION_SELECTOR = ""  # 例如 ".pagination a"，空则脚本会尝试查找“下一页”等

# 数据 iframe（主数据在 RealDatas.html 内）
IFRAME_WAIT_MS = 8000  # 主页面加载后等待 iframe 及 AJAX 数据的时间（毫秒）
DATA_IFRAME_ID = "MF"  # 数据 iframe 的 id
DATA_IFRAME_SRC_CONTAINS = "RealDatas"  # 用于匹配 iframe src，定位数据 frame
# iframe 内优先点击的 Tab/链接文本（点击后重新提取表格）
IFRAME_TAB_TEXTS = ["发布说明", "实时数据"]
# 区域选择：先选区域再抓取，才能拿到多行数据
REGION_SELECT_LABELS = ["选择区域", "区域"]  # 用于定位区域下拉/按钮的文本
REGION_OPTION = "全国"  # test 模式下默认选择的区域
# 自定义下拉（与 Main/RealDatas 一致）：区域由 button#ddm_Area 打开，一级为 a.area-item，二级在 li.dropdown-submenu 内
REGION_TRIGGER_SELECTOR = "button#ddm_Area"  # 区域下拉触发按钮
REGION_LEVEL1_OPTION_SELECTOR = "ul.dropdown-menu[aria-labelledby='ddm_Area'] > li > a.area-item"  # 一级：全国 + 各省
REGION_DROPDOWN_OPTION_SELECTOR = "a.area-item"  # 下拉项（兼容旧逻辑，优先用 LEVEL1）
REGION_WAIT_MS = 6000  # 选择区域后等待表格开始刷新的时间（毫秒）
TABLE_LOAD_WAIT_MS = 10000  # 等待表格数据加载完成的最长时间（毫秒），再抓取
RANDOM_WAIT_MAX_MS = 5000  # 相邻操作间随机等待上限（毫秒），防反爬
# 运行模式：test=仅抓「全国」；prod=按省/市逐个选择再抓（注意间隔防反爬）
RUN_MODE = os.environ.get("WATER_CRAWLER_MODE", "test")  # "test" | "prod"
PROD_INTERVAL_MS = 5000  # prod 模式下每个区域之间基础间隔（毫秒），再加随机等待
# prod 下只抓前 N 个一级地域，便于调试；None 或 0 表示不限制
PROD_TOP_N = None  # 例如 3 表示只抓前 3 个一级（如 全国、北京、天津）
# 单 sheet 汇总：所有 iframe 数据写入一个 sheet 时的名称及区域列名
SINGLE_SHEET_NAME = "水质实时数据"
REGION_COLUMN_1 = "一级区域"
REGION_COLUMN_2 = "二级区域"
# 只导出“有效”表格，过滤掉明显是布局的空表/单行表
MIN_TABLE_ROWS = 2  # 至少行数（含表头）
MIN_TABLE_COLS = 2  # 至少列数

# 输出
OUTPUT_EXCEL = "water_info_data.xlsx"
# OUTPUT_DIR 已在本文件顶部设为 项目根/output
