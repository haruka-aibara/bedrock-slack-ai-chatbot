module "slack_bolt_app" {
  source = "./modules/slack_bolt_app"

  app_name             = local.project_name
  slack_bot_token      = var.slack_bot_token
  slack_signing_secret = var.slack_signing_secret
  queue_arn            = aws_sqs_queue.this.arn
}

module "bedrock_backend" {
  source = "./modules/bedrock_backend"

  app_name             = local.project_name
  slack_bot_token      = var.slack_bot_token
  slack_signing_secret = var.slack_signing_secret
  queue_arn            = aws_sqs_queue.this.arn
}
