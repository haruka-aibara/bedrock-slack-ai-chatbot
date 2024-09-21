# AWS プロバイダーは実装時のメジャーバージョンの最新以上を使用するよう定義
terraform {
  cloud {
    organization = "haruka-aibara"
    workspaces {
      name = "bedrock-slack-ai-chatbot"
    }
  }

  required_version = ">= 1.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
