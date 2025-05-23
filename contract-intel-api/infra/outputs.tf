output "vpc_id" {
  description = "The ID of the VPC."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "List of IDs of public subnets."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "List of IDs of private subnets."
  value       = aws_subnet.private[*].id
}

output "lambda_execution_role_arn" {
  description = "ARN of the Lambda execution role."
  value       = aws_iam_role.lambda_execution_role.arn
}

output "sagemaker_execution_role_arn" {
  description = "ARN of the SageMaker execution role."
  value       = aws_iam_role.sagemaker_execution_role.arn
}

output "input_s3_bucket_id" {
  description = "ID (name) of the input S3 bucket."
  value       = aws_s3_bucket.input_bucket.id
}

output "results_s3_bucket_id" {
  description = "ID (name) of the results S3 bucket."
  value       = aws_s3_bucket.results_bucket.id
}

output "models_s3_bucket_id" {
  description = "ID (name) of the models S3 bucket."
  value       = aws_s3_bucket.models_bucket.id
}

output "textract_sqs_queue_url" {
  description = "URL of the Textract SQS queue."
  value       = aws_sqs_queue.textract_queue.id # .id returns the URL for SQS queues
}

output "textract_sqs_dlq_url" {
  description = "URL of the Textract SQS Dead Letter Queue."
  value       = aws_sqs_queue.textract_dlq.id # .id returns the URL for SQS queues
}

output "sagemaker_endpoint_name" {
  description = "Name of the SageMaker endpoint."
  value       = aws_sagemaker_endpoint.main.name
}

output "api_gateway_invoke_url" {
  description = "Invoke URL for the API Gateway stage."
  value       = aws_api_gateway_stage.main.invoke_url
}

output "lambda_function_name" {
  description = "Name of the main Lambda function."
  value       = aws_lambda_function.main.function_name
}

output "lambda_function_arn" {
  description = "ARN of the main Lambda function."
  value       = aws_lambda_function.main.arn
}

output "s3_vpc_endpoint_id" {
  description = "ID of the S3 VPC Gateway Endpoint."
  value       = aws_vpc_endpoint.s3.id
}

output "sagemaker_runtime_vpc_endpoint_id" {
  description = "ID of the SageMaker Runtime VPC Interface Endpoint."
  value       = aws_vpc_endpoint.sagemaker_runtime.id
}

output "api_gateway_vpc_endpoint_id" {
  description = "ID of the API Gateway VPC Interface Endpoint (if created)."
  value       = try(aws_vpc_endpoint.api_gateway[0].id, null) # Use try in case it's conditional
}
