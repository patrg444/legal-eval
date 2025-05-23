resource "aws_dynamodb_table" "jobs_table" {
  name         = "${var.project_name}-jobs"
  billing_mode = "PAY_PER_REQUEST" # Or PROVISIONED if throughput is predictable

  hash_key = "job_id"

  attribute {
    name = "job_id"
    type = "S" # String
  }
  # Add other attributes here if they are part of a GSI key or LSI key.
  # Other attributes like s3_uri, status, etc., are schemaless and don't need definition here
  # unless they are part of an index.

  # TTL specification (optional, but good for cleaning up old items)
  # ttl {
  #   attribute_name = "ttl_timestamp"
  #   enabled        = true
  # }

  # Point-in-time recovery
  point_in_time_recovery {
    enabled = true
  }

  # Server-side encryption (default is AWS owned CMK)
  # server_side_encryption {
  #   enabled     = true
  #   kms_key_arn = aws_kms_key.dynamodb_kms_key.arn # If using customer managed CMK
  # }

  tags = {
    Name        = "${var.project_name}-jobs-table"
    Project     = var.project_name
    Environment = "dev"
  }
}

# (Optional) KMS Key for DynamoDB encryption if CMK is preferred
# resource "aws_kms_key" "dynamodb_kms_key" {
#   description             = "KMS key for DynamoDB table server-side encryption"
#   deletion_window_in_days = 10
#   enable_key_rotation     = true
#   tags = {
#     Name    = "${var.project_name}-dynamodb-kms-key"
#     Project = var.project_name
#   }
# }

# --- Outputs for DynamoDB ---
output "dynamodb_jobs_table_name" {
  description = "Name of the DynamoDB jobs table."
  value       = aws_dynamodb_table.jobs_table.name
}

output "dynamodb_jobs_table_arn" {
  description = "ARN of the DynamoDB jobs table."
  value       = aws_dynamodb_table.jobs_table.arn
}
