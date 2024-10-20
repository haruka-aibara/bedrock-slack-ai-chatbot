resource "aws_iam_role" "slack_ai_chatbot" {
  name               = "${local.project_name}_role"
  assume_role_policy = data.aws_iam_policy_document.slack_ai_chatbot_lambda_assume_role.json
}

data "aws_iam_policy_document" "slack_ai_chatbot_lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "slack_ai_chatbot" {
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
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${aws_lambda_function.slack_ai_chatbot.function_name}:*"
    ]
  }

  statement {
    sid     = "sqs"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    resources = [
      aws_sqs_queue.slack_ai_chatbot.arn
    ]
  }
}

resource "aws_iam_policy" "slack_ai_chatbot" {
  name   = "${local.project_name}_policy"
  policy = data.aws_iam_policy_document.slack_ai_chatbot.json
}

resource "aws_iam_role_policy_attachment" "slack_ai_chatbot" {
  role       = aws_iam_role.slack_ai_chatbot.name
  policy_arn = aws_iam_policy.slack_ai_chatbot.arn
}

resource "aws_iam_role" "bedrock_backend" {
  name               = "${local.project_name}_bedrock-backend-role"
  assume_role_policy = data.aws_iam_policy_document.bedrock_backend_lambda_assume_role.json
}

data "aws_iam_policy_document" "bedrock_backend_lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "bedrock_backend" {
  statement {
    sid    = "sqs"
    effect = "Allow"
    actions = [
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage"
    ]
    resources = [
      aws_sqs_queue.slack_ai_chatbot.arn
    ]
  }

  statement {
    sid    = "bedrock"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel"
    ]
    resources = [
      "*"
    ]
  }

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
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${aws_lambda_function.slack_bolt_app_bedrock_backend.function_name}:*"
    ]
  }
}

resource "aws_iam_policy" "bedrock_backend" {
  name   = "${local.project_name}_bedrock-backend-policy"
  policy = data.aws_iam_policy_document.bedrock_backend.json
}

resource "aws_iam_role_policy_attachment" "lambda_bedrock_backend" {
  role       = aws_iam_role.bedrock_backend.name
  policy_arn = aws_iam_policy.bedrock_backend.arn
}
