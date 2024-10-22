resource "aws_sqs_queue" "slack_ai_chatbot" {
  name = "${local.project_name}-queue"
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.slack_ai_chatbot_dlq.arn
    maxReceiveCount     = 1
  })
  message_retention_seconds = 60
}

resource "aws_sqs_queue" "slack_ai_chatbot_dlq" {
  name                      = "${local.project_name}-dead-letter-queue"
  message_retention_seconds = 60
}

resource "aws_sqs_queue_redrive_allow_policy" "slack_ai_chatbot_redrive_allow_policy" {
  queue_url = aws_sqs_queue.slack_ai_chatbot_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue",
    sourceQueueArns   = [aws_sqs_queue.slack_ai_chatbot.arn]
  })
}
