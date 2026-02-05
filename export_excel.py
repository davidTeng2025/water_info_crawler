# -*- coding: utf-8 -*-
"""将抓取结果写入 Excel。每个 sheet 对应一个页面/模块。"""
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    pd = None

import config


def to_excel(sheets_data, output_path=None):
    """
    sheets_data: list of dict:
        [ {"sheet_name": "实时数据", "rows": [["列1","列2"], ["a","b"], ...] }, ... ]
    """
    if pd is None:
        raise ImportError("请安装 pandas 与 openpyxl: pip install pandas openpyxl")
    out = output_path or Path(config.OUTPUT_DIR) / config.OUTPUT_EXCEL
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    used_names = set()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for item in sheets_data:
            name = item.get("sheet_name", "Sheet")
            rows = item.get("rows", [])
            # 限制 sheet 名称长度与非法字符，并保证唯一
            base = name[:28].replace("[", "(").replace("]", ")").replace("*", "-").replace(":", "-").replace("?", "-").replace("/", "-").replace("\\", "-")
            safe_name = base
            n = 0
            while safe_name in used_names:
                n += 1
                safe_name = f"{base[:24]}_{n}"[:31]
            used_names.add(safe_name)
            if not rows:
                pd.DataFrame([["(无数据)"]]).to_excel(writer, sheet_name=safe_name, index=False, header=False)
            else:
                # 统一列数：部分表格首行与数据行列数不一致会导致 pandas 报错，按最大列数补齐
                max_cols = max(len(r) for r in rows)
                padded = [list(r) + [""] * (max_cols - len(r)) for r in rows]
                df = pd.DataFrame(padded[1:], columns=padded[0])
                df.to_excel(writer, sheet_name=safe_name, index=False)
    return str(out)
