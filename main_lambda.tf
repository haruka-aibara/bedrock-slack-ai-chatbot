# Lambda Layer用のzipファイルのアーカイブを作成
data "archive_file" "lambda_layer_zip" {
  type        = "zip"
  source_dir  = "lambda_layer" # Lambda Layerに含めるファイルのディレクトリ
  output_path = "archives/lambda_layer.zip"
}

resource "aws_lambda_layer_version" "slack_bolt" {
  filename                 = data.archive_file.lambda_layer_zip.output_path
  layer_name               = local.app_name
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"]
  description              = "lambda layer for ${local.app_name}" # 説明（オプション）
}

# Lambda関数のコードをzip化
data "archive_file" "lambda_code" {
  type        = "zip"
  source_dir  = "slack_bot_lambda"
  output_path = "archives/lambda_function.zip"
}

resource "aws_lambda_function" "slack_bolt_app" {
  filename         = data.archive_file.lambda_code.output_path
  function_name    = local.app_name
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_code.output_base64sha256
  role             = aws_iam_role.slack_bolt_app_role.arn
  layers           = [aws_lambda_layer_version.slack_bolt.arn]
  timeout          = 3

  environment {
    variables = {
      SLACK_BOT_TOKEN      = var.slack_bot_token
      SLACK_SIGNING_SECRET = var.slack_signing_secret
      BACKEND_QUEUE        = "${local.app_name}-queue"
      ACCOUNT_ID           = data.aws_caller_identity.current.account_id
      REGION               = data.aws_region.current.name
    }
  }
}

# IAMロールの定義
resource "aws_iam_role" "slack_bolt_app_role" {
  name               = "${local.app_name}-role"
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

# IAMポリシーリソース
resource "aws_iam_policy" "lambda_basic_execution" {
  name   = "${local.app_name}-lambda-basic-execution-policy"
  path   = "/service-role/"
  policy = data.aws_iam_policy_document.lambda_basic_execution.json
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
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.app_name}:*"
    ]
  }
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.slack_bolt_app_role.name
  policy_arn = aws_iam_policy.lambda_basic_execution.arn
}

# IAMポリシーリソース
resource "aws_iam_policy" "lambda_sqs" {
  name   = "${local.app_name}-lambda-sqs-policy"
  policy = data.aws_iam_policy_document.lambda_sqs.json
}

# カスタムポリシードキュメントの定義
data "aws_iam_policy_document" "lambda_sqs" {
  statement {
    sid     = "bedrock"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]
    resources = [
      aws_sqs_queue.bedrock_backend.arn
    ]
  }
}

resource "aws_iam_role_policy_attachment" "lambda_sqs" {
  role       = aws_iam_role.slack_bolt_app_role.name
  policy_arn = aws_iam_policy.lambda_sqs.arn
}

# ===== Bedrock =====

# Lambda関数のコードをzip化
data "archive_file" "lambda_bedrock_code" {
  type        = "zip"
  source_dir  = "bedrock_backend_lambda"
  output_path = "archives/bedrock_backend.zip"
}

# Bedrock Backend
resource "aws_lambda_function" "slack_bolt_app_bedrock_backend" {
  filename         = data.archive_file.lambda_bedrock_code.output_path
  function_name    = "${local.app_name}-bedrock-backend"
  handler          = "bedrock_backend.lambda_handler"
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  source_code_hash = data.archive_file.lambda_bedrock_code.output_base64sha256
  role             = aws_iam_role.slack_bolt_app_bedrock_backend_role.arn
  layers           = [aws_lambda_layer_version.slack_bolt.arn]
  timeout          = 30

  environment {
    variables = {
      SLACK_BOT_TOKEN = var.slack_bot_token
      REGION          = data.aws_region.current.name
    }
  }
}

# IAMロールの定義
resource "aws_iam_role" "slack_bolt_app_bedrock_backend_role" {
  name               = "${local.app_name}-bedrock-backend-role"
  path               = "/service-role/"
  assume_role_policy = data.aws_iam_policy_document.lambda_bedrock_assume_role.json
}

# Lambda用のAssume Role Policyを定義するデータブロック
data "aws_iam_policy_document" "lambda_bedrock_assume_role" {
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
data "aws_iam_policy_document" "lambda_bedrock_backend" {
  statement {
    sid    = "sqs"
    effect = "Allow"
    actions = [
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage"
    ]
    resources = [
      aws_sqs_queue.bedrock_backend.arn
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
}

# IAMポリシーリソース
resource "aws_iam_policy" "lambda_bedrock_backend" {
  name   = "${local.app_name}-lambda-bedrock-backend-policy"
  policy = data.aws_iam_policy_document.lambda_bedrock_backend.json
}

resource "aws_iam_role_policy_attachment" "lambda_bedrock_backend" {
  role       = aws_iam_role.slack_bolt_app_bedrock_backend_role.name
  policy_arn = aws_iam_policy.lambda_bedrock_backend.arn
}

# IAMポリシーリソース
resource "aws_iam_policy" "lambda_bedrock_logging" {
  name   = "${local.app_name}-lambda-bedrock-logging-policy"
  path   = "/service-role/"
  policy = data.aws_iam_policy_document.lambda_bedrock_logging.json
}

# カスタムポリシードキュメントの定義
data "aws_iam_policy_document" "lambda_bedrock_logging" {
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
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.app_name}-bedrock-backend:*"
    ]
  }
}

resource "aws_iam_role_policy_attachment" "lambda_bedrock_logging" {
  role       = aws_iam_role.slack_bolt_app_bedrock_backend_role.name
  policy_arn = aws_iam_policy.lambda_bedrock_logging.arn
}

# SQS Lambda 統合
resource "aws_lambda_event_source_mapping" "mapping_for_sugoi_function" {
  event_source_arn = aws_sqs_queue.bedrock_backend.arn
  function_name    = aws_lambda_function.slack_bolt_app_bedrock_backend.arn
}
