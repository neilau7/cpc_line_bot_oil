import googlemaps
import configparser

# 初始化 Google Maps client
config = configparser.ConfigParser()
config.read("config.ini")
googlemap_api_key = config["GoogleMapAPI"]["KEY"]
gmaps = googlemaps.Client(key=googlemap_api_key)

def find_gas_stations(keyword: str, radius_km: float = 5.0) -> str:
    """
    查詢指定地點附近加油站
    :param keyword: 地名或地址，例如 "台北車站"
    :param radius_km: 搜尋範圍，單位公里，預設 5 公里
    :return: 字串，包含每個加油站名稱、地址、營業狀態、是否有咖啡/便利店
    """
    # 1. 將使用者輸入轉成經緯度
    geocode_result = gmaps.geocode(keyword, language="zh-TW")
    if not geocode_result:
        return f"找不到地點：{keyword}"

    location = geocode_result[0]['geometry']['location']
    latlng = (location['lat'], location['lng'])

    # 2. 搜尋附近加油站
    radius_m = int(radius_km * 1000)  # 公尺
    places_result = gmaps.places_nearby(
        location=latlng,
        radius=radius_m,
        type="gas_station",
        language="zh-TW"
    )

    if not places_result.get('results'):
        return f"{keyword} 附近沒有找到加油站"

    # 3. 整理結果為字串
    lines = []
    for place in places_result['results']:
        name = place['name']
        address = place.get('vicinity', "無地址")
        opening_hours = place.get('opening_hours', {}).get('open_now')
        is_open = "營業中" if opening_hours else "休息中"
        types = place.get('types', [])
        has_coffee = "cafe" in types or "convenience_store" in types
        lines.append(f"{name} | {address} | {is_open} | 有咖啡/便利店: {has_coffee}")

    return "\n".join(lines)


print( find_gas_stations("台北車站", 5))