import sys
import configparser
import requests, json

# Azure OpenAI
import os
from openai import AzureOpenAI

from flask import Flask, request, abort
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
import logging

from linebot.models import FollowEvent
from linebot.models import TextSendMessage

from datetime import datetime


# Config Parser
config = configparser.ConfigParser()
config.read("config.ini")

# Azure OpenAI Key
client = AzureOpenAI(
    api_key=config["AzureOpenAI"]["KEY"],
    api_version=config["AzureOpenAI"]["VERSION"],
    azure_endpoint=config["AzureOpenAI"]["BASE"],
)

# Logging position
handler = logging.FileHandler('app.log', encoding='utf-8')
handler.setLevel(logging.INFO)

# Flask Web Server
app = Flask(__name__)


app.logger.addHandler(handler)

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


@app.route("/callback", methods=["POST"])
def callback():
    # get X-Line-Signature header value
    signature = request.headers["X-Line-Signature"] #這是 LINE 為了驗證請求來源而附帶的 簽章 (signature)，避免有人偽造請求。
    # get request body as text
    body = request.get_data(as_text=True) #取得請求的主體內容，這是 LINE 傳送過來的事件資料，通常是 JSON 格式的字串。
    app.logger.info("Request body: " + body) #將請求的主體內容記錄到應用程式的日誌中，方便除錯和追蹤請求。

    # parse webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(FollowEvent) #當用戶新增機器人為好友時觸發
def handle_follow(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client) #line bot api response
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




@handler.add(MessageEvent, message=TextMessageContent) #這是一個 裝飾器 (decorator)，用來告訴 handler：「當收到特定事件時，請執行下面這個函數」。
def message_text(event):
    with ApiClient(configuration) as api_client:
        #isFunctionCall, response, movie_title, movie_target = azure_openai( # 確認是否要呼叫函數
        #    event.message.text
        #)
        isFunctionCall, response, oil, amt, liter, pay = azure_openai( # 確認是否要呼叫函數
            event.message.text
        )

        this_messages = []
        if isFunctionCall:
            
            this_messages.append(TextMessage(text="你想要做的交易是：" + oil + "，金額：" + amt + "，公升數：" + liter + "，付款方式：" + pay))
            
            #movie_title, movie_result = call_tmdb(movie_title, movie_target)
            success, island, gun, time = saveTran(oil, amt, liter, pay)
            if success:
                this_messages.append(TextMessage(text=f"交易成功！\n您加注的油品為 {oil} \n付款方式為 {pay}"))
                #this_messages.append(TextMessage(text="島號：" + island))
                #this_messages.append(TextMessage(text="槍號：" + gun))
                #this_messages.append(TextMessage(text="時間：" + time))
                if amt != "N/A":
                    this_messages.append(TextMessage(text="交易金額：" + amt + " 元"))
                if liter != "N/A":
                    this_messages.append(TextMessage(text="公升數：" + liter + " 公升"))
            else:
                this_messages.append(TextMessage(text="交易失敗，請重新嘗試！"))
      
        else:
            this_messages.append(TextMessage(text=response))
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=this_messages,
            )
        )

def azure_openai(user_input):
    message_text = [
        {
            "role": "system",
            "content": "",
        },
        {"role": "user", "content": user_input},
    ]

    functions = [
        {
            "name": "save_user_info",
            "description": "Save user info to database，including: 油品、金額或公升數、付款方式，最後回傳交易資料(島號、槍號、時間、交易金額)與成功與否",
            "parameters": {
                "type": "object",
                "properties": {
                    "oil": {"type": "string", "description": "油品,例如：九五無鉛、九八無鉛、柴油等"},
                    "amt": {
                        "type": "string",
                        "description": "交易金額",
                    },
                    "liter": {
                        "type": "string",
                        "description": "公升數",
                    },
                    "pay": {"type": "string", "description": "付款方式,例如：信用卡、條碼支付、現金等．若無法取得，請填寫N/A"},
                },
                "required": ["oil", "pay" ],
            },
        }
    ]

    message_text[0]["content"] += "你是一位專業加油員，你可以協助使用者完成一筆加油交易，一筆交易包含：加油站點、油品、金額或公升數、付款方式等資訊。"
    message_text[0]["content"] += "請一律用繁體中文來回答函數要傳入的數值。"

    completion = client.chat.completions.create(
        model=config["AzureOpenAI"]["DEPLOYMENT_NAME"],
        messages=message_text, #是 一個訊息列表 (list of messages)，用來提供給 Azure OpenAI 的 chat model 作為上下文。
        functions=functions,
        max_tokens=800,
        top_p=0.95,
        frequency_penalty=0,
        presence_penalty=0,
        stop=None,
    )
    print(completion)
    completion_message = completion.choices[0].message
    if completion.choices[0].finish_reason == "function_call":
        this_arguments = json.loads(completion_message.function_call.arguments)
        print("[this_arguments]", this_arguments)
        function_name = completion_message.function_call.name
        oil = this_arguments["oil"]
        amt = this_arguments["amt"] if "amt" in this_arguments else "N/A" 
        liter = this_arguments["liter"] if "liter" in this_arguments else "N/A"
        pay = this_arguments["pay"]
        
        
        movie_target = (
            this_arguments["target"] if "target" in this_arguments else "no-target"
        )
        return True, "need to call funcation", oil, amt, liter, pay
    else:
        return False, completion_message.content, "unknown", "unknown", "unknown", "unknown"


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



def call_tmdb(movie_title, movie_target):
    print("[call_tmdb] in")
    print("movie_title : ", movie_title)
    print("movie_target : ", movie_target)
    baseUrl = "https://api.themoviedb.org/3/search/movie?"
    parameters = "language=" + "zh-TW"
    parameters += "&"
    parameters += "query=" + movie_title

    headers = {
        "accept": "application/json",
        "Authorization": "Bearer " + config["TMDB"]["KEY"],
    }

    response = requests.get(baseUrl + parameters, headers=headers)
    result = json.loads(response.text)
    print(result)
    if len(result["results"]) != 0:
        if movie_target == "overview":
            if "overview" in result["results"][0] and len(result["results"][0]["overview"])!=0:
                return TextMessage(text=result["results"][0]["title"]), TextMessage(text=result["results"][0]["overview"])
            else:
                return TextMessage(text=result["results"][0]["title"]), TextMessage(text="該電影沒有簡介")
        elif movie_target == "poster":
            if "poster_path" in result["results"][0]:
                imageUri = "https://image.tmdb.org/t/p/original" + result["results"][0]["poster_path"]
                return TextMessage(text=result["results"][0]["title"]), ImageMessage(
                    originalContentUrl=imageUri, previewImageUrl=imageUri
                )
            else:
                return TextMessage(text=result["results"][0]["title"]), TextMessage(text="該電影沒有海報")
        else:
            if "overview" in result["results"][0]:
                return TextMessage(text=result["results"][0]["title"]), TextMessage(text=result["results"][0]["overview"])
            else:
                return TextMessage(text=result["results"][0]["title"]), TextMessage(text="該電影沒有簡介")
    else:
        return TextMessage(text="N/A"), TextMessage(text="很抱歉，系統內並無相關電影")


if __name__ == "__main__":
    app.run()