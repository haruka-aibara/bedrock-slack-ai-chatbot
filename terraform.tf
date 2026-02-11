terraform {
  cloud {
    organization = "haruka-aibara"
    workspaces {
      name = "bedrock-slack-ai-chatbot"
    }
  }

  required_version = "1.13.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.19.0"
    }
  }
}
