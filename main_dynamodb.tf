# Idempotency claims only. Conversation context is read back from the Slack thread,
# which is the copy the user can actually see, so there is no second store to keep in
# sync with it.
resource "aws_dynamodb_table" "idempotency" {
  name         = "${local.project_name}-idempotency"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"

  attribute {
    name = "event_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
