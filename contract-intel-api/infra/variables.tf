variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "A prefix used for naming resources to ensure uniqueness and grouping."
  type        = string
  default     = "contractintel"
}

variable "vpc_cidr_block" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "List of CIDR blocks for public subnets."
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "List of CIDR blocks for private subnets."
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24"]
}

variable "availability_zones" {
  description = "List of Availability Zones to use."
  type        = list(string)
  # Ensure these are valid for your chosen region
  default     = ["us-east-1a", "us-east-1b"]
}

variable "input_s3_bucket_name" {
  description = "Name for the input S3 bucket. Will be prefixed with project_name."
  type        = string
  default     = "input-contracts"
}

variable "results_s3_bucket_name" {
  description = "Name for the results S3 bucket. Will be prefixed with project_name."
  type        = string
  default     = "processed-results"
}

variable "models_s3_bucket_name" {
  description = "Name for the S3 bucket storing SageMaker models. Will be prefixed with project_name."
  type        = string
  default     = "sagemaker-models"
}

variable "lambda_runtime" {
  description = "Runtime for the Lambda function."
  type        = string
  default     = "python3.11"
}

variable "lambda_handler" {
  description = "Handler for the Lambda function."
  type        = string
  default     = "src.api.handler.process_contract_request" # Updated path
}

variable "lambda_zip_s3_key" {
  description = "S3 key for the Lambda deployment package (zip file)."
  type        = string
  default     = "lambda_deployment_package.zip" # Placeholder
}

variable "sagemaker_instance_type" {
  description = "Instance type for the SageMaker endpoint."
  type        = string
  default     = "ml.m5.large" # Default to a general-purpose instance
}

variable "sagemaker_model_image_uri" {
  description = "ECR URI for the SageMaker model inference image."
  type        = string
  # This would be the URI of your custom container image or a standard AWS image
  default     = "763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-inference:1.13-gpu-py39-cu117-ubuntu20.04-sagemaker" # Example PyTorch image
}

variable "sagemaker_model_s3_key" {
  description = "S3 key for the model.tar.gz file."
  type        = string
  default     = "models/contract-model/model.tar.gz" # Placeholder
}

variable "textract_sqs_queue_name" {
  description = "Name for the SQS queue for Textract job tracking."
  type        = string
  default     = "TextractJobsQueue"
}

variable "textract_sqs_dlq_name" {
  description = "Name for the SQS Dead Letter Queue for Textract."
  type        = string
  default     = "TextractJobsDLQ"
}

variable "api_gateway_stage_name" {
  description = "Stage name for the API Gateway deployment."
  type        = string
  default     = "dev"
}
