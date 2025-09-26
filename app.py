import sys
import configparser
import requests, json
import os
import logging
from datetime import datetime

import googlemaps

from matplotlib import rcParams

# 指定中文字體，例如蘋果系統常用的 Heiti
rcParams['font.family'] = 'AppleGothic'

# Azure OpenAI
from openai import AzureOpenAI

# Flask
from flask import Flask, request, abort

# LINE Bot SDK v3
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    ImageMessage,
)

from linebot.models import FollowEvent

# ----------------------------
# Config Parser
# ----------------------------
config = configparser.ConfigParser()
config.read("config.ini")
sever_url = config["Server"]["URL"] # external url


# Azure OpenAI Key
client = AzureOpenAI(
    api_key=config["AzureOpenAI"]["KEY"],
    api_version=config["AzureOpenAI"]["VERSION"],
    azure_endpoint=config["AzureOpenAI"]["BASE"],
)
# weather API Key
weather_api_key = config["WeatherAPI"]["KEY"]

# Google Maps API Key
googlemap_api_key = config["GoogleMapAPI"]["KEY"]

# News API Key
news_api_key = config["NewsAPI"]["KEY"]
# 初始化 client
gmaps = googlemaps.Client(key=googlemap_api_key)

# Flask Web Server
app = Flask(__name__)

# ----------------------------
# Logging (避免和 LINE handler 衝突)
# ----------------------------
log_handler = logging.FileHandler("app.log", encoding="utf-8")
log_handler.setLevel(logging.INFO)
app.logger.addHandler(log_handler)

# ----------------------------
# LINE Bot Config
# ----------------------------
channel_access_token = config["Line"]["CHANNEL_ACCESS_TOKEN"]
channel_secret = config["Line"]["CHANNEL_SECRET"]
if channel_secret is None:
    print("Specify LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)
if channel_access_token is None:
    print("Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.")
    sys.exit(1)

handler = WebhookHandler(channel_secret)
configuration = Configuration(access_token=channel_access_token)

# ----------------------------
# 全域變數
# ----------------------------
conversation_history = {}   # 每個使用者的對話紀錄

## image route
@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory("static", filename)

# ----------------------------
# Callback
# ----------------------------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# ----------------------------
# Follow Event
# ----------------------------
@handler.add(FollowEvent)
def handle_follow(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[
                    TextMessage(
                        text="感謝您將本機器人加入好友，歡迎使用！\n您可以開始加油！"
                    )
                ],
            )
        )

# ----------------------------
# Message Event
# ----------------------------
@handler.add(MessageEvent, message=TextMessageContent)
def message_text(event):
    user_id = event.source.user_id
    user_input = event.message.text

    global conversation_history

    # 初始化對話
    if user_id not in conversation_history:
        conversation_history[user_id] = [
            {
                "role": "system",
                "content": "你是一位專業加油員，你可以協助使用者完成一筆加油交易，一筆交易包含：加油站點、油品、金額或公升數、付款方式等資訊。請一律用繁體中文來回答。如果使用者只是查詢油價，請只回覆油價資訊，不要主動執行加油交易。"
            }
        ]

    # 加入使用者輸入
    conversation_history[user_id].append({"role": "user", "content": user_input})

    with ApiClient(configuration) as api_client:
        isFunctionCall, function_name, response, oil, amt, liter, pay = azure_openai(user_id)

        this_messages = []
        if isFunctionCall:
            if function_name == "get_price":
                this_messages.append(TextMessage(text="目前油品牌價如下：\n" + response))
            elif function_name == "save_user_info":
            
                this_messages.append(TextMessage(text="你想要做的交易是：" + oil + "，金額：" + amt + "，公升數：" + liter + "，付款方式：" + pay))
                success, island, gun, time = saveTran(oil, amt, liter, pay)
                if success:
                    this_messages.append(TextMessage(text=f"交易成功！\n您加注的油品為 {oil} \n付款方式為 {pay}"))
                    if amt != "N/A":
                        this_messages.append(TextMessage(text="交易金額：" + amt + " 元"))
                    if liter != "N/A":
                        this_messages.append(TextMessage(text="公升數：" + liter + " 公升"))
                else:
                    this_messages.append(TextMessage(text="交易失敗，請重新嘗試！"))
            elif function_name == "get_weather":
                if "error" not in response:
                    weather_info = "\n".join([f"{key}：{value}" for key, value in response.items()])
                    this_messages.append(TextMessage(text="目前天氣狀況如下：\n" + weather_info))
                else:
                    this_messages.append(TextMessage(text="查詢天氣失敗，原因：" + response["error"]))
            elif function_name == "get_news":
                if isinstance(response, list) and len(response) > 0:
                    news_list = "\n\n".join([f"📰 {item['title']}\n{item['url']}" for item in response])
                    this_messages.append(TextMessage(text="以下是最新新聞：\n" + news_list))
                else:
                    this_messages.append(TextMessage(text="查無相關新聞，請換個關鍵字！"))
            else:
                this_messages.append(TextMessage(text="發生錯誤，請重新嘗試！"))
            
        else:
            this_messages.append(TextMessage(text=response))

        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=this_messages,
            )
        )

# ----------------------------
# Azure OpenAI Function
# ----------------------------
def azure_openai(user_id):
    """
    支援多步 function call：
    1. 先執行 function
    2. 把結果加入 conversation_history
    3. 再呼叫 OpenAI，讓 AI 根據結果決定下一步
    """
    import json
    import urllib.parse
    global conversation_history
    messages = conversation_history[user_id]

    functions = [
        {
            "name": "save_user_info",
            "description": "Save user info to database，包含油品、金額、公升數、付款方式",
            "parameters": {
                "type": "object",
                "properties": {
                    "oil": {"type": "string"},
                    "amt": {"type": "string"},
                    "liter": {"type": "string"},
                    "pay": {"type": "string"}
                },
                "required": ["oil", "pay"]
            }
        },
        {
            "name": "get_price",
            "description": "取得油品牌價",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string"},
                    "all_results": {"type": "boolean"}
                },
                "required": ["all_results"]
            }
        },
        {
            "name": "get_weather",
            "description": "查詢即時天氣",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "days": {"type": "integer"}
                },
                "required": ["city"]
            }
        },
        {
            "name": "get_news",
            "description": "查詢新聞，依指定關鍵字回傳最新新聞列表",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "limit": {"type": "integer"}
                },
                "required": []
            }
        },
        {
            "name": "find_gas_stations",
            "description": "查詢指定地點附近加油站",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "radius_km": {"type": "number"}
                },
                "required": ["keyword"]
            }
        },
        {
            "name": "get_gas_station_link",
            "description": "生成加油站 Google Maps 導航連結",
            "parameters": {
                "type": "object",
                "properties": {
                    "station_name": {"type": "string"}
                },
                "required": ["station_name"]
            }
        }
    ]

    # 初始化第一次呼叫
    completion = client.chat.completions.create(
        model=config["AzureOpenAI"]["DEPLOYMENT_NAME"],
        messages=messages,
        functions=functions,
        max_tokens=1500,
        top_p=0.95,
        frequency_penalty=0,
        presence_penalty=0
    )

    completion_message = completion.choices[0].message
    function_name = getattr(completion_message.function_call, "name", None)

    # 如果 AI 回覆內容直接在 content 中
    if completion_message.content:
        conversation_history[user_id].append({"role": "assistant", "content": completion_message.content})

    # 多步 function call
    while getattr(completion_message, "function_call", None):
        this_arguments = json.loads(completion_message.function_call.arguments or "{}")

        # -------------------------
        # get_weather
        # -------------------------
        if function_name == "get_weather":
            city = this_arguments["city"]
            days = this_arguments.get("days", 0)
            weather_info = get_weather(city, days)
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": json.dumps(weather_info, ensure_ascii=False)
            })
            # 準備回覆文字
            if days == 0:
                text = (
                    f"{weather_info['地點']} 現在天氣：{weather_info['天氣']}\n"
                    f"氣溫 {weather_info['氣溫(°C)']}°C，體感 {weather_info['體感溫度(°C)']}°C\n"
                    f"濕度 {weather_info['濕度(%)']}%，風速 {weather_info['風速(kph)']} kph\n"
                    f"降雨量 {weather_info.get('降雨量(mm)', 0)} mm"
                )
            else:
                lines = [f"{weather_info['地點']} 未來 {days} 天預報："]
                for day in weather_info["預報"]:
                    lines.append(
                        f"- {day['日期']}: {day['天氣']}, 最高 {day['最高氣溫(°C)']}°C, "
                        f"最低 {day['最低氣溫(°C)']}°C, 降雨機率 {day.get('降雨機率(%)', 0)}%"
                    )
                text = "\n".join(lines)
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": text
            })

        # -------------------------
        # get_price
        # -------------------------
        elif function_name == "get_price":
            product_name = this_arguments.get("product_name")
            all_results = this_arguments.get("all_results", True)
            price_info = getPrice(product_name, all_results)
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": price_info
            })

        # -------------------------
        # save_user_info
        # -------------------------
        elif function_name == "save_user_info":
            oil = this_arguments["oil"]
            amt = this_arguments.get("amt", "N/A")
            liter = this_arguments.get("liter", "N/A")
            pay = this_arguments["pay"]
            success, island, gun, time = saveTran(oil, amt, liter, pay)
            result = {
                "success": success,
                "oil": oil,
                "amt": amt,
                "liter": liter,
                "pay": pay,
                "island": island,
                "gun": gun,
                "time": time
            }
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": json.dumps(result, ensure_ascii=False)
            })

        # -------------------------
        # find_gas_stations
        # -------------------------
        elif function_name == "find_gas_stations":
            keyword = this_arguments["keyword"]
            radius_km = this_arguments.get("radius_km", 5)
            gas_station_info = find_gas_stations(keyword, radius_km)
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": gas_station_info
            })

        # -------------------------
        # get_gas_station_link
        # -------------------------
        elif function_name == "get_gas_station_link":
            station_name = this_arguments["station_name"]
            link = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(station_name)}"
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": link
            })

        # -------------------------
        # get_news
        # -------------------------
        elif function_name == "get_news":
            keyword = this_arguments.get("keyword", "中油")
            news_result = get_news(keyword)  # 回傳 dict
            news_list = news_result.get("新聞列表", [])  # 取出列表
            if news_list:
                news_info = "\n\n".join([f"📰 {item['標題']}\n{item['連結']}" for item in news_list])
            else:
                news_info = f"查無關鍵字 '{keyword}' 的新聞"
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": news_info
            })

        else:
            conversation_history[user_id].append({
                "role": "function",
                "name": function_name,
                "content": "function name error"
            })

        # 呼叫 AI 決定下一步
        completion = client.chat.completions.create(
            model=config["AzureOpenAI"]["DEPLOYMENT_NAME"],
            messages=conversation_history[user_id],
            functions=functions,
            max_tokens=800,
            top_p=0.95,
            frequency_penalty=0,
            presence_penalty=0
        )
        completion_message = completion.choices[0].message
        function_name = getattr(completion_message.function_call, "name", None)
        if completion_message.content:
            conversation_history[user_id].append({"role": "assistant", "content": completion_message.content})

    # -------------------------
    # 最終回傳
    # -------------------------
    for msg in reversed(conversation_history[user_id]):
        if msg["role"] == "function" and "oil" in msg.get("content", ""):
            try:
                data = json.loads(msg["content"])
                return True, "save_user_info", "交易完成", data["oil"], data["amt"], data["liter"], data["pay"]
            except:
                return True, "save_user_info", msg["content"], "N/A", "N/A", "N/A", "N/A"

    if not function_name:
        return False, "unknown", completion_message.content, "unknown", "unknown", "unknown", "unknown"
    return True, function_name, completion_message.content, "N/A", "N/A", "N/A", "N/A"

# ----------------------------
# 模擬交易 (假的 DB 存取)
# ----------------------------
def saveTran(oil, amt, liter, pay):
    island = "1"
    gun = "2"
    now = datetime.now()
    time = now.strftime("%Y/%m/%d %H:%M:%S")
    success = True

    if oil == "N/A" or pay == "N/A":
        success = False
    if amt == "N/A" and liter == "N/A":
        success = False

    return success, island, gun, time

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
        if not has_coffee:
            if "咖啡" in name:
                has_coffee = True
        lines.append(f"{name} | {address} | {is_open} | 有咖啡/便利店: {has_coffee}")

    return "\n".join(lines)

def get_gas_station_link(station_name: str) -> str:
    """
    生成 Google Maps 導航連結
    :param station_name: 加油站名稱或地址
    :return: 可點擊的導航網址
    """
    # URL 編碼
    import urllib.parse
    query = urllib.parse.quote(station_name)
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def getPrice(product_name=None, all_results=True):

    print("getPrice called with:", product_name, all_results)
    import xml.etree.ElementTree as ET
    import urllib3
    import requests

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = "https://vipmbr.cpc.com.tw/CPCSTN/ListPriceWebService.asmx/getCPCMainProdListPrice_XML"
    res = requests.get(url, verify=False)
    root = ET.fromstring(res.text)

    prices = []
    for table in root.findall("Table"):
        product = table.find("產品名稱").text
        price = table.find("參考牌價_金額").text
        date = table.find("牌價生效日期").text

        if all_results:  # 🔹 全部
            prices.append(f"{product}: {price} 元 (生效日 {date})")
        elif product_name and product_name in product:  # 🔹 單一
            return f"{product}: {price} 元 (生效日 {date})"

    if not all_results and product_name:
        return f"查無 {product_name} 的油價資訊"

    return "\n".join(prices)

def get_weather(city: str, days: int = 0) -> dict:
    """
    使用 WeatherAPI 查詢指定台灣地區天氣，並抓降雨資訊
    
    :param city: 城市名稱，例如 "Taipei", "Kaohsiung", "Tainan", "Taichung"
    :param days: 查詢天數，0 表示即時天氣，1-7 表示未來天氣（含今天）
    :return: dict 格式
        - days=0 回傳即時天氣（含降雨量 mm）
        - days>0 回傳未來天氣列表（含降雨機率 %）
    """
    import requests

    days = min(days, 7)  # 限制最多 7 天

    # 設定 API URL
    if days == 0:
        url = f"http://api.weatherapi.com/v1/current.json?key={weather_api_key}&q={city},Taiwan&lang=zh"
    else:
        url = f"http://api.weatherapi.com/v1/forecast.json?key={weather_api_key}&q={city},Taiwan&days={days}&lang=zh"

    response = requests.get(url)
    if response.status_code != 200:
        return {"error": f"查詢失敗，狀態碼 {response.status_code}"}

    data = response.json()
    if "error" in data:
        return {"error": data["error"]["message"]}

    # 即時天氣
    if days == 0:
        result = {
            "地點": data["location"]["name"],
            "時間": data["location"]["localtime"],
            "氣溫(°C)": data["current"]["temp_c"],
            "體感溫度(°C)": data["current"]["feelslike_c"],
            "天氣": data["current"]["condition"]["text"],
            "濕度(%)": data["current"]["humidity"],
            "風速(kph)": data["current"]["wind_kph"],
            "降雨量(mm)": data["current"].get("precip_mm", 0)
        }
    # 未來天氣
    else:
        forecast_list = []
        for day in data["forecast"]["forecastday"]:
            forecast_list.append({
                "日期": day["date"],
                "平均氣溫(°C)": day["day"]["avgtemp_c"],
                "最高氣溫(°C)": day["day"]["maxtemp_c"],
                "最低氣溫(°C)": day["day"]["mintemp_c"],
                "天氣": day["day"]["condition"]["text"],
                "降雨機率(%)": day["day"].get("daily_chance_of_rain", 0),
                "平均濕度(%)": day["day"]["avghumidity"]
            })
        result = {
            "地點": data["location"]["name"],
            "預報": forecast_list
        }

    return result

import matplotlib
matplotlib.use("Agg")  # 非 GUI，純粹生成圖片檔案
import matplotlib.pyplot as plt
import os
import requests

def get_weather_chart(city: str, days: int = 7, show: str = "weather") -> dict:
    """
    取得未來 N 天天氣，生成折線圖
    :param city: 城市名稱
    :param days: 1~7 天
    :param show: 'weather' 或 'rain'，決定圖表 y 軸顯示氣溫或降雨機率
    :return: dict，包含天氣資料與圖檔路徑
    """
    import requests, matplotlib.pyplot as plt, os

    url = f"http://api.weatherapi.com/v1/forecast.json?key={weather_api_key}&q={city},Taiwan&days={days}&lang=zh"
    resp = requests.get(url)
    data = resp.json()
    if resp.status_code != 200 or "error" in data:
        return {"error": data.get("error", {}).get("message", "查詢失敗")}

    forecast_list = []
    dates, y_values = [], []

    for day in data["forecast"]["forecastday"]:
        d = day["date"]
        dates.append(d)
        if show == "rain":
            y = day["day"].get("daily_chance_of_rain", 0)
        else:
            y = day["day"]["avgtemp_c"]
        y_values.append(y)

        forecast_list.append({
            "日期": d,
            "平均氣溫(°C)": day["day"]["avgtemp_c"],
            "最高氣溫(°C)": day["day"]["maxtemp_c"],
            "最低氣溫(°C)": day["day"]["mintemp_c"],
            "天氣": day["day"]["condition"]["text"],
            "降雨機率(%)": day["day"].get("daily_chance_of_rain", 0),
            "平均濕度(%)": day["day"]["avghumidity"]
        })

    # 產生圖表
    plt.figure(figsize=(10,6))
    plt.plot(dates, y_values, marker='o', label=("降雨機率(%)" if show=="rain" else "平均氣溫(°C)"))
    plt.title(f"{city} 未來 {days} 天{'降雨機率' if show=='rain' else '氣溫'}預報")
    plt.xlabel("日期")
    plt.ylabel("值")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()

    os.makedirs("static", exist_ok=True)
    chart_file = f"static/{show.lower()}_{city.lower()}.png"
    plt.savefig(chart_file)
    plt.close()

    return {
        "地點": city,
        "預報": forecast_list,
        "chart_path": chart_file
    }


def get_news(keyword: str = "中油", limit: int = 5) -> dict:
    """
    使用 NewsAPI 查詢指定關鍵字的新聞
    """
    import requests
    from datetime import datetime

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": keyword,
        "language": "zh",
        "sortBy": "publishedAt",
        "pageSize": limit,
        "apiKey": news_api_key
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        return {"關鍵字": keyword, "新聞列表": [], "error": str(e)}

    news_list = []
    for article in data.get("articles", []):
        # 將時間轉成簡單格式
        try:
            pub_time = datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00"))
            pub_time_str = pub_time.strftime("%Y-%m-%d %H:%M")
        except:
            pub_time_str = article["publishedAt"]

        news_list.append({
            "標題": article["title"],
            "來源": article["source"]["name"],
            "時間": pub_time_str,
            "連結": article["url"]
        })

    return {
        "關鍵字": keyword,
        "新聞列表": news_list
    }




# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    app.run(port=5005, debug=True)
