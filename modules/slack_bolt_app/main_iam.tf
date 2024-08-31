# IAMロールの定義
resource "aws_iam_role" "slack_bolt_app_role" {
  name               = "${var.app_name}-role"
  path               = "/service-role/"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Lambda用のAssume Role Policyを定義するデータブロック
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# カスタムポリシードキュメントの定義
data "aws_iam_policy_document" "lambda_basic_execution" {
  statement {
    sid       = "loggroup"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup"]
    resources = ["arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    sid    = "logevents"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = [
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.app_name}:*"
    ]
  }

    statement {
    sid     = "sqs"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    resources = [
      var.queue_arn
    ]
  }
}

# IAMポリシーリソース
resource "aws_iam_policy" "lambda_basic_execution" {
  name   = "${var.app_name}-lambda-basic-execution-policy"
  path   = "/service-role/"
  policy = data.aws_iam_policy_document.lambda_basic_execution.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.slack_bolt_app_role.name
  policy_arn = aws_iam_policy.lambda_basic_execution.arn
}

