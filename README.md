# bedrock-slack-ai-chatbot
Amazon Bedrockを利用したSlack AIチャットボットアプリ

API Gateway を用いた HTTP API サーバーとして機能します

こちらを参考に作成させていただきました。
https://dev.classmethod.jp/articles/amazon-bedrock-slack-chat-bot-part2/

## 構成図
![image](https://github.com/user-attachments/assets/dcab2590-68ab-4d53-a8cf-896108f6cd89)
引用元: https://dev.classmethod.jp/articles/amazon-bedrock-slack-chat-bot-part2/#toc-step-3-bedrock

このプロジェクトを Terraform Apply すると、構成図における「AWS Cloud」内のリソース・設定が作成されます。
 - API Gateway
 - Lambda レイヤー
 - Lambda 関数
 - Bedrock
 - SQS

variables の SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET はご自身の Slack App の値を入れて使用します。

@app_name とメンションすれば、回答が返ってきます。
![image](https://github.com/user-attachments/assets/e81fcbce-7f46-4b91-9257-50a5902e3bb8)
