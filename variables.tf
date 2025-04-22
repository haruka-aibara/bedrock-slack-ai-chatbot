variable "env" {
  description = "environment"
  type        = string
  default     = "production"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
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

variable "bedrock_model_id" {
  description = "Bedrock model ID to use for chat responses"
  type        = string
  default     = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
}

variable "bedrock_max_tokens" {
  description = "Maximum number of tokens to generate in Bedrock responses"
  type        = number
  default     = 1000
}
