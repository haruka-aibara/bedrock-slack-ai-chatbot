resource "aws_apigatewayv2_api" "slack_ai_chatbot" {
  name          = "${local.project_name}-api-gateway"
  protocol_type = "HTTP"
  description   = "HTTP API for ${local.project_name}"
}

resource "aws_apigatewayv2_integration" "slack_ai_chatbot" {
  api_id                 = aws_apigatewayv2_api.slack_ai_chatbot.id
  integration_uri        = aws_lambda_function.slack_ai_chatbot.arn
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "slack_ai_chatbot" {
  api_id    = aws_apigatewayv2_api.slack_ai_chatbot.id
  route_key = "ANY /slack/events"
  target    = "integrations/${aws_apigatewayv2_integration.slack_ai_chatbot.id}"
}

resource "aws_apigatewayv2_stage" "slack_ai_chatbot" {
  api_id      = aws_apigatewayv2_api.slack_ai_chatbot.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "slack_ai_chatbot" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.slack_ai_chatbot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.slack_ai_chatbot.execution_arn}/*/*/slack/events"
}
