locals {
  project_name        = "bedrock-slack-ai-chatbot"
  git_repository_name = "https://github.com/haruka-aibara/bedrock-slack-ai-chatbot"

  default_tags = {
    Owner       = "haruka-aibara"
    Terraform   = true
    Environment = var.env
    Project     = local.project_name
    Repository  = local.git_repository_name
  }
}
