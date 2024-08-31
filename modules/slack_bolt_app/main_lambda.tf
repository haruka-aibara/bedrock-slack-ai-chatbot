resource "aws_lambda_layer_version" "slack_bolt" {
  filename                 = data.archive_file.lambda_layer_zip.output_path
  layer_name               = var.app_name
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "lambda layer for ${var.app_name}"
}

resource "aws_lambda_function" "slack_bolt_app" {
  filename         = data.archive_file.lambda_code.output_path
  function_name    = var.app_name
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_code.output_base64sha256
  role             = aws_iam_role.slack_bolt_app_role.arn
  layers           = [aws_lambda_layer_version.slack_bolt.arn]
  timeout          = 3

  environment {
    variables = {
      SLACK_BOT_TOKEN      = var.slack_bot_token
      SLACK_SIGNING_SECRET = var.slack_signing_secret
      BACKEND_QUEUE        = "${var.app_name}-queue"
      ACCOUNT_ID           = data.aws_caller_identity.current.account_id
      REGION               = data.aws_region.current.name
    }
  }
}
