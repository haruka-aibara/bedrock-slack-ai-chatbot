# HTTP API Gatewayの作成
resource "aws_apigatewayv2_api" "slack_bot_api" {
  name          = "slack-bolt-app"
  protocol_type = "HTTP"
  description   = "HTTP API for Slack Bot Application"
}

# Lambda統合の設定
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.slack_bot_api.id
  integration_uri        = module.slack_bolt_app.lambda_function_arn
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
  function_name = module.slack_bolt_app.lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.slack_bot_api.execution_arn}/*/*/slack/events"
}
