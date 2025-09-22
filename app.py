import sys
import configparser
import requests, json
import os
import logging
from datetime import datetime

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

# Azure OpenAI Key
client = AzureOpenAI(
    api_key=config["AzureOpenAI"]["KEY"],
    api_version=config["AzureOpenAI"]["VERSION"],
    azure_endpoint=config["AzureOpenAI"]["BASE"],
)

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
                "content": "你是一位專業加油員，你可以協助使用者完成一筆加油交易，一筆交易包含：加油站點、油品、金額或公升數、付款方式等資訊。請一律用繁體中文來回答。"
            }
        ]

    # 加入使用者輸入
    conversation_history[user_id].append({"role": "user", "content": user_input})

    with ApiClient(configuration) as api_client:
        isFunctionCall, response, oil, amt, liter, pay = azure_openai(user_id)

        this_messages = []
        if isFunctionCall:
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
    global conversation_history
    messages = conversation_history[user_id]

    functions = [
        {
            "name": "save_user_info",
            "description": "Save user info to database，including: 油品、金額或公升數、付款方式，最後回傳交易資料(島號、槍號、時間、交易金額)與成功與否",
            "parameters": {
                "type": "object",
                "properties": {
                    "oil": {"type": "string", "description": "油品,例如：九五無鉛、九八無鉛、柴油等"},
                    "amt": {"type": "string", "description": "交易金額"},
                    "liter": {"type": "string", "description": "公升數"},
                    "pay": {"type": "string", "description": "付款方式,例如：信用卡、條碼支付、現金等．若無法取得，請填寫N/A"},
                },
                "required": ["oil", "pay"],
            },
        }
    ]

    completion = client.chat.completions.create(
        model=config["AzureOpenAI"]["DEPLOYMENT_NAME"],
        messages=messages,
        functions=functions,
        max_tokens=800,
        top_p=0.95,
        frequency_penalty=0,
        presence_penalty=0,
        stop=None,
    )

    completion_message = completion.choices[0].message

    # 把 AI 回覆存到對話
    if completion_message.content:
        conversation_history[user_id].append({"role": "assistant", "content": completion_message.content})

    if completion.choices[0].finish_reason == "function_call":
        this_arguments = json.loads(completion_message.function_call.arguments)
        function_name = completion_message.function_call.name
        if function_name == "save_user_info":
            oil = this_arguments["oil"]
            amt = this_arguments["amt"] if "amt" in this_arguments else "N/A"
            liter = this_arguments["liter"] if "liter" in this_arguments else "N/A"
            pay = this_arguments["pay"]
            return True, "need to call funcation", oil, amt, liter, pay
        else:
            return False, "function name error", "unknown", "unknown", "unknown", "unknown"
    else:
        return False, completion_message.content, "unknown", "unknown", "unknown", "unknown"

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

# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    app.run(port=5000, debug=True)
