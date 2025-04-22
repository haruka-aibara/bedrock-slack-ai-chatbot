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
      BACKEND_QUEUE_URL    = aws_sqs_queue.slack_ai_chatbot.url
    }
  }
}

resource "aws_lambda_layer_version" "bedrock" {
  filename                 = data.archive_file.lambda_bedrock_layer_zip.output_path
  layer_name               = "${local.project_name}_bedrock-backend"
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "lambda layer for ${local.project_name} bedrock backend"
}

resource "aws_lambda_function" "slack_bolt_app_bedrock_backend" {
  filename         = data.archive_file.lambda_bedrock_code.output_path
  function_name    = "${local.project_name}_bedrock-backend"
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_bedrock_code.output_base64sha256
  role             = aws_iam_role.bedrock_backend.arn
  layers           = [aws_lambda_layer_version.bedrock.arn]
  timeout          = 30

  environment {
    variables = {
      SLACK_BOT_TOKEN    = var.slack_bot_token
      BEDROCK_MODEL_ID   = var.bedrock_model_id
      BEDROCK_MAX_TOKENS = var.bedrock_max_tokens
    }
  }
}

resource "aws_lambda_event_source_mapping" "bedrock" {
  event_source_arn = aws_sqs_queue.slack_ai_chatbot.arn
  function_name    = aws_lambda_function.slack_bolt_app_bedrock_backend.arn
}
