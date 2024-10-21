resource "aws_sqs_queue" "slack_ai_chatbot" {
  name = "${local.project_name}-queue"
}
