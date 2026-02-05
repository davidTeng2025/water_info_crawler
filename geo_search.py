# -*- coding: utf-8 -*-
"""
根据抓取数据中的位置信息转地理坐标，并支持按地名查询物理距离最近的记录。

================================================================================
调用方法
================================================================================

一、命令行（CLI）
----------------
在项目根目录执行（需已存在 output/water_info_*.xlsx）：

  1) 仅测试地址转坐标（不查最近记录）
     python geo_search.py --geocode "北京市朝阳区" [--scheme amap|offline]

  2) 给定两个地点，转坐标后计算距离（km）
     python geo_search.py --distance "北京" "郑州" [--scheme amap|offline]

  3) 构建坐标缓存（首次或数据更新后执行一次）
     python geo_search.py --build-cache [--scheme amap|offline]

  4) 按地名查最近 N 条记录
     python geo_search.py "北京市朝阳区" [--top 10] [--scheme amap|offline]

  5) 使用离线坐标表（需先准备 output/geo_cache.csv，列：address,lat,lon）
     python geo_search.py "郑州" --top 5 --scheme offline

  6) 启动 HTTP 查询接口
     python geo_search.py --serve [--port 5000]
     访问: GET http://127.0.0.1:5000/nearest?place=郑州&top=5

  参数说明:
    place         查询地名（位置参数，与 --build-cache / --serve 二选一）
    --top N       返回最近 N 条，默认 10
    --scheme      地理编码方案: amap(高德,需 AMAP_KEY) | offline(CSV)
    --build-cache 对全部唯一地址建坐标缓存并写入 output/geo_cache.json
    --geocode ADDRESS  仅将地址转成坐标并打印（用于测试地理编码）
    --distance A B    给定两个地点描述，分别转坐标后计算距离(km)
    --serve       启动 HTTP 服务
    --port        与 --serve 同用时端口，默认 5000

二、HTTP 接口（--serve 启动后）
-----------------------------
  GET /nearest
  查询参数: place(必填), top(可选), scheme(可选, 默认 amap)
  返回: JSON，{ "place": "...", "count": N, "results": [ { 记录字段, "_distance_km": 距离km }, ... ] }

  GET /distance
  查询参数: place_a(必填), place_b(必填), scheme(可选, 默认 amap)
  返回: JSON，{ "place_a": "...", "place_b": "...", "coord_a": [lat,lon], "coord_b": [lat,lon], "distance_km": N }

三、代码中调用
--------------
  from geo_search import load_all_records, search_nearest, build_cache, distance_between

  # 查最近记录（返回 [(record_dict, distance_km), ...]）
  results = search_nearest("郑州市", top=10, scheme="amap")

  # 两地转坐标并计算距离（返回 (distance_km, coord_a, coord_b)）
  dist_km, coord_a, coord_b = distance_between("北京", "郑州", scheme="amap")

  # 仅加载记录（每条含 _address、_source_file）
  records = load_all_records()

  # 构建缓存（写入 output/geo_cache.json）
  build_cache(scheme="amap")
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

# 可选依赖
try:
    import pandas as pd
except ImportError:
    pd = None

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(config.OUTPUT_DIR)
GEO_CACHE_JSON = OUTPUT_DIR / "geo_cache.json"   # 方案 A/B 的地址 -> (lat, lon) 缓存
GEO_CACHE_CSV = OUTPUT_DIR / "geo_cache.csv"     # 方案 C 离线表：address, lat, lon
DATA_GLOB = "water_info_*.xlsx"                  # 数据文件匹配

# 用于拼地址的列（按优先级，存在则用）
ADDRESS_COLUMNS = ["省份", "城市", "地市", "地区", "断面名称", "流域"]

# 高德 API（方案 B）需配置 key，否则跳过
AMAP_KEY = os.environ.get("AMAP_KEY", "88daf6da379071667d2a1cd2d8efb861")


# ---------------------------------------------------------------------------
# 高德地图 Web 服务 API（国内精度好，需 AMAP_KEY）
# ---------------------------------------------------------------------------
def geocode_amap(address: str) -> tuple[float, float] | None:
    if not AMAP_KEY:
        return None
    try:
        import urllib.request
        import urllib.parse
        q = urllib.parse.quote(address)
        url = f"https://restapi.amap.com/v3/geocode/geo?key={AMAP_KEY}&address={q}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") == "1" and data.get("geocodes"):
            loc = data["geocodes"][0]["location"]
            lon, lat = map(float, loc.split(","))
            return (lat, lon)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 离线 CSV 表（address, lat, lon），无网络、无 key
# ---------------------------------------------------------------------------
def load_offline_cache() -> dict[str, tuple[float, float]]:
    path = GEO_CACHE_CSV
    out = {}
    if not path.exists():
        return out
    try:
        df = pd.read_csv(path, encoding="utf-8")
        if "address" not in df.columns or "lat" not in df.columns or "lon" not in df.columns:
            return out
        for _, row in df.iterrows():
            addr = str(row["address"]).strip()
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                out[addr] = (lat, lon)
            except (ValueError, TypeError):
                continue
    except Exception:
        pass
    return out


def geocode_offline(address: str, cache: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    # 精确匹配
    if address in cache:
        return cache[address]
    # 模糊：包含关系
    for key, coords in cache.items():
        if address in key or key in address:
            return coords
    return None


# ---------------------------------------------------------------------------
# 统一地理编码入口 + 缓存
# ---------------------------------------------------------------------------
def geocode(scheme: str, address: str, cache: dict | None = None) -> tuple[float, float] | None:
    if cache is None:
        cache = {}
    address = (address or "").strip()
    if not address:
        return None
    if address in cache:
        return cache[address]
    if scheme == "offline":
        offline = load_offline_cache()
        coord = geocode_offline(address, offline)
    else:
        coord = geocode_amap(address)
    if coord:
        cache[address] = coord
    return coord


def load_json_cache() -> dict[str, list[float]]:
    if not GEO_CACHE_JSON.exists():
        return {}
    try:
        with open(GEO_CACHE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: tuple(v) for k, v in data.items()}
    except Exception:
        return {}


def save_json_cache(cache: dict[str, tuple[float, float]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(GEO_CACHE_JSON, "w", encoding="utf-8") as f:
        json.dump({k: [c[0], c[1]] for k, c in cache.items()}, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 数据加载：从 output/water_info_*.xlsx 读入所有记录
# ---------------------------------------------------------------------------
def _row_address(row: dict, columns: list[str]) -> str:
    parts = []
    for col in columns:
        if col in row and pd.notna(row[col]) and str(row[col]).strip():
            parts.append(str(row[col]).strip())
    return "".join(parts) if parts else ""


def load_all_records() -> list[dict]:
    if pd is None:
        raise ImportError("请安装 pandas: pip install pandas openpyxl")
    records = []
    for path in sorted(OUTPUT_DIR.glob(DATA_GLOB)):
        try:
            df = pd.read_excel(path, sheet_name=0)
            cols = list(df.columns)
            for _, row in df.iterrows():
                r = row.to_dict()
                addr = _row_address(r, ADDRESS_COLUMNS)
                r["_address"] = addr
                r["_source_file"] = path.name
                records.append(r)
        except Exception:
            continue
    return records


# ---------------------------------------------------------------------------
# 球面距离（Haversine），单位 km
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0  # 地球半径 km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ---------------------------------------------------------------------------
# 最近记录查询
# ---------------------------------------------------------------------------
def search_nearest(
    place_name: str,
    top: int = 10,
    scheme: str = "amap",
    records: list[dict] | None = None,
    cache: dict[str, tuple[float, float]] | None = None,
) -> list[tuple[dict, float]]:
    """
    输入地名，返回物理距离最近的 top 条记录。
    返回: [(record, distance_km), ...]
    """
    if records is None:
        records = load_all_records()
    if cache is None and scheme != "offline":
        cache = load_json_cache()

    # 查询点坐标
    query_coord = geocode(scheme, place_name, cache)
    if query_coord is None:
        return []  # 地名无法解析坐标

    qlat, qlon = query_coord
    if scheme != "offline":
        cache[place_name] = query_coord

    # 为每条记录解析坐标（用 _address）
    results = []
    offline_cache = load_offline_cache() if scheme == "offline" else None
    for r in records:
        addr = r.get("_address", "")
        if not addr:
            continue
        if scheme == "offline":
            coord = geocode_offline(addr, offline_cache or {})
        else:
            coord = geocode(scheme, addr, cache)
        if coord is None:
            continue
        lat, lon = coord
        dist = haversine_km(qlat, qlon, lat, lon)
        results.append((r, dist))

    results.sort(key=lambda x: x[1])
    return results[: top]


# ---------------------------------------------------------------------------
# 两地转坐标并计算距离
# ---------------------------------------------------------------------------
def distance_between(
    place_a: str,
    place_b: str,
    scheme: str = "amap",
    cache: dict[str, tuple[float, float]] | None = None,
) -> tuple[float | None, tuple[float, float] | None, tuple[float, float] | None]:
    """
    给定两个地点描述，分别转成坐标后计算球面距离（Haversine，单位 km）。

    参数:
        place_a: 地点 A 描述（如「北京市朝阳区」）
        place_b: 地点 B 描述（如「郑州市」）
        scheme: 地理编码方案，amap | offline
        cache: 可选，地址 -> (lat, lon) 缓存，用于复用/写入

    返回:
        (distance_km, coord_a, coord_b)
        - distance_km: 两地点间距离（km），任一方无法解析坐标时为 None
        - coord_a: (lat, lon) 或 None
        - coord_b: (lat, lon) 或 None
    """
    if cache is None and scheme != "offline":
        cache = load_json_cache()
    coord_a = geocode(scheme, (place_a or "").strip(), cache)
    coord_b = geocode(scheme, (place_b or "").strip(), cache)
    if coord_a is None or coord_b is None:
        return (None, coord_a, coord_b)
    dist_km = haversine_km(coord_a[0], coord_a[1], coord_b[0], coord_b[1])
    if scheme != "offline" and cache is not None:
        cache[(place_a or "").strip()] = coord_a
        cache[(place_b or "").strip()] = coord_b
    return (dist_km, coord_a, coord_b)


# ---------------------------------------------------------------------------
# 构建缓存：对全部唯一地址做地理编码并写入 geo_cache.json
# ---------------------------------------------------------------------------
def build_cache(scheme: str = "amap") -> None:
    records = load_all_records()
    addrs = set(r.get("_address", "") for r in records if r.get("_address"))
    cache = load_json_cache()
    total = len(addrs)
    for i, addr in enumerate(sorted(addrs)):
        if addr in cache:
            continue
        coord = geocode(scheme, addr, cache)
        if coord:
            cache[addr] = coord
        if (i + 1) % 10 == 0:
            print("  已处理 %d / %d 个地址" % (i + 1, total))
    save_json_cache(cache)
    print("  缓存已写入: %s（共 %d 条）" % (GEO_CACHE_JSON, len(cache)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="按地名查水质数据中物理距离最近的记录")
    parser.add_argument("place", nargs="?", default="", help="查询地名，例如：郑州市、北京市朝阳区")
    parser.add_argument("--top", type=int, default=10, help="返回最近 N 条，默认 10")
    parser.add_argument("--scheme", choices=["amap", "offline"], default="amap",
                        help="地理编码方案: amap(高德,需 AMAP_KEY) | offline(CSV)")
    parser.add_argument("--build-cache", action="store_true", help="对全部唯一地址建坐标缓存")
    parser.add_argument("--geocode", metavar="ADDRESS", default="",
                        help="仅将地址转成坐标并打印，不查最近记录。例: --geocode \"北京市朝阳区\"")
    parser.add_argument("--distance", nargs=2, metavar=("PLACE_A", "PLACE_B"), default=None,
                        help="给定两个地点描述，分别转坐标后计算距离(km)。例: --distance \"北京\" \"郑州\"")
    parser.add_argument("--serve", action="store_true", help="启动简单 HTTP 接口")
    parser.add_argument("--port", type=int, default=5000, help="--serve 时的端口")
    args = parser.parse_args()

    # 仅测试地址转坐标
    if args.geocode:
        addr = args.geocode.strip()
        if not addr:
            print("请提供地址，例如: python geo_search.py --geocode \"北京市朝阳区\"")
            return 1
        cache = load_json_cache()
        coord = geocode(args.scheme, addr, cache)
        if coord is None:
            print("未解析到坐标（可换 --scheme amap 或检查网络/AMAP_KEY）")
            return 1
        lat, lon = coord
        print("地址: %s" % addr)
        print("坐标: lat=%.6f, lon=%.6f" % (lat, lon))
        if args.scheme != "offline":
            cache[addr] = coord
            save_json_cache(cache)
        return 0

    # 两地转坐标并计算距离
    if args.distance is not None:
        place_a, place_b = args.distance[0].strip(), args.distance[1].strip()
        if not place_a or not place_b:
            print("请提供两个地点，例如: python geo_search.py --distance \"北京\" \"郑州\"")
            return 1
        cache = load_json_cache()
        dist_km, coord_a, coord_b = distance_between(place_a, place_b, scheme=args.scheme, cache=cache)
        if dist_km is None:
            print("无法解析坐标（地点 A: %s -> %s；地点 B: %s -> %s）" % (
                place_a, "OK" if coord_a else "失败", place_b, "OK" if coord_b else "失败"))
            return 1
        print("地点 A: %s -> lat=%.6f, lon=%.6f" % (place_a, coord_a[0], coord_a[1]))
        print("地点 B: %s -> lat=%.6f, lon=%.6f" % (place_b, coord_b[0], coord_b[1]))
        print("距离: %.2f km" % dist_km)
        if args.scheme != "offline":
            save_json_cache(cache)
        return 0

    if args.build_cache:
        print("正在构建坐标缓存 (scheme=%s) ..." % args.scheme)
        build_cache(scheme=args.scheme)
        return 0

    if args.serve:
        try:
            from flask import Flask, request, jsonify
        except ImportError:
            print("请安装 flask: pip install flask")
            return 1
        app = Flask(__name__)

        @app.route("/nearest")
        def api_nearest():
            place = request.args.get("place", "").strip()
            top = int(request.args.get("top", 10))
            scheme = request.args.get("scheme", "amap")
            if not place:
                return jsonify({"error": "缺少 place 参数"}), 400
            results = search_nearest(place, top=top, scheme=scheme)
            out = []
            for r, dist in results:
                rec = {k: (v if pd.notna(v) else None) for k, v in r.items() if not k.startswith("_")}
                rec["_distance_km"] = round(dist, 4)
                out.append(rec)
            return jsonify({"place": place, "count": len(out), "results": out})

        @app.route("/distance")
        def api_distance():
            place_a = request.args.get("place_a", "").strip()
            place_b = request.args.get("place_b", "").strip()
            scheme = request.args.get("scheme", "amap")
            if not place_a or not place_b:
                return jsonify({"error": "缺少 place_a 或 place_b 参数"}), 400
            dist_km, coord_a, coord_b = distance_between(place_a, place_b, scheme=scheme)
            if dist_km is None:
                return jsonify({"error": "无法解析坐标", "place_a": place_a, "place_b": place_b}), 400
            return jsonify({
                "place_a": place_a, "place_b": place_b,
                "coord_a": [round(coord_a[0], 6), round(coord_a[1], 6)] if coord_a else None,
                "coord_b": [round(coord_b[0], 6), round(coord_b[1], 6)] if coord_b else None,
                "distance_km": round(dist_km, 4),
            })

        print("HTTP 接口: http://127.0.0.1:%s/nearest?place=郑州&top=5" % args.port)
        print("          http://127.0.0.1:%s/distance?place_a=北京&place_b=郑州" % args.port)
        app.run(host="0.0.0.0", port=args.port)
        return 0

    if not args.place:
        parser.print_help()
        return 0

    records = load_all_records()
    print("已加载 %d 条记录，正在查询与「%s」最近的 %d 条 (scheme=%s) ..." % (len(records), args.place, args.top, args.scheme))
    results = search_nearest(args.place, top=args.top, scheme=args.scheme, records=records)
    if not results:
        print("未找到结果（可能地名无法解析坐标或无匹配记录）")
        return 1
    for i, (r, dist) in enumerate(results, 1):
        addr = r.get("_address", "")
        print("  %d. [%.2f km] %s" % (i, dist, addr))
        # 可选：打印关键列
        for k in ["省份", "断面名称", "水质类别", "监测时间"]:
            if k in r and pd.notna(r[k]):
                print("      %s: %s" % (k, r[k]))
    # 非 offline 时可顺带更新缓存
    if args.scheme != "offline":
        cache = load_json_cache()
        for r, _ in results:
            addr = r.get("_address", "")
            if addr and addr not in cache:
                coord = geocode(args.scheme, addr, cache)
                if coord:
                    cache[addr] = coord
        save_json_cache(cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
