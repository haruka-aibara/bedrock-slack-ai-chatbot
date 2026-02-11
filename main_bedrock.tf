resource "aws_bedrock_inference_profile" "claude_opus_4_6" {
  name        = "Claude Opus 4.6"
  description = "Claude Opus 4.6"

  model_source {
    copy_from = "arn:aws:bedrock:ap-northeast-1:172580074565:inference-profile/global.anthropic.claude-opus-4-6-v1"
  }
}
