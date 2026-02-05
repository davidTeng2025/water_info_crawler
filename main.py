# -*- coding: utf-8 -*-
"""
入口脚本：执行页面抓取并保存为 Excel。
用法: python main.py
prod 模式下：每个省份独立保存为 output/water_info_<省份>.xlsx
"""
import os
import re
import sys
from pathlib import Path

# 保证在项目根目录运行
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import config
from crawler import run as run_crawl
from export_excel import to_excel


def _safe_filename(name):
    """将省份名等转为可做文件名的字符串。"""
    s = (name or "").strip()
    s = re.sub(r'[\\/:*?"<>|]', "_", s)
    return s[:50] if s else "未命名"


def main():
    print("开始抓取:", config.BASE_URL)
    sheets_data = run_crawl()
    if not sheets_data:
        print("未抓取到任何数据，请检查网络或页面结构。")
        return 1
    # prod 模式：带 province 的项按省份独立保存为 water_info_<省份>.xlsx
    per_province = [s for s in sheets_data if s.get("province") is not None]
    if per_province:
        out_dir = Path(config.OUTPUT_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        for item in per_province:
            province = item.get("province", "")
            one_sheet = [{"sheet_name": item.get("sheet_name", "水质实时数据"), "rows": item.get("rows", [])}]
            fname = "water_info_%s.xlsx" % _safe_filename(province)
            out_path = out_dir / fname
            saved = to_excel(one_sheet, output_path=str(out_path))
            print("已保存:", saved)
        return 0
    # 非 prod 或旧格式：全部写入单个文件
    for item in sheets_data:
        item.pop("province", None)
    out_path = Path(config.OUTPUT_DIR) / config.OUTPUT_EXCEL
    saved = to_excel(sheets_data, output_path=str(out_path))
    print("已保存到:", saved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
