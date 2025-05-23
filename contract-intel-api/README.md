# Contract Intelligence API

[![CI/CD Pipeline](https://github.com/YOUR_USERNAME/YOUR_REPOSITORY/actions/workflows/main.yml/badge.svg)](https://github.com/YOUR_USERNAME/YOUR_REPOSITORY/actions/workflows/main.yml)
*(Please update the badge URL with your actual GitHub username and repository name.)*

## Overview

The Contract Intelligence API is a comprehensive solution designed to ingest legal contracts, perform Optical Character Recognition (OCR), and leverage advanced AI/ML models to extract key clauses, identify and score risks, generate compliance checklists, and provide natural-language summaries of the documents. It is built for robust, scalable, and secure processing of sensitive legal information.

## Features

*   **Key Clause Extraction:** Identifies and extracts critical clauses (e.g., indemnification, limitation of liability, termination clauses).
*   **Risk Scoring:** Analyzes extracted clauses and contract language to assign risk levels (LOW, MEDIUM, HIGH) based on predefined criteria.
*   **Compliance Checklist:** Generates a checklist based on the presence or absence of key clauses and their attributes, aiding in compliance reviews.
*   **Natural-Language Summary:** Provides a concise, human-readable summary of the contract's main points.
*   **Asynchronous Processing:** Handles document ingestion and processing asynchronously, suitable for large documents and varying workloads.
*   **Secure and Scalable:** Built on AWS serverless and managed services, incorporating security best practices like PrivateLink, encryption, and fine-grained IAM controls.

## Architecture

The system follows a serverless, event-driven architecture on AWS:

1.  **API Gateway:** Exposes a private REST API for submitting contracts and checking job status.
2.  **AWS Lambda (Orchestrator):**
    *   The primary Lambda function (`process_contract_request`) receives requests from API Gateway.
    *   It validates input and initiates the OCR process.
3.  **AWS Textract:** Performs OCR on the submitted documents (PDF, DOCX, text) stored in an S3 input bucket.
4.  **SQS & SNS:**
    *   The orchestrator Lambda sends an initial message to an SQS queue after successfully starting a Textract job.
    *   Textract publishes a completion notification to an SNS topic.
    *   The SNS topic is subscribed to the SQS queue, which then triggers a processing Lambda (implicitly, the `ocr.py`'s `process_sqs_message` logic or a dedicated results processor Lambda).
5.  **SageMaker Endpoint:**
    *   The (conceptual) results processing Lambda takes the extracted text from Textract.
    *   It invokes a SageMaker endpoint hosting the ensemble of HuggingFace models (Legal-Longformer, RoBERTa-CLS, PEGASUS-legal-large) for clause extraction, risk classification, and summarization.
    *   The current implementation simulates this step within the main orchestrator Lambda for simplicity in this project iteration.
6.  **Amazon S3:**
    *   **Input Bucket:** Stores uploaded contracts.
    *   **Results Bucket:** Stores the final JSON output containing extracted insights.
    *   **Models Bucket:** Stores the SageMaker model artifacts (`model.tar.gz`).
7.  **Callback URL:** Optionally, the system can notify a client-provided callback URL upon job completion or failure.

This architecture is designed for scalability, cost-efficiency (pay-per-use), and security.

## API Endpoints

More detailed specification available in `docs/openapi.yaml`.

### 1. Submit Contract for Processing

*   **Method:** `POST`
*   **Path:** `/v1/contracts`
*   **Request Body Schema (`application/json`):**
    ```json
    {
      "document_type": "pdf", // enum: [pdf, docx, text]
      "s3_uri": "s3://your-input-bucket/path/to/contract.pdf",
      "callback_url": "https://your-service.com/webhook" // optional
    }
    ```
*   **Response (202 Accepted):**
    ```json
    {
      "job_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
      "status": "IN_PROGRESS"
    }
    ```

### 2. Get Job Status and Result

*   **Method:** `GET`
*   **Path:** `/v1/contracts/{job_id}`
*   **Path Parameter:**
    *   `job_id` (string, UUID): The ID of the job returned by the POST request.
*   **Response (200 OK - Example for completed job):**
    ```json
    {
      "job_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
      "status": "SUCCEEDED", // enum: [IN_PROGRESS, SUCCEEDED, FAILED]
      "result_s3_uri": "s3://your-results-bucket/results/a1b2c3d4.../output.json",
      "error_message": null
    }
    ```
*   **Response (200 OK - Example for failed job):**
    ```json
    {
      "job_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
      "status": "FAILED",
      "result_s3_uri": null,
      "error_message": "Error processing document: SageMaker endpoint invocation failed."
    }
    ```

## Getting Started / Setup

### Prerequisites

*   AWS Account with appropriate permissions.
*   Terraform (v1.5.0+ recommended).
*   Docker (latest).
*   Python 3.11+.
*   Poetry (v1.5.1+ recommended for Python dependency management).
*   Configured AWS CLI credentials (or GitHub Actions secrets for CI/CD).

### Deployment Steps

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
    cd contract-intel-api
    ```

2.  **Configure AWS Credentials:**
    Ensure your environment is configured for AWS access (e.g., via `aws configure` or by setting environment variables `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`). For GitHub Actions, these will be set as repository secrets.

3.  **Build and Push Docker Image (for Model Service):**
    *   **Via GitHub Actions (Recommended):** The CI/CD workflow (`.github/workflows/main.yml`) automatically builds and pushes the Docker image to Amazon ECR when changes are pushed to the `main` branch. The ECR repository name is defined by the `ECR_REPOSITORY` environment variable in the workflow.
    *   **Manual Steps (if needed):**
        1.  Authenticate Docker with ECR:
            ```bash
            aws ecr get-login-password --region <your-aws-region> | docker login --username AWS --password-stdin <your-aws-account-id>.dkr.ecr.<your-aws-region>.amazonaws.com
            ```
        2.  Build the image (from the root of the `contract-intel-api` directory):
            ```bash
            docker build -t <your-ecr-repo-uri>:<tag> -f Dockerfile .
            ```
        3.  Push the image:
            ```bash
            docker push <your-ecr-repo-uri>:<tag>
            ```
        You will need to create the ECR repository (`contract-intel-sagemaker-models` or your chosen name) in AWS first if it doesn't exist.

4.  **Deploy Infrastructure using Terraform:**
    Navigate to the Terraform directory and deploy:
    ```bash
    cd infra
    terraform init
    # Review the plan before applying
    terraform plan -out=tfplan
    terraform apply tfplan
    ```
    *   **Key Terraform Variables:**
        *   `sagemaker_model_image_uri`: This is the URI of the Docker image in ECR for the SageMaker model. If using the GitHub Actions workflow, this is automatically passed from the Docker build job to the Terraform job. If deploying manually, you'll need to provide this (e.g., via a `terraform.tfvars` file or command-line `-var` option). Example: `sagemaker_model_image_uri = "YOUR_ACCOUNT_ID.dkr.ecr.YOUR_REGION.amazonaws.com/YOUR_ECR_REPO_NAME:YOUR_IMAGE_TAG"`
        *   `lambda_zip_s3_key`: Path to the Lambda deployment package in S3. The Terraform setup currently uses a placeholder bucket and key. You'll need to ensure your Lambda deployment package (a zip file containing the `src` directory and dependencies) is uploaded to an S3 bucket, and update `infra/lambda.tf` (or use variables) to point to it.
        *   Other variables in `infra/variables.tf` can be customized as needed (e.g., bucket names, CIDR blocks).

## Local Development & Testing

1.  **Install Dependencies:**
    Ensure Poetry is installed. From the root of the `contract-intel-api` project:
    ```bash
    poetry install
    ```
    This will install all dependencies, including development dependencies, into a virtual environment managed by Poetry.

2.  **Activate Virtual Environment:**
    ```bash
    poetry shell
    ```

3.  **Run Linters:**
    ```bash
    flake8 src tests
    # Or using Ruff (if configured):
    # ruff check src tests
    ```

4.  **Run Unit & Integration Tests:**
    ```bash
    pytest
    # Or with coverage:
    # pytest --cov=src tests/
    ```

## Configuration

The Lambda function (`src/api/handler.py`) relies on the following key environment variables, which are configured by Terraform during deployment:

*   `AWS_REGION`: The AWS region.
*   `RESULT_S3_BUCKET`: S3 bucket for storing processed results.
*   `SAGEMAKER_ENDPOINT_NAME`: Name of the SageMaker endpoint for model inference.
*   `TEXTRACT_SQS_QUEUE_URL`: URL of the SQS queue for Textract job notifications.
*   `TEXTRACT_SNS_TOPIC_ARN`: ARN of the SNS topic for Textract completion.
*   `TEXTRACT_IAM_ROLE_ARN`: ARN of the IAM role Textract assumes.

## Security & Compliance

*   **AWS PrivateLink:** API Gateway, SageMaker, SQS, Textract, and other services are accessed via VPC Endpoints, ensuring traffic does not traverse the public internet.
*   **Encryption:**
    *   Data at Rest: S3 buckets are configured for server-side encryption (SSE-S3 by default, KMS optional).
    *   Data in Transit: HTTPS is enforced for API Gateway and other service communications.
*   **Data Purge:** S3 lifecycle policies are configured for input and results buckets to manage data retention (e.g., delete after 30/90 days).
*   **IAM:** Fine-grained IAM roles and policies are used to grant least privilege access to AWS resources (e.g., Lambda execution role, SageMaker execution role).
*   **Input Validation:** Basic validation is performed on input parameters.
*   **Secrets Management:** AWS Secrets Manager should be used for any sensitive configuration not suitable for environment variables (though not explicitly detailed in current implementation).

## Marketplace Packaging

This solution is designed with AWS Marketplace SaaS contract integration in mind. Key considerations for Marketplace:

*   **Metered Billing:** The API can be integrated with AWS Marketplace Metering Service to bill customers based on usage (e.g., per contract processed, per API call). This would require adding metering logic to the Lambda handler.
*   **Subscription Management:** Integration with AWS License Manager or custom logic can handle subscription validation.
*   **Secure Deployment:** The Terraform scripts and containerized model service provide a repeatable and secure deployment mechanism.

## Project Structure

```
contract-intel-api/
├── .github/
│   └── workflows/
│       └── main.yml        # GitHub Actions CI/CD workflow
├── Dockerfile              # Dockerfile for the model service
├── docs/
│   └── openapi.yaml        # OpenAPI 3 specification
├── infra/                  # Terraform scripts
│   ├── apigateway.tf
│   ├── iam.tf
│   ├── lambda.tf
│   ├── network.tf
│   ├── outputs.tf
│   ├── s3.tf
│   ├── sagemaker.tf
│   ├── sqs.tf
│   └── variables.tf
├── pyproject.toml          # Poetry project configuration
├── README.md
├── src/
│   ├── api/                # Lambda handler for orchestration
│   │   ├── __init__.py
│   │   └── handler.py
│   ├── model_service/      # FastAPI application for SageMaker models
│   │   ├── __init__.py
│   │   └── app.py
│   └── preprocess/         # OCR and text processing utilities
│       ├── __init__.py
│       └── ocr.py
└── tests/                  # Unit and integration tests
    ├── __init__.py
    ├── integration/
    │   ├── __init__.py
    │   ├── sample_documents/
    │   │   └── sample_contract.txt
    │   ├── gold_outputs/
    │   │   └── sample_contract_gold.json
    │   └── test_api_flow.py
    └── ... # Placeholder for unit tests
```

## Data Privacy

This solution is designed with data privacy in mind. Customer documents uploaded for analysis are processed by AWS Textract and a SageMaker AI model. The original documents and extracted text are stored temporarily in customer-controlled S3 buckets created by this solution within your AWS account. 

*   The `InputBucket` (for uploaded documents) and `ResultsBucket` (for JSON analysis results) are configured with a default 30-day lifecycle policy to automatically purge data. You can adjust this retention period by modifying the `S3DocumentRetentionDays` parameter when deploying via the SAR template, or by directly changing the `s3_document_retention_days` variable in the Terraform configuration (`infra/variables.tf`) if deploying manually.
*   We do not store customer documents or their content outside of these S3 buckets and other transient AWS resources (like Lambda memory during processing) within your AWS account.
*   All S3 buckets are configured with server-side encryption (SSE-S3 by default, with options for SSE-KMS via Terraform).
*   Access to AWS services is managed via fine-grained IAM roles and, for the API, through a private API Gateway endpoint accessible only within your VPC, further enhancing data isolation.

## Pricing Guidelines

The cost of running this Contract Intelligence API will depend on your usage and the AWS resources consumed. Key cost drivers include:

*   **AWS SageMaker Endpoint:** Billed per hour based on the instance type (e.g., `ml.m5.large`, `ml.g4dn.xlarge`) and number of instances. Costs vary significantly by region and instance type. (e.g., An `ml.m5.large` might be around $0.10 - $0.25 per hour per instance in `us-east-1`). Autoscaling is configured by default.
*   **AWS Lambda:** Billed based on the number of requests and execution duration. AWS provides a generous free tier.
*   **AWS Textract:** Billed per page processed. For `DetectDocumentText` (used for OCR), the cost is approximately $0.0015 per page for the first 1 million pages, then tiered.
*   **Amazon S3:** Storage costs (e.g., Standard tier is around $0.023 per GB-month in `us-east-1`) and request costs (e.g., PUT, GET requests).
*   **Amazon DynamoDB:** Billed for on-demand capacity (reads/writes) and storage. The `JobsTable` uses on-demand capacity.
*   **API Gateway:** Billed per million requests, plus data transfer out. A free tier is available.
*   **Amazon SNS, Amazon CloudWatch Logs:** Typically have free tiers and then low per-request or per-GB ingested/stored costs.

**Illustrative Example:**

Let's estimate costs for processing 100 contracts per month, each averaging 10 pages. We'll assume the SageMaker endpoint uses one `ml.m5.large` instance running continuously (approximately 730 hours/month).

*   **SageMaker:** 1 instance * ~$0.15/hour * 730 hours = ~$109.50
*   **Textract:** 100 contracts * 10 pages/contract * $0.0015/page = $1.50
*   **Lambda:** (Assuming ~1200 invocations for all three Lambdas related to 100 contracts, with average duration of 500ms for main, 5s for completion, 200ms for query, and 512MB-1GB memory) - Costs would likely be minimal, potentially under $1.00 after free tier.
*   **S3 Storage:** (Assuming 100 contracts * 1MB/contract = 100MB for input, plus similar for results) - Storage cost would be negligible (e.g., < $0.01). Request costs also minimal at this volume.
*   **DynamoDB:** (100 initial writes, ~200 updates for status, 100 reads for queries) - On-demand costs would be very low, likely < $0.50.
*   **API Gateway, SNS, CloudWatch:** Likely < $1.00 for this volume, mostly within free tiers.

*   **Estimated Total (Illustrative):** ~$110 - $120 per month.

**Note:** This is a simplified example. Actual costs can vary significantly based on:
*   **Contract Complexity & Size:** Affects Textract processing time and pages, Lambda duration, and SageMaker inference time.
*   **SageMaker Instance Type & Count:** Larger or GPU instances are more expensive. Autoscaling can optimize costs but depends on traffic patterns.
*   **Data Transfer:** Costs for data transfer in/out of AWS services, especially S3 and API Gateway, if significant.
*   **Region:** AWS prices vary by region.
*   **Log Volume:** Extensive logging to CloudWatch can increase costs.
*   **API Key/Usage Plan:** If you implement more complex API Gateway features, they may have associated costs.

We strongly recommend using the [AWS Pricing Calculator](https://calculator.aws/) to create a detailed estimate based on your specific usage patterns, desired SageMaker instance types, and AWS region.

## Support

Support for this solution is provided via GitHub Issues on this repository. Please provide detailed information when opening an issue, including:
*   A clear description of the issue.
*   Steps to reproduce the issue.
*   Relevant logs or error messages.
*   Your deployment configuration (e.g., if deployed via SAR or Terraform, any customizations).

We aim to address issues based on severity and community feedback. Please note that response times may vary. For enterprise-level support or custom modifications, please contact [Your Company Name/Contact - Placeholder, e.g., `opensource@example.com` or link to a commercial support offering].
