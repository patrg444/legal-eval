# --- Lambda Function ---
resource "aws_lambda_function" "main" {
  function_name = "${var.project_name}-main-handler"
  handler       = var.lambda_handler # "src.api.handler.process_contract_request"
  role          = aws_iam_role.lambda_execution_role.arn # From iam.tf
  runtime       = var.lambda_runtime # "python3.11"

  # Assuming the deployment package is uploaded to an S3 bucket (e.g., by CI/CD)
  # You'll need to create this bucket separately or ensure it exists.
  # For this subtask, we use a placeholder bucket name.
  # In a real scenario, this bucket might be one of the S3 buckets defined in s3.tf or a dedicated one.
  s3_bucket = "${var.project_name}-lambda-deployments" # Placeholder bucket name
  s3_key    = var.lambda_zip_s3_key                 # "lambda_deployment_package.zip"

  # Alternatively, for local testing or small projects, you can specify a local zip file:
  # filename         = "../lambda_deployment_package.zip" # Path relative to this terraform file
  # source_code_hash = filebase64sha256("../lambda_deployment_package.zip") # To trigger updates on code change

  timeout     = 60  # seconds
  memory_size = 512 # MB

  environment {
    variables = {
      AWS_REGION             = var.aws_region
      RESULT_S3_BUCKET       = aws_s3_bucket.results_bucket.id # Still needed if main lambda has any fallback/error S3 writes
      SAGEMAKER_ENDPOINT_NAME = aws_sagemaker_endpoint.main.name # Needed by post-processing lambda
      # TEXTRACT_SQS_QUEUE_URL = aws_sqs_queue.textract_queue.id # May not be needed by main lambda anymore
      # TEXTRACT_SNS_TOPIC_ARN is now the central one for notifications
      TEXTRACT_SNS_TOPIC_ARN = aws_sns_topic.processing_notifications.arn # CENTRAL SNS TOPIC
      TEXTRACT_IAM_ROLE_ARN  = aws_iam_role.textract_service_role.arn # Role Textract assumes
      DYNAMODB_JOBS_TABLE_NAME = aws_dynamodb_table.jobs_table.name # For main and post-processing lambdas
      # Add other environment variables required by handler.py
      # e.g. LOG_LEVEL = "INFO"
    }
  }

  # VPC Configuration (if Lambda needs to access resources in VPC)
  # This is crucial for accessing SQS, SageMaker, and potentially other services
  # via VPC endpoints without traversing the public internet.
  vpc_config {
    subnet_ids         = aws_subnet.private[*].id # Use private subnets
    security_group_ids = [aws_security_group.lambda_sg.id]
  }

  # Dead Letter Queue for the Lambda function itself (different from SQS DLQ)
  # dead_letter_config {
  #   target_arn = aws_sqs_queue.lambda_dlq.arn # Requires another SQS queue for Lambda DLQ
  # }

  tags = {
    Name        = "${var.project_name}-main-lambda"
    Project     = var.project_name
    Environment = "dev"
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_base_attach,
    aws_iam_role_policy_attachment.lambda_service_interaction_attach,
    aws_cloudwatch_log_group.lambda_lg, # Ensure log group exists before function
    aws_sagemaker_endpoint.main # Ensure SageMaker endpoint is available
  ]
}

# --- Security Group for Lambda Function (if VPC enabled) ---
resource "aws_security_group" "lambda_sg" {
  name        = "${var.project_name}-lambda-sg"
  description = "Security group for the main Lambda function"
  vpc_id      = aws_vpc.main.id # From network.tf

  # Ingress: Typically, Lambda SGs don't need ingress rules unless invoked by specific VPC resources.
  # API Gateway invocation (if private) would be via VPC endpoint, not directly to Lambda's ENI.
  # If other resources within the VPC need to call Lambda directly (e.g. EC2), add rules here.

  # Egress: Allow Lambda to communicate with AWS services (SQS, S3, SageMaker, Textract, CloudWatch Logs)
  # via VPC endpoints. HTTPS (port 443) is the primary requirement.
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    # Destination should be the CIDR of the VPC or specific security groups of the VPC endpoints
    # Using the interface_endpoints_sg which is associated with all relevant VPC Endpoints
    # Alternatively, if endpoints have their own SGs, reference those.
    # For simplicity, allowing to the endpoint SG or the entire VPC.
    # More secure would be to use prefix lists for AWS services if not using VPC endpoints for all.
    # However, since we have VPC endpoints, traffic should route through them.
    destination_security_group_ids = [aws_security_group.interface_endpoints_sg.id]
    # Or, if not using destination_security_group_ids:
    # cidr_blocks = [var.vpc_cidr_block] # Allow to anywhere in VPC, VPC endpoints will pick it up
  }
  # If Lambda needs to make calls to external `callback_url`s over the internet,
  # it will use the NAT Gateway. This requires an egress rule to 0.0.0.0/0.
  # This rule should be specific if possible.
  egress {
    description = "Allow outbound internet access for callbacks via NAT Gateway"
    from_port   = 443 # HTTPS for callbacks
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # For external callbacks
  }
   egress {
    description = "Allow outbound internet access for callbacks via NAT Gateway (HTTP)"
    from_port   = 80 # HTTP for callbacks (less secure, but if necessary)
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # For external callbacks
  }


  tags = {
    Name    = "${var.project_name}-lambda-sg"
    Project = var.project_name
  }
}


# --- Query Lambda Function (for GET /v1/contracts/{job_id}) ---
resource "aws_lambda_function" "query_lambda" {
  function_name = "${var.project_name}-query-handler"
  # Assuming the same deployment package contains this handler
  s3_bucket = "${var.project_name}-lambda-deployments" # Placeholder, use your actual bucket
  s3_key    = var.lambda_zip_s3_key                 # "lambda_deployment_package.zip"

  handler = "src.api.query_handler.get_contract_status" # Path to the new handler
  role    = aws_iam_role.query_lambda_role.arn # New dedicated role
  runtime = var.lambda_runtime

  timeout     = 30  # seconds
  memory_size = 256 # MB (likely less resource intensive than processing lambdas)

  environment {
    variables = {
      AWS_REGION                = var.aws_region
      DYNAMODB_JOBS_TABLE_NAME  = aws_dynamodb_table.jobs_table.name
    }
  }

  # VPC Configuration (optional for this Lambda if only accessing DynamoDB via public endpoint,
  # but recommended for consistency if other Lambdas are in VPC and using DynamoDB VPC endpoint)
  # If DynamoDB VPC endpoint exists and is used, then VPC config is needed.
  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda_sg.id] # Can reuse or create a new one
  }

  tags = {
    Name        = "${var.project_name}-query-lambda"
    Project     = var.project_name
    Environment = "dev"
  }

  depends_on = [
    aws_iam_role_policy_attachment.query_lambda_base_attach,
    aws_iam_role_policy_attachment.query_lambda_dynamodb_attach,
    aws_cloudwatch_log_group.query_lambda_lg,
    aws_dynamodb_table.jobs_table
  ]
}

# --- CloudWatch Log Group for Query Lambda Function ---
resource "aws_cloudwatch_log_group" "query_lambda_lg" {
  name              = "/aws/lambda/${var.project_name}-query-handler"
  retention_in_days = 14

  tags = {
    Name    = "${var.project_name}-query-lambda-log-group"
    Project = var.project_name
  }
}

# --- CloudWatch Log Group for Lambda Function ---
resource "aws_cloudwatch_log_group" "lambda_lg" {
  name              = "/aws/lambda/${var.project_name}-main-handler"
  retention_in_days = 14 # Adjust as needed

  tags = {
    Name    = "${var.project_name}-lambda-log-group"
    Project = var.project_name
  }
}

# (Optional) SQS Queue for Lambda Dead Letter Queue
# resource "aws_sqs_queue" "lambda_dlq" {
#   name = "${var.project_name}-lambda-dlq"
#   tags = {
#     Name    = "${var.project_name}-lambda-dlq"
#     Project = var.project_name
#   }
# }

# Note on Lambda Source Code:
# The `s3_bucket` and `s3_key` attributes for `aws_lambda_function` assume that
# you have a separate process (e.g., CI/CD pipeline) that packages your Lambda code
# (src/api/handler.py and its dependencies from src/preprocess, etc.) into a zip file
# and uploads it to an S3 bucket.
# The bucket `var.project_name}-lambda-deployments` is a placeholder; you'd need to create it.
# For this Terraform setup to apply successfully, the specified S3 object must exist.
#
# If you want Terraform to manage the packaging and uploading, you can use `archive_file` data source:
# data "archive_file" "lambda_zip" {
#   type        = "zip"
#   source_dir  = "../src/" # Or a staging directory with all necessary files
#   output_path = "${path.module}/lambda_deployment_package.zip"
# }
# resource "aws_s3_object" "lambda_zip_upload" {
#   bucket = aws_s3_bucket.lambda_deploy_bucket.id # A bucket for lambda zips
#   key    = var.lambda_zip_s3_key
#   source = data.archive_file.lambda_zip.output_path
#   etag   = data.archive_file.lambda_zip.output_md5
# }
# Then in aws_lambda_function:
# s3_bucket = aws_s3_bucket.lambda_deploy_bucket.id
# s3_key    = aws_s3_object.lambda_zip_upload.key
# source_code_hash = data.archive_file.lambda_zip.output_base64sha256 # To track changes

# The current `handler` path `src.api.handler.process_contract_request` implies that
# the `src` directory is at the root of the deployment package.
# Ensure your packaging process respects this structure.
# Example structure inside lambda_deployment_package.zip:
# src/
#   api/
#     __init__.py
#     handler.py
#     completion_handler.py # New handler
#   preprocess/
#     __init__.py
#     ocr.py
#   model_service/ # Not directly used by this Lambda, but for completeness if packaging all src
#     __init__.py
#     app.py
# <dependencies like requests, etc.>

# The Lambda's IAM role (`lambda_execution_role` in `iam.tf`) needs permissions for:
# - CloudWatch Logs (covered by `lambda_base_policy`).
# - VPC ENI creation/deletion (covered by `lambda_base_policy`).
# - S3 GetObject from input bucket, PutObject to results bucket.
# - Textract Start/Get operations.
# - SQS SendMessage to the Textract SQS queue. (May be removed if ocr.py changes)
# - DynamoDB PutItem/UpdateItem.
# - iam:PassRole for the Textract service role.
# These are covered in `lambda_service_interaction_policy` in `iam.tf`.
# Ensure the Sagemaker endpoint name and SQS queue URL are correctly passed as env vars.
# The `TEXTRACT_SQS_QUEUE_URL` should be the `.id` attribute of the `aws_sqs_queue` resource,
# as this attribute returns the URL of the queue.
# (Corrected in environment variables block: `aws_sqs_queue.textract_queue.id`)


# --- SNS-Triggered Post-Processing Lambda Function ---
resource "aws_lambda_function" "post_processing_lambda" {
  function_name = "${var.project_name}-post-processing-handler"
  # Assuming the same deployment package contains this handler
  s3_bucket = "${var.project_name}-lambda-deployments" # Placeholder, use your actual bucket
  s3_key    = var.lambda_zip_s3_key                 # "lambda_deployment_package.zip"
  # filename = "../lambda_deployment_package.zip" # Or local path
  # source_code_hash = filebase64sha256("../lambda_deployment_package.zip")

  handler = "src.api.completion_handler.handle_processing_completion" # Adjust path if needed
  role    = aws_iam_role.post_processing_lambda_role.arn
  runtime = var.lambda_runtime

  timeout     = 300 # Potentially longer for SageMaker processing + S3 write
  memory_size = 512 # MB

  environment {
    variables = {
      AWS_REGION                = var.aws_region
      RESULT_S3_BUCKET          = aws_s3_bucket.results_bucket.id
      SAGEMAKER_ENDPOINT_NAME    = aws_sagemaker_endpoint.main.name
      DYNAMODB_JOBS_TABLE_NAME  = aws_dynamodb_table.jobs_table.name
      # TEXTRACT_SNS_TOPIC_ARN is not directly needed by this lambda as it's the trigger
      # Add any other specific env vars this lambda needs
    }
  }

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda_sg.id] # Can reuse the same SG or create a new one
  }

  # dead_letter_config {
  #   target_arn = aws_sqs_queue.lambda_dlq.arn # If you have a DLQ for this lambda
  # }

  tags = {
    Name        = "${var.project_name}-post-processing-lambda"
    Project     = var.project_name
    Environment = "dev"
  }

  depends_on = [
    aws_iam_role_policy_attachment.post_processing_lambda_base_attach,
    aws_iam_role_policy_attachment.post_processing_lambda_policy_attach,
    aws_cloudwatch_log_group.post_processing_lambda_lg,
    aws_dynamodb_table.jobs_table,
    aws_sagemaker_endpoint.main
  ]
}

# --- SNS Subscription for the Post-Processing Lambda ---
resource "aws_sns_topic_subscription" "lambda_processing_notification_subscription" {
  topic_arn = aws_sns_topic.processing_notifications.arn # From sns.tf
  protocol  = "lambda"
  endpoint  = aws_lambda_function.post_processing_lambda.arn
}

# --- Lambda Permission for SNS to invoke Post-Processing Lambda ---
resource "aws_lambda_permission" "sns_invoke_post_processing_lambda" {
  statement_id  = "AllowSNSInvokePostProcessingLambda"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.post_processing_lambda.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.processing_notifications.arn
}

# --- CloudWatch Log Group for Post-Processing Lambda Function ---
resource "aws_cloudwatch_log_group" "post_processing_lambda_lg" {
  name              = "/aws/lambda/${var.project_name}-post-processing-handler"
  retention_in_days = 14

  tags = {
    Name    = "${var.project_name}-post-processing-lambda-log-group"
    Project = var.project_name
  }
}
