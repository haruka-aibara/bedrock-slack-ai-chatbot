resource "aws_dynamodb_table" "conversation_history" {
  name         = "${local.project_name}-conversation-history"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "thread_ts"

  attribute {
    name = "thread_ts"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
