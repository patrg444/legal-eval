# --- SageMaker Model ---
resource "aws_sagemaker_model" "main" {
  name               = "${var.project_name}-contract-model"
  execution_role_arn = aws_iam_role.sagemaker_execution_role.arn # From iam.tf

  primary_container {
    image = var.sagemaker_model_image_uri
    # model_data_url is the S3 path to your model.tar.gz
    # Ensure the S3 bucket and key are correct and the SageMaker execution role has access.
    model_data_url = "s3://${aws_s3_bucket.models_bucket.id}/${var.sagemaker_model_s3_key}"
    # environment = {
    #   SAGEMAKER_PROGRAM = "inference.py" # If your container needs specific entrypoint script
    #   SAGEMAKER_SUBMIT_DIRECTORY = "/opt/ml/model/code" # If you have inference scripts in model.tar.gz
    # }
  }

  # Define VPC configuration if the model needs to access resources in a VPC
  # or if you want to restrict its network access.
  vpc_config {
    subnets = aws_subnet.private[*].id # Use private subnets
    security_group_ids = [aws_security_group.sagemaker_sg.id] # Specific SG for SageMaker
  }

  tags = {
    Name        = "${var.project_name}-contract-sagemaker-model"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- SageMaker Endpoint Configuration ---
resource "aws_sagemaker_endpoint_configuration" "main" {
  name = "${var.project_name}-contract-epc"
  # kms_key_arn = aws_kms_key.sagemaker_kms_key.arn # Optional: For encrypting data at rest on the endpoint instances

  production_variants {
    variant_name           = "AllTraffic" # Name for this variant
    model_name             = aws_sagemaker_model.main.name
    instance_type          = var.sagemaker_endpoint_instance_type # Use new variable
    initial_instance_count = var.sagemaker_autoscale_min_instances # Start with min capacity
    # initial_variant_weight = 1.0 # If only one variant
    # serverless_config {} # For SageMaker Serverless Inference (if applicable)
  }

  # (Optional) DataCaptureConfig for capturing input/output data
  # data_capture_config {
  #   enable_capture = true
  #   destination_s3_uri = "s3://${aws_s3_bucket.sagemaker_capture_bucket.id}/data-capture"
  #   initial_sampling_percentage = 100 # Capture all data
  #   capture_options {
  #     capture_mode = "InputAndOutput" # Or "Input" or "Output"
  #   }
  #   # kms_key_id = aws_kms_key.sagemaker_kms_key.arn # For encrypting captured data
  # }

  tags = {
    Name        = "${var.project_name}-contract-sagemaker-epc"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- SageMaker Endpoint ---
resource "aws_sagemaker_endpoint" "main" {
  name                 = "${var.project_name}-contract-endpoint"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.main.name

  tags = {
    Name        = "${var.project_name}-contract-sagemaker-endpoint"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- SageMaker Endpoint Autoscaling ---
resource "aws_appautoscaling_target" "sagemaker_endpoint_target" {
  max_capacity       = var.sagemaker_autoscale_max_instances
  min_capacity       = var.sagemaker_autoscale_min_instances
  resource_id        = "endpoint/${aws_sagemaker_endpoint.main.name}/variant/AllTraffic" # Variant name must match
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  service_namespace  = "sagemaker"

  # Ensure endpoint is created before trying to attach autoscaling
  depends_on = [aws_sagemaker_endpoint.main]
}

resource "aws_appautoscaling_policy" "sagemaker_endpoint_invocations_policy" {
  name               = "${var.project_name}-sagemaker-invocations-scaling-policy"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.sagemaker_endpoint_target.resource_id
  scalable_dimension = aws_appautoscaling_target.sagemaker_endpoint_target.scalable_dimension
  service_namespace  = aws_appautoscaling_target.sagemaker_endpoint_target.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      # See https://docs.aws.amazon.com/sagemaker/latest/dg/endpoint-scaling-load-testing.html
      # For CPU-based models, SageMakerVariantInvocationsPerInstance is often a good choice.
      # Or SageMakerVariantProvisionedConcurrencyUtilization if using provisioned concurrency (serverless).
      # Or a custom metric if needed.
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }
    target_value       = var.sagemaker_autoscale_target_utilization
    scale_in_cooldown  = 300 # seconds (e.g., 5 minutes)
    scale_out_cooldown = 60  # seconds (e.g., 1 minute)
  }
}


# --- Security Group for SageMaker Endpoint (if VPC enabled) ---
resource "aws_security_group" "sagemaker_sg" {
  name        = "${var.project_name}-sagemaker-sg"
  description = "Security group for SageMaker model endpoint"
  vpc_id      = aws_vpc.main.id # From network.tf

  # Ingress: Allow Lambda to invoke the endpoint (HTTPS)
  # This assumes Lambda SG will be allowed to talk to this SG,
  # or that they are in the same SG (not recommended for separation of concerns).
  # For now, allowing traffic from within the VPC.
  ingress {
    from_port   = 443 # HTTPS for SageMaker invocation
    to_port     = 443
    protocol    = "tcp"
    # Consider restricting source to Lambda's security group ID
    # security_groups = [aws_security_group.lambda_sg.id] # If Lambda has its own SG
    cidr_blocks = [var.vpc_cidr_block] # Or specific private subnet CIDRs
  }

  # Egress: Allow SageMaker to access S3 (for model data) and CloudWatch Logs.
  # If using VPC endpoints, this might be restricted to those endpoints.
  # For simplicity, allowing all outbound.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "${var.project_name}-sagemaker-sg"
    Project = var.project_name
  }
}

# (Optional) KMS Key for SageMaker encryption (e.g., for endpoint storage volume)
# resource "aws_kms_key" "sagemaker_kms_key" {
#   description             = "KMS key for SageMaker resources"
#   deletion_window_in_days = 10
#   enable_key_rotation     = true
#   tags = {
#     Name    = "${var.project_name}-sagemaker-kms-key"
#     Project = var.project_name
#   }
# }

# Note on Model Deployment:
# The `var.sagemaker_model_s3_key` should point to a `model.tar.gz` file.
# This archive typically contains:
# - The serialized model artifacts (e.g., .pth files for PyTorch, .pkl for scikit-learn).
# - Optionally, an `code/` directory with inference scripts (`inference.py`, `requirements.txt`)
#   if the chosen Docker image requires them (e.g., for custom inference logic).
# The `var.sagemaker_model_image_uri` points to the Docker image in ECR that will serve the model.
# This can be an AWS Deep Learning Container image or a custom image.
# The example URI in variables.tf is for a standard AWS PyTorch inference image.
# If that image expects code in `model.tar.gz/code/`, ensure it's packaged correctly.

# The SageMaker endpoint will be accessible within the VPC via its private DNS name,
# facilitated by the SageMaker Runtime VPC Interface Endpoint (`aws_vpc_endpoint.sagemaker_runtime` in `network.tf`).
# The Lambda function (`src/api/handler.py`) invokes this endpoint. Its IAM role
# (`lambda_service_interaction_policy` in `iam.tf`) has `sagemaker:InvokeEndpoint` permission.
# If the Lambda is also in the VPC, it will use the VPC endpoint to communicate with SageMaker.
