# SQSキューリソースの定義
resource "aws_sqs_queue" "this" {
  name = "${local.project_name}-queue"
}
