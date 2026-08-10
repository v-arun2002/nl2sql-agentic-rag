terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # No remote backend configured -- state stays local (terraform.tfstate)
  # for a single-developer portfolio project. For a team, uncomment and
  # point this at an S3 bucket + DynamoDB lock table instead.
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "nl2sql-agentic-rag/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.aws_region
}
