variable "app_name" {
  type        = string
  description = "生成AIチャットボットアプリの名前"
}

variable "slack_bot_token" {
  description = "Slack Bot User OAuth Token"
  type        = string
  sensitive   = true
}

variable "slack_signing_secret" {
  description = "Slack Signing Secret"
  type        = string
  sensitive   = true
}

variable "queue_arn" {
  type = string
}
