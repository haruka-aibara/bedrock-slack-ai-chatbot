terraform {
  cloud {
    organization = "haruka-aibara"
    workspaces {
      name = "bedrock-slack-ai-chatbot"
    }
  }

  # Pessimistic constraint, not an exact pin: HCP Terraform upgrades the workspace's
  # patch version on its own, and an exact pin fails `terraform init` every time it does.
  required_version = "~> 1.15.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.19.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6.0"
    }
  }
}
