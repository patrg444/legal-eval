resource "aws_sns_topic" "processing_notifications" {
  name = "${var.project_name}-processing-notifications"

  # (Optional) Configure delivery policy for retries, etc.
  # delivery_policy = jsonencode({ ... })

  # (Optional) Server-side encryption for the topic
  # kms_master_key_id = "alias/aws/sns" # AWS-managed key for SNS

  tags = {
    Name        = "${var.project_name}-processing-notifications-topic"
    Project     = var.project_name
    Environment = "dev"
  }
}

# --- Outputs for SNS ---
output "sns_processing_notifications_topic_arn" {
  description = "ARN of the SNS topic for processing notifications."
  value       = aws_sns_topic.processing_notifications.arn
}

# This replaces the SNS topic previously defined in sqs.tf if this is the central topic.
# If the SNS topic in sqs.tf (`textract_completion_topic`) is specifically for Textract->SQS
# and this new one (`processing_notifications`) is for Textract->Lambda and SageMaker->Lambda,
# then you might have two topics.
# For this task, let's assume `processing_notifications` is THE central topic for Textract (and later SageMaker)
# to publish to, and the new post-processing Lambda will subscribe to this.

# If the `textract_completion_topic` from `sqs.tf` is no longer needed because
# Textract will publish to `aws_sns_topic.processing_notifications.arn`, then
# `aws_sns_topic.textract_completion_topic` and its SQS subscription
# (`aws_sns_topic_subscription.textract_sns_to_sqs_subscription` and
# `aws_sqs_queue_policy.textract_sqs_allow_sns`) in `sqs.tf` might become redundant
# or need adjustment.

# For now, creating this new topic as the primary notification mechanism for the async flow.
# The Textract `NotificationChannel` will point to this topic.
# The new post-processing Lambda will be triggered by this topic.
# The old SQS queue (`textract_queue`) might still be used by `ocr.py`'s `start_textract_job` for its
# initial custom message, but its role in the main async completion flow changes if Textract
# now notifies this new SNS topic which directly triggers a Lambda.

# Let's assume the `textract_queue` in `sqs.tf` and its related SNS topic
# (`textract_completion_topic`) were for a different/previous flow.
# This `processing_notifications` topic will be the one used by Textract's NotificationChannel
# to trigger the new `completion_handler` Lambda.
# The SQS queue (`textract_queue`) might still be used for the initial message from `ocr.start_textract_job`.
# This needs careful review of how `TEXTRACT_SQS_QUEUE_URL` and `TEXTRACT_SNS_TOPIC_ARN`
# environment variables are used by the Python code.

# Based on the problem description:
# "Textract needs to be configured to send a notification to an SNS topic upon completion. This SNS topic is the one defined in Part 2 (this new one)."
# So, the `aws_sns_topic.textract_completion_topic` in `sqs.tf` should ideally be replaced by this one,
# or this one should be used by Textract.

# To simplify and avoid confusion, I will proceed assuming this new SNS topic (`processing_notifications`)
# is the one that Textract will publish to for triggering the new completion Lambda.
# The IAM role for Textract will need `sns:Publish` permission to this topic.
# The old SNS topic in `sqs.tf` might need to be removed or its purpose clarified later if it's still needed.
# For now, I will focus on this new topic as the central piece for the async notifications.
