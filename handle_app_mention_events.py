from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import os
import re

# アプリの初期化
app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# Slackイベントハンドラー：Slackアプリがメンションされた時
@app.event("app_mention")
def handle_app_mention_events(event, say):
    input_text = re.sub("<@.+>", "", event["text"]).strip()
    say(input_text)

# アプリを起動
if __name__ == "__main__":
    SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN")).start()