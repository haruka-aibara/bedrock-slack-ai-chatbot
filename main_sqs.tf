resource "aws_sqs_queue" "slack_ai_chatbot" {
  name = "${local.project_name}-queue"
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.slack_ai_chatbot_dlq.arn
    # Bedrock throttles under load, and a throttled request is worth retrying rather
    # than parking on the first failure. Three attempts ride out a transient
    # ThrottlingException instead of dropping the user's question.
    maxReceiveCount = 3
  })
  # Must exceed the backend Lambda's timeout, otherwise a slow invocation has its
  # message redelivered while it is still running and the thread gets two answers.
  # AWS recommends six times the function timeout, which is 6 x 30s here.
  visibility_timeout_seconds = 180
  # Long enough to survive a burst of throttling, short enough that an answer never
  # shows up so late it is just noise in the thread.
  message_retention_seconds = 900
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "slack_ai_chatbot_dlq" {
  name = "${local.project_name}-dead-letter-queue"
  # The DLQ is the record of questions the bot failed to answer, so it keeps messages
  # for the SQS maximum (14 days) instead of discarding the evidence after a minute.
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue_redrive_allow_policy" "slack_ai_chatbot_redrive_allow_policy" {
  queue_url = aws_sqs_queue.slack_ai_chatbot_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue",
    sourceQueueArns   = [aws_sqs_queue.slack_ai_chatbot.arn]
  })
}
