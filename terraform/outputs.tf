output "api_invoke_url" {
  description = "Public URL for the deployed API"
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "ecr_repository_url" {
  description = "Push images here: docker push <this>:<tag>"
  value       = aws_ecr_repository.api.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.api.function_name
}
