# Slack認証情報用の変数を定義
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

variable "TFC_AWS_PROVIDER_AUTH" {
  type = string
}

variable "TFC_AWS_RUN_ROLE_ARN" {
  type = string
}
