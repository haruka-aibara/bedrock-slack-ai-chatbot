resource "aws_lambda_layer_version" "slack_bolt" {
  filename                 = data.archive_file.lambda_layer_zip.output_path
  layer_name               = var.app_name
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "lambda layer for ${var.app_name}"
}

# Bedrock Backend
resource "aws_lambda_function" "slack_bolt_app_bedrock_backend" {
  filename         = data.archive_file.lambda_bedrock_code.output_path
  function_name    = "${var.app_name}_bedrock-backend"
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_bedrock_code.output_base64sha256
  role             = aws_iam_role.slack_bolt_app_bedrock_backend_role.arn
  layers           = [aws_lambda_layer_version.slack_bolt.arn]
  timeout          = 30

  environment {
    variables = {
      SLACK_BOT_TOKEN = var.slack_bot_token
      REGION          = data.aws_region.current.name
    }
  }
}

# Lambda 関数を SQS と統合
resource "aws_lambda_event_source_mapping" "mapping_for_sugoi_function" {
  event_source_arn = var.queue_arn
  function_name    = aws_lambda_function.slack_bolt_app_bedrock_backend.arn
}
