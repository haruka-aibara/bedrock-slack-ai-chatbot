# bedrock-slack-ai-chatbot
Amazon Bedrockを利用したSlack AIチャットボットアプリ

API Gateway を用いた HTTP API サーバーとして機能します

こちらを参考に作成させていただきました。
https://dev.classmethod.jp/articles/amazon-bedrock-slack-chat-bot-part2/

このプロジェクトを Terraform Apply すると、下記のリソースが作成されます。
 - Lambda 関数
 - Lambda レイヤー
 - API Gateway

variables の SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET はご自身の Slack App の値を入れて使用します。

@app_name とメンションすれば、回答が返ってきます。
![image](https://github.com/user-attachments/assets/e81fcbce-7f46-4b91-9257-50a5902e3bb8)
