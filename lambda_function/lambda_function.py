from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
import boto3
import json
import os
import re

bedrock_region = os.environ.get("BEDROCK_REGION")

bedrock_runtime = boto3.client("bedrock-runtime", region_name=bedrock_region)

# アプリの初期化
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

# Bedrockを使って応答テキストを生成する
def generate_answer(input_text):
    # メッセージをセット
    messages = [
        {
            "role": "user",
            "content": input_text,
        },
    ]

    # リクエストBODYをセット
    request_body = json.dumps({
        "messages": messages,
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
    })

    # Bedrock APIを呼び出す
    response = bedrock_runtime.invoke_model(
        modelId="anthropic.claude-3-haiku-20240307-v1:0",
        accept="application/json",
        contentType="application/json",
        body=request_body,
    )

    # レスポンスBODYから応答テキストを取り出す
    response_body = json.loads(response.get("body").read())
    output_text = response_body.get("content")[0].get("text")

    # 応答テキストを戻り値として返す
    return output_text

# Slackイベントハンドラー：Slackアプリがメンションされた時
@app.event("app_mention")
def handle_app_mention_events(event, say):
    input_text = re.sub("<@.+>", "", event["text"]).strip()
    output_text = generate_answer(input_text)
    say(output_text)

# Lambdaイベントハンドラー
def lambda_handler(event, context):
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)