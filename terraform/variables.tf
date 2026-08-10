variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "nl2sql-agentic-rag"
}

variable "lambda_image_tag" {
  description = "Tag of the image already pushed to ECR (see terraform/README.md for the two-phase deploy workflow -- this can't be a real tag until after the first apply creates the ECR repo)"
  type        = string
  default     = "latest"
}

variable "gemini_api_key" {
  description = "Gemini API key (free tier)"
  type        = string
  sensitive   = true
}

variable "groq_api_key" {
  description = "Groq API key (free tier)"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
}

variable "planner_provider" {
  type    = string
  default = "gemini"
}

variable "planner_model" {
  type    = string
  default = "gemini-2.5-flash"
}

variable "generator_provider" {
  type    = string
  default = "openai"
}

variable "generator_model" {
  type    = string
  default = "gpt-5-mini"
}

variable "classifier_provider" {
  type    = string
  default = "groq"
}

variable "classifier_model" {
  type    = string
  default = "llama-3.1-8b-instant"
}

variable "max_retries" {
  type    = number
  default = 3
}
