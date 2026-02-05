# -*- coding: utf-8 -*-
"""
水质数据 SQLite 库：抓取结果 + 坐标入库、KD-tree 最近邻查询、全量更新与无缝表切换。

功能概要:
  1. 从 output/water_info_*.xlsx 加载记录，用「省份」+「断面名称」拼接地址并地理编码，写入 SQLite。
  2. 表结构: id, province, site_name, address, lat, lon, data_json（全量水质数据 JSON）。
  3. 基于 KD-tree 的最近邻查询：用户输入地名 -> 转坐标 -> 与库内所有坐标算距离 -> 返回最近位置的水质数据。
  4. 全量更新接口：执行 prod 抓取 -> 地理编码 -> 写入新表 -> 原子切换表（无缝、无中断）。
  5. 无缝切换（线上无中断）：查询始终读表 water_data；更新时先写 water_data_new，再在单事务内
     DROP water_data; ALTER TABLE water_data_new RENAME TO water_data; COMMIT; 因此不会出现表为空或半新半旧。

调用示例:
  # 全量更新（抓取 + 坐标 + 写库并切换）
  python water_db.py update [--scheme amap|offline]
  # 跳过抓取，仅从已有 output/water_info_*.xlsx 做地理编码与入库
  python water_db.py update --skip-crawl [--scheme amap|offline]

  # 最近邻查询（KD-tree）
  python water_db.py query --place "郑州" [--top 10] [--scheme amap]

  # 仅建表
  python water_db.py init [--db output/water_data.db]

  # 启动 HTTP（GET /nearest?place=郑州&top=5）
  python water_db.py serve [--port 5001]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

try:
    import pandas as pd
except ImportError:
    pd = None

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(config.OUTPUT_DIR)
DB_PATH = OUTPUT_DIR / "water_data.db"
TABLE_CURRENT = "water_data"       # 线上查询始终读此表
TABLE_STAGING = "water_data_new"   # 全量更新时先写此表，再原子切换
DATA_GLOB = "water_info_*.xlsx"

# 拼接「地址」的列（仅省份 + 断面名称）
ADDRESS_COLUMNS = ["省份", "断面名称"]


def _row_address(row: dict) -> str:
    parts = []
    for col in ADDRESS_COLUMNS:
        if col in row and pd is not None and pd.notna(row.get(col)) and str(row[col]).strip():
            parts.append(str(row[col]).strip())
    return "".join(parts) if parts else ""


def _row_to_json(row: dict) -> str:
    """将单行转为 JSON，NaN -> null。"""
    d = {}
    for k, v in row.items():
        if k.startswith("_"):
            continue
        if pd is not None and pd.isna(v):
            d[k] = None
        else:
            try:
                json.dumps(v)
                d[k] = v
            except (TypeError, ValueError):
                d[k] = str(v)
    return json.dumps(d, ensure_ascii=False)


def _json_to_record(data_json: str) -> dict:
    try:
        return json.loads(data_json) if data_json else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 建表与写入
# ---------------------------------------------------------------------------
def get_connection(path: Path | str | None = None) -> sqlite3.Connection:
    p = path if path is not None else DB_PATH
    p = Path(p) if not isinstance(p, Path) else p
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def create_table(conn: sqlite3.Connection, table_name: str = TABLE_CURRENT) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS %s (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province TEXT,
            site_name TEXT,
            address TEXT NOT NULL,
            lat REAL,
            lon REAL,
            data_json TEXT
        )
        """ % (table_name,)
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_%s_lat_lon ON %s(lat, lon)" % (table_name, table_name)
    )
    conn.commit()


def drop_table_if_exists(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute("DROP TABLE IF EXISTS %s" % (table_name,))
    conn.commit()


# ---------------------------------------------------------------------------
# 1. 从 Excel 加载记录，2. 用「省份」+「断面名称」拼接地址并地理编码
# ---------------------------------------------------------------------------
def load_records_from_excel() -> list[dict]:
    """从 output/water_info_*.xlsx 加载所有行，每条增加 address = 省份 + 断面名称。"""
    if pd is None:
        raise ImportError("请安装 pandas 与 openpyxl")
    records = []
    for path in sorted(OUTPUT_DIR.glob(DATA_GLOB)):
        try:
            df = pd.read_excel(path, sheet_name=0)
            for _, row in df.iterrows():
                r = row.to_dict()
                r["address"] = _row_address(r)
                r["_source_file"] = path.name
                records.append(r)
        except Exception:
            continue
    return records


def geocode_records(
    records: list[dict],
    scheme: str = "amap",
    cache: dict | None = None,
) -> list[dict]:
    """为每条记录计算坐标：address 已存在，调用 geo_search.geocode 写入 lat, lon。"""
    from geo_search import geocode, load_json_cache, save_json_cache

    if cache is None:
        cache = load_json_cache()
    for r in records:
        addr = r.get("address", "")
        if not addr:
            r["lat"], r["lon"] = None, None
            continue
        coord = geocode(scheme, addr, cache)
        if coord:
            r["lat"], r["lon"] = coord[0], coord[1]
        else:
            r["lat"], r["lon"] = None, None
    if scheme != "offline":
        save_json_cache(cache)
    return records


# ---------------------------------------------------------------------------
# 插入到指定表（用于全量更新时写入 staging 表）
# ---------------------------------------------------------------------------
def insert_records(conn: sqlite3.Connection, records: list[dict], table_name: str = TABLE_STAGING) -> int:
    create_table(conn, table_name)
    cur = conn.cursor()
    n = 0
    for r in records:
        province = r.get("省份") or r.get("province")
        site_name = r.get("断面名称") or r.get("site_name")
        address = r.get("address", "")
        lat = r.get("lat")
        lon = r.get("lon")
        data_json = _row_to_json(r)
        cur.execute(
            "INSERT INTO %s (province, site_name, address, lat, lon, data_json) VALUES (?,?,?,?,?,?)" % (table_name,),
            (province, site_name, address, lat, lon, data_json),
        )
        n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# 无缝切换：先写 water_data_new，再原子替换 water_data
# ---------------------------------------------------------------------------
def swap_tables(conn: sqlite3.Connection) -> None:
    """
    原子切换：删除当前表，将 staging 表改名为当前表。
    查询端始终读 water_data，仅在 COMMIT 瞬间从旧数据切换到新数据，无中间态。
    """
    conn.execute("DROP TABLE IF EXISTS %s" % (TABLE_CURRENT,))
    conn.execute("ALTER TABLE %s RENAME TO %s" % (TABLE_STAGING, TABLE_CURRENT))
    conn.commit()


# ---------------------------------------------------------------------------
# 3. 高效最近邻：用户输入地名 -> 转坐标 -> KD-tree 与库内坐标算距离 -> 返回最近位置的水质数据
# ---------------------------------------------------------------------------
def query_nearest(
    place_name: str,
    k: int = 10,
    scheme: str = "amap",
    db_path: Path | None = None,
) -> list[tuple[dict, float]]:
    """
    根据用户输入地名，转成坐标后与数据库中所有有效坐标做距离计算（KD-tree），
    返回距离最近的 k 条记录的完整水质数据及距离(km)。
    返回: [(record_dict, distance_km), ...]
    """
    from geo_search import geocode, load_json_cache, haversine_km

    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return _query_nearest_fallback(place_name, k=k, scheme=scheme, db_path=db_path)

    cache = load_json_cache()
    coord = geocode(scheme, (place_name or "").strip(), cache)
    if coord is None:
        return []
    qlat, qlon = coord

    conn = get_connection(db_path)
    cur = conn.execute(
        "SELECT id, lat, lon, data_json FROM %s WHERE lat IS NOT NULL AND lon IS NOT NULL" % TABLE_CURRENT
    )
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return []

    import numpy as np
    points = [(r[1], r[2]) for r in rows]
    tree = cKDTree(points)
    k_actual = min(k, len(points))
    _, indices = tree.query([(qlat, qlon)], k=k_actual)
    indices = np.atleast_1d(indices).tolist()

    out = []
    for idx in indices:
        if idx >= len(rows):
            continue
        r = rows[idx]
        rec = _json_to_record(r[3])
        lat, lon = r[1], r[2]
        dist_km = haversine_km(qlat, qlon, lat, lon)
        rec["_distance_km"] = round(dist_km, 4)
        out.append((rec, rec["_distance_km"]))
    return out[:k]


def _query_nearest_fallback(
    place_name: str,
    k: int = 10,
    scheme: str = "amap",
    db_path: Path | None = None,
) -> list[tuple[dict, float]]:
    """无 scipy 时：查全表，用 Haversine 算距离后排序取前 k。"""
    from geo_search import geocode, load_json_cache, haversine_km

    cache = load_json_cache()
    coord = geocode(scheme, (place_name or "").strip(), cache)
    if coord is None:
        return []
    qlat, qlon = coord

    conn = get_connection(db_path)
    cur = conn.execute("SELECT id, lat, lon, data_json FROM %s WHERE lat IS NOT NULL AND lon IS NOT NULL" % TABLE_CURRENT)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return []

    scored = []
    for r in rows:
        dist = haversine_km(qlat, qlon, r[1], r[2])
        rec = _json_to_record(r[3])
        rec["_distance_km"] = round(dist, 4)
        scored.append((rec, dist))
    scored.sort(key=lambda x: x[1])
    return [(rec, rec["_distance_km"]) for rec, _ in scored[:k]]


# ---------------------------------------------------------------------------
# 4. 全量更新：prod 抓取 -> 地理编码 -> 写入 staging 表 -> 原子切换
# ---------------------------------------------------------------------------
def full_update(scheme: str = "amap", skip_crawl: bool = False) -> None:
    """
    全量更新数据库：
    1) 可选：执行 prod 模式抓取（main.py 逻辑），生成 output/water_info_*.xlsx；若 skip_crawl=True 则跳过，直接使用已有 Excel。
    2) 从 Excel 加载记录，用「省份」+「断面名称」地理编码
    3) 写入 water_data_new，再原子切换为 water_data（无缝、无服务中断）
    """
    if not skip_crawl:
        # 1) prod 抓取（环境变量 + 已导入的 config 需同步，否则 crawler 仍读到 RUN_MODE=test）
        os.environ["WATER_CRAWLER_MODE"] = "prod"
        config.RUN_MODE = "prod"
        from main import main as main_crawl
        ret = main_crawl()
        if ret != 0:
            raise RuntimeError("抓取失败，请检查网络或页面")
    else:
        print("已跳过抓取步骤，直接使用 output/ 下已有 Excel 进行地理编码与入库")

    # 2) 加载 + 地理编码
    records = load_records_from_excel()
    if not records:
        raise RuntimeError("未从 output/water_info_*.xlsx 加载到任何记录")
    records = geocode_records(records, scheme=scheme)

    # 3) 写入新表并原子切换
    conn = get_connection()
    create_table(conn, TABLE_STAGING)
    n = insert_records(conn, records, table_name=TABLE_STAGING)
    swap_tables(conn)
    conn.close()
    print("全量更新完成: 共 %d 条，已原子切换为 water_data" % n)


# ---------------------------------------------------------------------------
# 5. 无缝切换说明（线上无中断）
# ---------------------------------------------------------------------------
# 实现方式：
# - 线上查询始终读取表名 water_data。
# - 全量更新时：先创建 water_data_new，将新数据全部写入 water_data_new，
#   最后在单事务内执行：DROP TABLE water_data; ALTER TABLE water_data_new RENAME TO water_data; COMMIT;
# - 因此任意时刻要么是旧 water_data，要么是完整新 water_data，不会出现“表为空”或“半新半旧”。
# 若需双写/蓝绿：可维护 water_data_v1 / water_data_v2，用一列配置或另一张 meta 表记录当前生效表名，
# 更新时写非当前表，再原子更新 meta 指向新表；本实现采用单表名 + staging 改名，更简单且等效。


# ---------------------------------------------------------------------------
# HTTP 接口（可选）：GET /nearest 基于 DB + KD-tree
# ---------------------------------------------------------------------------
def serve(port: int = 5001, db_path: Path | None = None) -> None:
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        raise ImportError("请安装 flask: pip install flask")
    app = Flask(__name__)

    @app.route("/nearest")
    def api_nearest():
        place = request.args.get("place", "").strip()
        top = int(request.args.get("top", 10))
        scheme = request.args.get("scheme", "amap")
        if not place:
            return jsonify({"error": "缺少 place 参数"}), 400
        results = query_nearest(place, k=top, scheme=scheme, db_path=db_path)
        out = [rec for rec, _ in results]
        return jsonify({"place": place, "count": len(out), "results": out})

    print("DB 最近邻接口: http://127.0.0.1:%s/nearest?place=郑州&top=5" % port)
    app.run(host="0.0.0.0", port=port)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="水质数据 SQLite 与最近邻查询")
    parser.add_argument("action", choices=["update", "query", "init", "serve"], nargs="?",
                        help="update=全量更新 query=最近邻查询 init=仅建表 serve=启动HTTP")
    parser.add_argument("--place", default="", help="query 时必填，地名")
    parser.add_argument("--top", type=int, default=10, help="query 返回最近几条")
    parser.add_argument("--scheme", choices=["amap", "offline"], default="amap")
    parser.add_argument("--skip-crawl", action="store_true",
                        help="update 时跳过抓取，仅从已有 output/water_info_*.xlsx 做地理编码与入库")
    parser.add_argument("--db", default="", help="数据库路径，默认 output/water_data.db")
    parser.add_argument("--port", type=int, default=5001, help="serve 时端口")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH

    if args.action == "init":
        conn = get_connection(db_path)
        create_table(conn, TABLE_CURRENT)
        conn.close()
        print("已建表:", db_path)
        return 0

    if args.action == "update":
        full_update(scheme=args.scheme, skip_crawl=args.skip_crawl)
        return 0

    if args.action == "serve":
        serve(port=args.port, db_path=db_path)
        return 0

    if args.action == "query":
        place = (args.place or "").strip()
        if not place:
            print("请提供 --place 地名")
            return 1
        results = query_nearest(place, k=args.top, scheme=args.scheme, db_path=db_path)
        if not results:
            print("未查到结果（地名无法解析坐标或数据库无有效坐标）")
            return 1
        for i, (rec, dist) in enumerate(results, 1):
            summary = rec.get("address") or rec.get("断面名称") or rec.get("省份") or ""
            print("  --- %d. [%.2f km] %s ---" % (i, dist, summary))
            print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
