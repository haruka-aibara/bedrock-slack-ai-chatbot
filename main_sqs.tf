# SQSキューリソースの定義
resource "aws_sqs_queue" "bedrock_backend" {
  name = "${local.app_name}-queue"
}
