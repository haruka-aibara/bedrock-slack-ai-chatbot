import json
import os
import re

import boto3
from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler

sqs = boto3.client("sqs")

aws_account_id = os.environ.get("ACCOUNT_ID")
region = os.environ.get("AWS_REGION")
queue = os.environ.get("BACKEND_QUEUE")

sqs_queue_url = f"https://sqs.{region}.amazonaws.com/{aws_account_id}/{queue}"

# アプリの初期化
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)


# Slackイベントハンドラー：Slackアプリがメンションされた時
@app.event("app_mention")
def handle_app_mention_events(event, say):
    _ = say(text="少々お待ちください...")

    channel_id = event["channel"]
    input_text = re.sub("<@.+>", "", event["text"]).strip()

    sqs.send_message(
        QueueUrl=sqs_queue_url,
        MessageBody=json.dumps(
            {
                "channel_id": channel_id,
                "input_text": input_text,
            }
        ),
    )


# Lambdaイベントハンドラー
def lambda_handler(event, context):
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
