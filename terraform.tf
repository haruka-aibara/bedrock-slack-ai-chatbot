terraform {
  cloud {
    organization = "haruka-aibara"
    workspaces {
      name = "bedrock-slack-ai-chatbot"
    }
  }

  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.100.0"
    }
  }
}
