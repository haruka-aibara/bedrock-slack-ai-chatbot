# 現在の AWS アカウント情報を取得するデータソース
data "aws_caller_identity" "current" {}

# 現在の AWS リージョン情報を取得するデータソース
data "aws_region" "current" {}

# Lambda Layer用のzipファイルのアーカイブを作成
data "archive_file" "lambda_layer_zip" {
  type        = "zip"
  source_dir  = "./lambda_layer_slack_ai_chatbot"
  output_path = "./lambda_layer_slack_ai_chatbot/lambda_layer.zip"
}

# Lambda関数のコードをzip化
data "archive_file" "lambda_code" {
  type        = "zip"
  source_dir  = "./lambda_function_slack_ai_chatbot"
  output_path = "./lambda_function_slack_ai_chatbot/lambda_function.zip"
}
