# HTTP API Gatewayの作成
resource "aws_apigatewayv2_api" "slack_bot_api" {
  name          = "${local.project_name}-api-gateway"
  protocol_type = "HTTP"
  description   = "HTTP API for ${local.project_name}"
}

# Lambda統合の設定
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.slack_bot_api.id
  integration_uri        = aws_lambda_function.slack_bolt_app.arn
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# ルートの設定
resource "aws_apigatewayv2_route" "slack_events_route" {
  api_id    = aws_apigatewayv2_api.slack_bot_api.id
  route_key = "ANY /slack/events"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

# デフォルトステージの設定
resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.slack_bot_api.id
  name        = "$default"
  auto_deploy = true
}

# Lambda関数にAPI Gatewayからの呼び出し許可を与える
resource "aws_lambda_permission" "api_gateway_lambda" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.slack_bolt_app.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.slack_bot_api.execution_arn}/*/*/slack/events"
}
