resource "aws_lambda_layer_version" "slack_ai_chatbot" {
  filename                 = data.archive_file.lambda_layer_zip.output_path
  layer_name               = local.project_name
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "lambda layer for ${local.project_name}"
}

resource "aws_lambda_function" "slack_ai_chatbot" {
  filename         = data.archive_file.lambda_code.output_path
  function_name    = local.project_name
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_code.output_base64sha256
  role             = aws_iam_role.slack_ai_chatbot.arn
  layers           = [aws_lambda_layer_version.slack_ai_chatbot.arn]
  timeout          = 3

  environment {
    variables = {
      SLACK_BOT_TOKEN      = var.slack_bot_token
      SLACK_SIGNING_SECRET = var.slack_signing_secret
      BACKEND_QUEUE        = "${local.project_name}-queue"
      ACCOUNT_ID           = data.aws_caller_identity.current.account_id
      REGION               = data.aws_region.current.name
    }
  }
}

# ===================================================

resource "aws_lambda_layer_version" "bedrock" {
  filename                 = data.archive_file.lambda_bedrock_layer_zip.output_path
  layer_name               = local.project_name
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "lambda layer for ${local.project_name}"
}

# Bedrock Backend
resource "aws_lambda_function" "slack_bolt_app_bedrock_backend" {
  filename         = data.archive_file.lambda_bedrock_code.output_path
  function_name    = "${local.project_name}_bedrock-backend"
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_bedrock_code.output_base64sha256
  role             = aws_iam_role.slack_bolt_app_bedrock_backend_role.arn
  layers           = [aws_lambda_layer_version.slack_ai_chatbot.arn]
  timeout          = 30

  environment {
    variables = {
      SLACK_BOT_TOKEN = var.slack_bot_token
      REGION          = data.aws_region.current.name
    }
  }
}

# Lambda 関数を SQS と統合
resource "aws_lambda_event_source_mapping" "bedrock" {
  event_source_arn = aws_sqs_queue.slack_ai_chatbot.arn
  function_name    = aws_lambda_function.slack_bolt_app_bedrock_backend.arn
}
