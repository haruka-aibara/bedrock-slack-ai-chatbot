# SQSキューリソースの定義
resource "aws_sqs_queue" "this" {
  name = "${var.name}-queue"
}
