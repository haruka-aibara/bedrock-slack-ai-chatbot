provider "aws" {
  region = var.region
  default_tags {
    tags = local.default_tags
  }
}
