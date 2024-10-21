import json
import logging
import os

import boto3
from slack_sdk import WebClient

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_runtime = boto3.client("bedrock-runtime")

client = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))


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
    request_body = json.dumps(
        {
            "messages": messages,
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
        }
    )

    # Bedrock APIを呼び出す
    response = bedrock_runtime.invoke_model(
        modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
        accept="application/json",
        contentType="application/json",
        body=request_body,
    )

    # レスポンスBODYから応答テキストを取り出す
    response_body = json.loads(response.get("body").read())
    output_text = response_body.get("content")[0].get("text")

    # 応答テキストを戻り値として返す
    return output_text


# Lambdaイベントハンドラー
def lambda_handler(event, context):
    # SQSキューから情報を取り出す
    body = json.loads(event["Records"][0]["body"])
    channel_id = body.get("channel_id")
    input_text = body.get("input_text")

    # 入力テキストが空でないかチェック
    if not input_text or input_text.strip() == "":
        error_message = "入力テキストが空です。有効なテキストを入力してください。"
        client.chat_postMessage(
            channel=channel_id,
            text=error_message,
        )
        return {"statusCode": 400, "body": json.dumps("Empty input text")}

    # Bedrockを呼び出して入力テキストに対する応答テキストを生成する
    output_text = generate_answer(input_text)

    # Slackへ応答テキストを書き込む
    _ = client.chat_postMessage(
        channel=channel_id,
        text=output_text,
    )

    return {"statusCode": 200, "body": json.dumps("Message sent successfully")}
