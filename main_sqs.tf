resource "aws_sqs_queue" "slack_ai_chatbot" {
  name = "${local.project_name}-queue"
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.slack_ai_chatbot_dlq.arn
    # Throttling is retried inside the invocation, not here: a redelivery only happens
    # after the visibility timeout, far too late for the answer to still be wanted.
    # These attempts exist so a hard failure still reaches the handler, which tells the
    # user it gave up rather than leaving the thread on silence.
    maxReceiveCount = 3
  })
  # Must exceed the backend Lambda's timeout, otherwise a slow invocation has its
  # message redelivered while it is still running and the thread gets two answers.
  # AWS recommends six times the function timeout, which is 6 x 30s here.
  visibility_timeout_seconds = 180
  # Retention only decides when a message disappears without anyone noticing, so it is
  # set generously. Whether a question is still worth answering is decided by the
  # handler's own deadline, which can say so in the thread instead of going quiet.
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
