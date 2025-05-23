# --- Lambda Execution Role ---
resource "aws_iam_role" "lambda_execution_role" {
  name = "${var.project_name}-lambda-execution-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = {
    Name    = "${var.project_name}-lambda-execution-role"
    Project = var.project_name
  }
}

# Base Lambda policy for CloudWatch Logs and VPC Access
resource "aws_iam_policy" "lambda_base_policy" {
  name        = "${var.project_name}-lambda-base-policy"
  description = "Base policy for Lambda functions for logging and VPC access"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "arn:aws:logs:*:*:*" # Restrict further if specific log groups are known
      },
      {
        Effect   = "Allow",
        Action   = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:AssignPrivateIpAddresses", # Required for Lambda in VPC
          "ec2:UnassignPrivateIpAddresses"  # Required for Lambda in VPC
        ],
        Resource = "*" # These actions are typically broad but necessary for VPC.
                       # Consider restricting by Subnet/SG if possible.
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_base_attach" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_base_policy.arn
}

# Policy for Lambda to interact with S3, Textract, SQS, SageMaker
resource "aws_iam_policy" "lambda_service_interaction_policy" {
  name        = "${var.project_name}-lambda-service-interaction-policy"
  description = "Policy for Lambda to interact with S3, Textract, SQS, and SageMaker"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = [
          "s3:GetObject",
          "s3:GetObjectVersion"
        ],
        Resource = [
          "arn:aws:s3:::${var.project_name}-${var.input_s3_bucket_name}/*",
          # If Lambda needs to read from the models bucket (e.g. for some config)
          # "arn:aws:s3:::${var.project_name}-${var.models_s3_bucket_name}/*"
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
          "s3:PutObject",
          "s3:PutObjectAcl" # If ACLs are managed by Lambda
        ],
        Resource = [
          "arn:aws:s3:::${var.project_name}-${var.results_s3_bucket_name}/*"
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
          "textract:StartDocumentTextDetection",
          "textract:GetDocumentTextDetection"
          # Add other Textract actions as needed by ocr.py
        ],
        # Textract actions are typically on all resources or specific document types/jobs
        Resource = "*" # Textract often uses "*" for job-related actions
      },
      {
        Effect   = "Allow",
        Action   = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage", # If Lambda also polls SQS for Textract results
          "sqs:DeleteMessage",  # If Lambda processes and deletes SQS messages
          "sqs:GetQueueAttributes"
        ],
        Resource = [
          aws_sqs_queue.textract_queue.arn
          # Add DLQ ARN if Lambda interacts with it
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
          "sagemaker:InvokeEndpoint"
        ],
        Resource = [
          aws_sagemaker_endpoint.main.arn
          # Or be more specific if endpoint name is fixed:
          # "arn:aws:sagemaker:${var.aws_region}:${data.aws_caller_identity.current.account_id}:endpoint/${var.project_name}-*"
        ]
      },
      { # Allow Lambda to pass role to Textract (for Textract to access S3 and publish to SNS)
        Effect = "Allow",
        Action = "iam:PassRole",
        Resource = [
            aws_iam_role.textract_service_role.arn
        ],
        Condition = {
            StringEqualsIfExists = {
                "iam:PassedToService": "textract.amazonaws.com"
            }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_service_interaction_attach" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_service_interaction_policy.arn
}

# --- SageMaker Execution Role ---
resource "aws_iam_role" "sagemaker_execution_role" {
  name = "${var.project_name}-sagemaker-execution-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = {
        Service = "sagemaker.amazonaws.com"
      }
    }]
  })

  tags = {
    Name    = "${var.project_name}-sagemaker-execution-role"
    Project = var.project_name
  }
}

# Base SageMaker policy (CloudWatch, ECR, S3)
resource "aws_iam_policy" "sagemaker_base_policy" {
  name        = "${var.project_name}-sagemaker-base-policy"
  description = "Base policy for SageMaker execution role"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ],
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow",
        Action   = [
          "s3:GetObject",
          "s3:ListBucket"
        ],
        Resource = [
          "arn:aws:s3:::${var.project_name}-${var.models_s3_bucket_name}",
          "arn:aws:s3:::${var.project_name}-${var.models_s3_bucket_name}/*"
          # Add other buckets if SageMaker needs access (e.g., for training data)
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
            "s3:PutObject", # For writing model artifacts, output data, etc.
            "s3:ListMultipartUploadParts",
            "s3:AbortMultipartUpload"
        ],
        Resource = [
            "arn:aws:s3:::${var.project_name}-${var.models_s3_bucket_name}/*",
            # Add other buckets if SageMaker needs to write to them
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
          "ecr:GetAuthorizationToken", // Required for ECR
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ],
        Resource = "*" // ECR actions are often on all resources
      },
      { # VPC access for SageMaker if models need to access resources in VPC or run in VPC only mode
        Effect = "Allow",
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
          "ec2:DescribeVpcs",
          "ec2:DescribeSubnets",
          "ec2:DescribeSecurityGroups"
        ],
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "sagemaker_base_attach" {
  role       = aws_iam_role.sagemaker_execution_role.name
  policy_arn = aws_iam_policy.sagemaker_base_policy.arn
}

# --- Textract Service Role ---
# Role that Textract assumes to publish to SNS (and implicitly read from S3 if NotificationChannel is used for results)
resource "aws_iam_role" "textract_service_role" {
  name = "${var.project_name}-textract-service-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = {
        Service = "textract.amazonaws.com"
      }
    }]
  })
  tags = {
    Name    = "${var.project_name}-textract-service-role"
    Project = var.project_name
  }
}

# Policy for Textract to publish to SNS
resource "aws_iam_policy" "textract_sns_publish_policy" {
  name        = "${var.project_name}-textract-sns-policy"
  description = "Allows Textract to publish notifications to an SNS topic."
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = "sns:Publish",
      # Resource will be the SNS Topic ARN, defined in sns.tf (or use variable)
      # For now, using a placeholder, will need to replace with actual SNS topic ARN
      Resource = "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${var.project_name}-TextractCompletionTopic"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "textract_sns_publish_attach" {
  role       = aws_iam_role.textract_service_role.name
  policy_arn = aws_iam_policy.textract_sns_publish_policy.arn
}

# (Optional) Policy for Textract to access S3 if not covered by presigned URLs or job context
# Typically, Textract is given access to the specific S3 object via the StartDocumentTextDetection call.
# However, if Textract needs broader S3 access for some reason or if using NotificationChannel for output,
# this might be needed. The Lambda's call to Textract provides the S3 object, so Textract itself
# might not need explicit GetObject if the permissions are correctly handled by the service-linked role or job context.
# The `iam:PassRole` on the Lambda's role (for `textract_service_role`) is key for the SNS notification channel.

# --- API Gateway CloudWatch Role (Optional but good practice) ---
# This role allows API Gateway to write logs to CloudWatch.
resource "aws_iam_role" "api_gateway_cloudwatch_logs_role" {
  name = "${var.project_name}-api-gateway-cloudwatch-logs-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = {
        Service = "apigateway.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_policy" "api_gateway_cloudwatch_logs_policy" {
  name        = "${var.project_name}-api-gateway-cloudwatch-logs-policy"
  description = "Policy for API Gateway to write logs to CloudWatch"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{
      Effect   = "Allow",
      Action   = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ],
      Resource = "arn:aws:logs:*:*:*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "api_gateway_cloudwatch_logs_attach" {
  role       = aws_iam_role.api_gateway_cloudwatch_logs_role.name
  policy_arn = aws_iam_policy.api_gateway_cloudwatch_logs_policy.arn
}

# To associate this role with your API Gateway for logging:
resource "aws_api_gateway_account" "main" {
  cloudwatch_role_arn = aws_iam_role.api_gateway_cloudwatch_logs_role.arn
}

# --- Data resource to get current AWS account ID and region ---
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
