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
          aws_s3_bucket.input_bucket.arn, # Grant access to the bucket itself for ListBucket etc. if needed by code
          "${aws_s3_bucket.input_bucket.arn}/*" # Grant access to objects within the bucket
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
          "s3:PutObject",
          "s3:PutObjectAcl" # If ACLs are managed by Lambda
        ],
        Resource = [
          aws_s3_bucket.results_bucket.arn,
          "${aws_s3_bucket.results_bucket.arn}/*"
        ]
      },
      { # KMS permissions for SSE-KMS encrypted S3 buckets
        Effect = "Allow",
        Action = [
          "kms:Decrypt",         # Needed to read objects from SSE-KMS buckets
          "kms:GenerateDataKey", # Needed to write objects to SSE-KMS buckets
          "kms:DescribeKey"      # Potentially useful for troubleshooting
        ],
        # Restrict to the specific KMS key used for S3 buckets
        Resource = [aws_kms_key.s3_kms_key.arn]
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
          # This was for the old SQS setup, may or may not be needed
          # If ocr.py still sends a custom message to this queue, keep it.
          # aws_sqs_queue.textract_queue.arn
          # Add DLQ ARN if Lambda interacts with it
        ]
      },
      {
        Effect   = "Allow",
        Action   = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ],
        Resource = [
          aws_dynamodb_table.jobs_table.arn
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
            aws_iam_role.textract_service_role.arn # Ensure this role can publish to the new SNS topic
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


# --- Post-Processing Lambda Execution Role ---
resource "aws_iam_role" "post_processing_lambda_role" {
  name = "${var.project_name}-post-processing-lambda-role"
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
    Name    = "${var.project_name}-post-processing-lambda-role"
    Project = var.project_name
  }
}

resource "aws_iam_role_policy_attachment" "post_processing_lambda_base_attach" {
  role       = aws_iam_role.post_processing_lambda_role.name
  policy_arn = aws_iam_policy.lambda_base_policy.arn # Reuse base policy for logs and VPC
}

# Policy for Post-Processing Lambda
resource "aws_iam_policy" "post_processing_lambda_policy" {
  name        = "${var.project_name}-post-processing-lambda-policy"
  description = "Policy for Post-Processing Lambda to interact with DynamoDB, Textract, SageMaker, S3"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query" # If needed for looking up Textract JobId
        ],
        Resource = aws_dynamodb_table.jobs_table.arn
      },
      {
        Effect   = "Allow",
        Action   = [
          "textract:GetDocumentTextDetection"
          # Add other Get* actions if different Textract features are used
        ],
        Resource = "*" # Textract Get actions often require *
      },
      {
        Effect   = "Allow",
        Action   = [
          "sagemaker:InvokeEndpoint"
        ],
        Resource = aws_sagemaker_endpoint.main.arn
      },
      {
        Effect   = "Allow",
        Action   = [
          "s3:PutObject"
        ],
        Resource = "${aws_s3_bucket.results_bucket.arn}/*" # Allow putting objects into results bucket
      }
      # If this Lambda also sends callbacks or publishes to another SNS:
      # {
      #   Effect = "Allow",
      #   Action = "sns:Publish",
      #   Resource = "arn_of_another_sns_topic_if_needed"
      # }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "post_processing_lambda_policy_attach" {
  role       = aws_iam_role.post_processing_lambda_role.name
  policy_arn = aws_iam_policy.post_processing_lambda_policy.arn
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
      # Ensure this points to the NEW central SNS topic for processing notifications
      Resource = aws_sns_topic.processing_notifications.arn # From sns.tf
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


# --- Query Lambda Execution Role (for GET /v1/contracts/{job_id}) ---
resource "aws_iam_role" "query_lambda_role" {
  name = "${var.project_name}-query-lambda-role"
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
    Name    = "${var.project_name}-query-lambda-role"
    Project = var.project_name
  }
}

# Attach base policy (logs, VPC)
resource "aws_iam_role_policy_attachment" "query_lambda_base_attach" {
  role       = aws_iam_role.query_lambda_role.name
  policy_arn = aws_iam_policy.lambda_base_policy.arn
}

# Policy for Query Lambda to access DynamoDB
resource "aws_iam_policy" "query_lambda_dynamodb_policy" {
  name        = "${var.project_name}-query-lambda-dynamodb-policy"
  description = "Policy for Query Lambda to get items from DynamoDB jobs table"
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = [
          "dynamodb:GetItem"
        ],
        Resource = aws_dynamodb_table.jobs_table.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "query_lambda_dynamodb_attach" {
  role       = aws_iam_role.query_lambda_role.name
  policy_arn = aws_iam_policy.query_lambda_dynamodb_policy.arn
}
