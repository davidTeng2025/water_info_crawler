import requests
from geopy.distance import geodesic

# 配置你的高德地图 Key
AMAP_KEY = '88daf6da379071667d2a1cd2d8efb861'

def get_coordinates(address):
    """
    1. 地名转经纬度 (Geocoding)
    输入：中文地名（如：北京市天安门）
    输出：(纬度, 经度)
    """
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {
        "key": AMAP_KEY,
        "address": address,
        "city": ""  # 可选：指定城市可以提高准确度
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if data['status'] == '1' and data['geocodes']:
            # 高德返回的是 "经度,纬度"，我们需要转换成常用的 (纬度, 经度)
            location = data['geocodes'][0]['location']
            lon, lat = map(float, location.split(','))
            return (lat, lon)
        else:
            print(data)
            print(f"解析失败: {address}")
            return None
    except Exception as e:
        print(f"请求异常: {e}")
        return None

def find_nearest_location(target_coord, location_list):
    """
    2. 计算最近距离
    输入：目标坐标 (lat, lon), 坐标列表 [{'name': '地点A', 'coord': (lat, lon)}, ...]
    输出：距离最近的信息项及其距离
    """
    if not target_coord or not location_list:
        return None
    
    nearest_item = None
    min_distance = float('inf')
    
    for item in location_list:
        # 使用 geodesic 计算大圆距离（考虑地球曲率，比勾股定理精确）
        dist = geodesic(target_coord, item['coord']).kilometers
        
        if dist < min_distance:
            min_distance = dist
            nearest_item = item
            
    return {
        "nearest_info": nearest_item,
        "distance_km": round(min_distance, 3)
    }

# --- 测试用例 ---

# 1. 模拟地名转坐标
my_location_name = "湖北省牛山湖湖心"
my_coord = get_coordinates(my_location_name)
print(f"'{my_location_name}' 的坐标为: {my_coord}")

# 2. 模拟已知坐标列表
# places = [
#     {"name": "上海人民广场", "coord": (31.2317, 121.4726)},
#     {"name": "上海中心大厦", "coord": (31.2334, 121.5020)},
#     {"name": "静安寺", "coord": (31.2244, 121.4451)}
# ]

# # 3. 寻找最近的地点
# if my_coord:
#     result = find_nearest_location(my_coord, places)
#     print(f"\n距离 '{my_location_name}' 最近的是: {result['nearest_info']['name']}")
#     print(f"直线距离约为: {result['distance_km']} 公里")