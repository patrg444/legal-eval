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
      sse_algorithm     = "AES256" # SSE-S3
      # For SSE-KMS, use:
      # kms_master_key_id = aws_kms_key.s3_kms_key.arn
      # sse_algorithm     = "aws:kms"
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
      sse_algorithm = "AES256" # SSE-S3
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
      sse_algorithm = "AES256" # SSE-S3
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

# (Optional) KMS Key for S3 encryption if SSE-KMS is preferred
# resource "aws_kms_key" "s3_kms_key" {
#   description             = "KMS key for S3 bucket server-side encryption"
#   deletion_window_in_days = 10
#   enable_key_rotation     = true
#   tags = {
#     Name    = "${var.project_name}-s3-kms-key"
#     Project = var.project_name
#   }
# }
