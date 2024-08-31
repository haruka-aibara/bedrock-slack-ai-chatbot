output "lambda_function_arn" {
  value = aws_lambda_function.slack_bolt_app.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.slack_bolt_app.function_name
}