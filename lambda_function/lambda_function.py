from slack_bolt import App
from slack_bolt.adapter.aws_lambda import SlackRequestHandler
import os
import re

# アプリの初期化
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
    process_before_response=True,
)

# Slackイベントハンドラー：Slackアプリがメンションされた時
@app.event("app_mention")
def handle_app_mention_events(event, say):
    input_text = re.sub("<@.+>", "", event["text"]).strip()
    say(input_text)

# Lambdaイベントハンドラー
def lambda_handler(event, context):
    slack_handler = SlackRequestHandler(app=app)
    return slack_handler.handle(event, context)
