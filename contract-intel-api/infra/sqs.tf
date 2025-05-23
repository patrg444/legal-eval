# --- Textract SQS Dead Letter Queue ---
resource "aws_sqs_queue" "textract_dlq" {
  name                        = "${var.project_name}-${var.textract_sqs_dlq_name}"
  message_retention_seconds   = 1209600 # 14 days
  receive_wait_time_seconds   = 10      # Enable long polling

  tags = {
    Name        = "${var.project_name}-textract-dlq"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- Textract SQS Queue ---
resource "aws_sqs_queue" "textract_queue" {
  name                        = "${var.project_name}-${var.textract_sqs_queue_name}"
  delay_seconds               = 0
  max_message_size            = 262144 # 256 KiB
  message_retention_seconds   = 345600 # 4 days (adjust as needed)
  receive_wait_time_seconds   = 20     # Enable long polling
  visibility_timeout_seconds  = 120    # 2 minutes (adjust based on expected processing time)

  # Redrive policy for DLQ
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.textract_dlq.arn
    maxReceiveCount     = 5 # Number of times a message is received before being sent to DLQ
  })

  # (Optional) Server-side encryption
  # kms_master_key_id                 = "alias/aws/sqs" # AWS-managed SQS key
  # kms_data_key_reuse_period_seconds = 300 # 5 minutes

  # If Lambda needs to access SQS via VPC endpoint, this policy might be needed
  # to allow specific VPC endpoints. However, IAM permissions on Lambda role are primary.
  # policy = jsonencode({
  #   Version = "2012-10-17",
  #   Statement = [
  #     {
  #       Effect = "Allow",
  #       Principal = "*",
  #       Action = "sqs:SendMessage",
  #       Resource = aws_sqs_queue.textract_queue.arn,
  #       Condition = {
  #         ArnEquals = {
  #           "aws:SourceArn" = aws_sns_topic.textract_completion_topic.arn # If using SNS to SQS
  #         }
  #       }
  #     },
  #     # Add policy for Lambda to consume if needed, restricted by VPC endpoint
  #   ]
  # })

  tags = {
    Name        = "${var.project_name}-textract-queue"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- SNS Topic for Textract Job Completion (used by ocr.py and IAM policy) ---
# This is used by Textract to notify when a job is done.
# The ocr.py module expects the IAM role for Textract to have sns:Publish permission to this topic.
# And this topic would typically have a subscription that forwards messages to an SQS queue
# which another Lambda (the "Textract result processor") would listen to.
# For this project, the ocr.py sends a message to TEXTRACT_SQS_QUEUE_URL *directly* after starting the job.
# And then an SNS -> SQS setup is also used by Textract itself to signal completion.
# The `process_sqs_message` function in `ocr.py` is designed to handle messages from this SNS->SQS path.

resource "aws_sns_topic" "textract_completion_topic" {
  name = "${var.project_name}-TextractCompletionTopic"
  # delivery_policy = jsonencode(...) # Optional: configure retry policies, etc.
  # kms_master_key_id = "alias/aws/sns" # Optional: SNS encryption

  tags = {
    Name    = "${var.project_name}-textract-completion-topic"
    Project = var.project_name
  }
}

# --- SNS Topic Subscription: SNS -> SQS ---
# This subscription forwards messages from the SNS topic (where Textract publishes)
# to the SQS queue (which the `ocr.py`'s `process_sqs_message` or a dedicated Lambda would process).
resource "aws_sns_topic_subscription" "textract_sns_to_sqs_subscription" {
  topic_arn = aws_sns_topic.textract_completion_topic.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.textract_queue.arn
  raw_message_delivery = false # false: delivers JSON object, true: delivers raw message body
}

# --- SQS Queue Policy to allow SNS to send messages ---
# This is important! SQS queue needs to allow the SNS topic to send messages to it.
resource "aws_sqs_queue_policy" "textract_sqs_allow_sns" {
  queue_url = aws_sqs_queue.textract_queue.id # Use .id for queue URL
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect    = "Allow",
        Principal = {
          Service = "sns.amazonaws.com" # Allows SNS service to send messages
        },
        Action    = "sqs:SendMessage",
        Resource  = aws_sqs_queue.textract_queue.arn,
        Condition = {
          ArnEquals = { # Ensures only the specific SNS topic can send messages
            "aws:SourceArn" = aws_sns_topic.textract_completion_topic.arn
          }
        }
      }
    ]
  })
}

# Note: The IAM policy for Textract (`textract_sns_publish_policy` in iam.tf)
# needs to point to `aws_sns_topic.textract_completion_topic.arn`.
# The current placeholder in iam.tf is:
# "arn:aws:sns:${var.aws_region}:${data.aws_caller_identity.current.account_id}:${var.project_name}-TextractCompletionTopic"
# This will resolve correctly if the SNS topic name matches.
# If you change the SNS topic name here, ensure it's updated in the IAM policy too or use the ARN output.
# It's better to use `aws_sns_topic.textract_completion_topic.arn` directly in the IAM policy if defined in the same root/module.
# Since they are in different files but same module, direct reference should work.
# Let's assume `iam.tf` will be updated or correctly references this.
# The `iam.tf` currently uses a constructed ARN. This is acceptable as long as names match.
# For robustness, one might pass `aws_sns_topic.textract_completion_topic.arn` as a variable to the IAM module
# if these were separated into true Terraform modules.
# For now, ensure `var.project_name` and the static part of the name align.

# The Lambda function in `src/api/handler.py` uses `ocr.start_textract_job`.
# `ocr.start_textract_job` itself sends a custom SQS message (JobId, S3Uri) to `TEXTRACT_SQS_QUEUE_URL`.
# This is a separate SQS message from the one Textract sends via SNS upon completion.
# The `process_sqs_message` in `ocr.py` is designed to handle the SNS-originated message.
# This means `textract_queue` will receive two types of messages if `TEXTRACT_SQS_QUEUE_URL` is set to its URL:
# 1. Custom message from `ocr.start_textract_job` (JobId, S3Uri).
# 2. SNS notification from Textract (full job details).
# The processing logic (e.g., a dedicated Lambda for SQS) needs to differentiate these or
# use separate queues if the custom message is for a different purpose (e.g., immediate tracking vs. result processing).

# For the current design, `TEXTRACT_SQS_QUEUE_URL` in `ocr.py` is the same queue
# that receives SNS notifications. The `process_sqs_message` in `ocr.py` is
# specifically designed to parse the SNS message structure.
# The custom message sent by `start_textract_job` might be redundant or for a different consumer.
# Let's assume the primary flow for Textract results is SNS -> SQS (`textract_queue`) -> Lambda.
# The Lambda defined in `lambda.tf` (main handler) *initiates* Textract.
# A *separate* Lambda (not defined in this subtask's scope but implied by architecture)
# would be triggered by `textract_queue` to process results.
# The `process_sqs_message` in `ocr.py` would be part of that separate Lambda.

# The `TEXTRACT_SQS_QUEUE_URL` env var for the main Lambda (`src/api/handler.py`) is used by
# `ocr.start_textract_job` to send its *initial* SQS message.
# So, `aws_sqs_queue.textract_queue.id` should be passed as this env var.I have created `contract-intel-api/infra/variables.tf`, `contract-intel-api/infra/outputs.tf`, `contract-intel-api/infra/network.tf`, `contract-intel-api/infra/iam.tf`, `contract-intel-api/infra/s3.tf`, and `contract-intel-api/infra/sqs.tf`.
Now I will create `contract-intel-api/infra/sagemaker.tf` to define the SageMaker resources.
