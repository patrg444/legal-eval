# --- KMS Key for S3 Buckets ---
resource "aws_kms_key" "s3_kms_key" {
  description             = "KMS key for S3 bucket server-side encryption for ${var.project_name}"
  deletion_window_in_days = 10 # Or your preferred deletion window (7-30 days)
  enable_key_rotation     = true

  # Default policy allows root user full control.
  # S3 service needs permission to use this key on behalf of the bucket owner for SSE-KMS.
  # Lambda roles will need kms:Decrypt to read, and kms:GenerateDataKey/* for writing.
  # This can often be managed via IAM role permissions granting access to specific keys,
  # or by a key policy that trusts account principals that will interact with S3.
  # For simplicity, a basic key policy that allows the account to manage/use the key for S3:
  policy = jsonencode({
    Version = "2012-10-17",
    Id      = "s3-kms-key-policy-${var.project_name}",
    Statement = [
      {
        Sid    = "EnableIAMUserPermissions",
        Effect = "Allow",
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        },
        Action   = "kms:*",
        Resource = "*"
      },
      # Allow S3 service to use the key for SSE-KMS (often implicitly handled or covered by IAM role perms)
      # Explicitly granting to relevant roles is more secure.
      # {
      #   Sid    = "AllowS3ServiceToUseKey",
      #   Effect = "Allow",
      #   Principal = { Service = "s3.amazonaws.com" }, # This principal type is not always valid for KMS.
      #                                                # Better to grant to specific IAM roles.
      #   Action = [
      #     "kms:Encrypt",
      #     "kms:Decrypt",
      #     "kms:ReEncrypt*",
      #     "kms:GenerateDataKey*",
      #     "kms:DescribeKey"
      #   ],
      #   Resource = "*", # Key itself
      #   Condition = {
      #     StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id },
      #     ArnLike      = { "aws:SourceArn": "arn:aws:s3:::${var.project_name}-*" } # Restrict to project buckets
      #   }
      # }
    ]
  })

  tags = {
    Name    = "${var.project_name}-s3-kms-key"
    Project = var.project_name
  }
}


# --- Input S3 Bucket ---
resource "aws_s3_bucket" "input_bucket" {
  bucket = "${var.project_name}-${var.input_s3_bucket_name}"

  tags = {
    Name        = "${var.project_name}-input-contracts-bucket"
    Project     = var.project_name
    Environment = "dev"
  }
}

resource "aws_s3_bucket_versioning" "input_bucket_versioning" {
  bucket = aws_s3_bucket.input_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "input_bucket_sse" {
  bucket = aws_s3_bucket.input_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.s3_kms_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "input_bucket_lifecycle" {
  bucket = aws_s3_bucket.input_bucket.id

  rule {
    id     = "log" # Rule ID
    status = "Enabled"

    expiration {
      days = 30
    }

    # Noncurrent version expiration (if versioning is enabled)
    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    # Abort incomplete multipart uploads
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "input_bucket_pab" {
  bucket = aws_s3_bucket.input_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Results S3 Bucket ---
resource "aws_s3_bucket" "results_bucket" {
  bucket = "${var.project_name}-${var.results_s3_bucket_name}"

  tags = {
    Name        = "${var.project_name}-results-bucket"
    Project     = var.project_name
    Environment = "dev"
  }
}

resource "aws_s3_bucket_versioning" "results_bucket_versioning" {
  bucket = aws_s3_bucket.results_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results_bucket_sse" {
  bucket = aws_s3_bucket.results_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.s3_kms_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results_bucket_lifecycle" {
  bucket = aws_s3_bucket.results_bucket.id

  rule {
    id     = "log"
    status = "Enabled"

    expiration {
      days = 90 # Example: Results kept for longer
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "results_bucket_pab" {
  bucket = aws_s3_bucket.results_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Models S3 Bucket ---
resource "aws_s3_bucket" "models_bucket" {
  bucket = "${var.project_name}-${var.models_s3_bucket_name}"

  tags = {
    Name        = "${var.project_name}-models-bucket"
    Project     = var.project_name
    Environment = "dev"
  }
}

resource "aws_s3_bucket_versioning" "models_bucket_versioning" {
  bucket = aws_s3_bucket.models_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "models_bucket_sse" {
  bucket = aws_s3_bucket.models_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      # Models bucket might also use the same KMS key or a different one
      # For simplicity, using the same key here.
      kms_master_key_id = aws_kms_key.s3_kms_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "models_bucket_pab" {
  bucket = aws_s3_bucket.models_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
