resource "aws_cloudwatch_log_group" "slack_ai_chatbot" {
  name              = "/aws/lambda/${aws_lambda_function.slack_ai_chatbot.function_name}"
  retention_in_days = 1
}

resource "aws_cloudwatch_log_group" "bedrock_backend" {
  name              = "/aws/lambda/${aws_lambda_function.slack_bolt_app_bedrock_backend.function_name}"
  retention_in_days = 1
}
