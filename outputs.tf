# Lambda関数のARNを出力
output "lambda_function_arn" {
  value       = aws_lambda_function.slack_bolt_app.arn
  description = "ARN of the created Lambda function"
}

# 作成されたLambda Layerの情報を出力
output "lambda_layer_arn" {
  value       = aws_lambda_layer_version.slack_bolt.arn
  description = "ARN of the created Lambda Layer"
}

# API GatewayのURLを出力
output "api_gateway_url" {
  value = "${aws_apigatewayv2_stage.default.invoke_url}/slack/events"
}
