# Contract Intelligence API - AWS Serverless Application Repository Edition

## Overview

This AWS Serverless Application Repository (SAR) application deploys the backend infrastructure for the Contract Intelligence API. It provides a robust, scalable, and secure solution for ingesting legal contracts, performing Optical Character Recognition (OCR) via AWS Textract, and preparing data for advanced AI/ML analysis.

**Note:** This SAR application deploys the core API, data processing pipeline, and storage. You must provide the name of a **pre-existing Amazon SageMaker endpoint** that hosts the necessary machine learning models for clause extraction, risk classification, and summarization.

## Features Deployed by this SAR Application

*   **API Gateway:** A secure HTTP API to submit contracts and query job status.
*   **AWS Lambda Functions:**
    *   **Submission Handler:** Ingests contract details, validates input, and initiates the asynchronous processing workflow.
    *   **Completion Handler:** Orchestrates the post-OCR steps, including invoking your SageMaker endpoint and storing results.
    *   **Query Handler:** Provides status updates for processing jobs.
*   **Amazon S3 Buckets:**
    *   **Input Bucket:** For storing uploaded contract documents.
    *   **Results Bucket:** For storing the JSON output containing extracted insights.
*   **Amazon DynamoDB Table:** Tracks the status and metadata of each contract processing job.
*   **Amazon SNS Topic:** Facilitates asynchronous communication between processing stages (e.g., Textract completion notifications).
*   **IAM Roles:** Necessary IAM roles with least-privilege permissions for all created resources to interact securely.

## Architecture

The application deploys an event-driven, serverless architecture on AWS:

1.  **API Gateway:** Receives contract submission requests.
2.  **ProcessContractLambda:** Triggered by API Gateway. It:
    *   Records the job in the **JobsTable** (DynamoDB).
    *   Initiates an OCR job with **AWS Textract**, configured to send a completion notification to the **ProcessingNotificationTopic** (SNS).
3.  **Textract:** Processes the document from the **InputBucket**.
4.  **ProcessingNotificationTopic (SNS):** Receives Textract completion notification.
5.  **CompletionHandlerLambda:** Triggered by the SNS topic. It:
    *   Retrieves the Textract output.
    *   Invokes the customer-provided **SageMaker Endpoint** with the extracted text.
    *   Stores the final analysis results in the **ResultsBucket**.
    *   Updates the job status in the **JobsTable**.
    *   (Optionally) Sends a callback notification if a URL was provided during submission.
6.  **QueryHandlerLambda:** Triggered by API Gateway to fetch job status from the **JobsTable**.

## Prerequisites

*   **AWS Account:** An active AWS account.
*   **SageMaker Endpoint:** You must have a deployed Amazon SageMaker real-time endpoint with the necessary contract analysis models. You will provide the name of this endpoint as a parameter during deployment.
*   **Lambda Deployment Package:** The Lambda source code for this application must be packaged as a ZIP file and uploaded to an S3 bucket in your account. You will provide the S3 bucket name and S3 key (path to the ZIP file) as parameters during deployment. Instructions for packaging the Lambda code can be found in the project's main README.

## Deployment Instructions

1.  **Navigate to the AWS Serverless Application Repository:**
    *   Open the AWS Management Console.
    *   Search for "Serverless Application Repository" and navigate to its page.
    *   Ensure you are in your desired AWS Region.
2.  **Find and Deploy the Application:**
    *   Search for "ContractIntelligenceApi" (or the name it's published under).
    *   Click on the application name to view details.
    *   Click the "Deploy" button.
3.  **Configure Application Parameters:**
    You will be prompted to enter parameters for the application stack. Key parameters include:
    *   **Application name:** (e.g., `my-contract-api`)
    *   **StageName:** (Default: `Prod`) - Stage for API Gateway.
    *   **SageMakerEndpointName:** (REQUIRED) - The exact name of your existing SageMaker endpoint.
    *   **LambdaCodeS3Bucket:** (REQUIRED) - The S3 bucket where your Lambda deployment ZIP file is stored.
    *   **LambdaCodeS3Key:** (REQUIRED) - The S3 key (path) to the Lambda deployment ZIP file in the `LambdaCodeS3Bucket`.
    *   **LogRetentionInDays:** (Default: `14`) - For CloudWatch logs.
    *   **S3DocumentRetentionDays:** (Default: `90`) - Days to retain documents in S3 buckets (-1 to disable expiration).
    *   Other parameters for Lambda memory/timeout can be adjusted if needed.
4.  **Acknowledge IAM Role Creation:**
    *   This application will create IAM roles and policies to enable the Lambda functions and other services to interact securely. Check the box to acknowledge that AWS CloudFormation will create these IAM resources.
5.  **Deploy:**
    *   Click the "Deploy" button at the bottom of the page.
    *   Deployment will take a few minutes. You can monitor the progress in the AWS CloudFormation console.

## Application Parameters

*   **`StageName` (String):** Stage name for API Gateway. Default: `Prod`.
*   **`SageMakerEndpointName` (String):** REQUIRED. Name of your existing SageMaker endpoint.
*   **`LambdaCodeS3Bucket` (String):** REQUIRED. S3 bucket containing the Lambda deployment package.
*   **`LambdaCodeS3Key` (String):** REQUIRED. S3 key of the Lambda deployment package in the specified bucket.
*   **`LogRetentionInDays` (Number):** Retention period for CloudWatch logs. Default: `14`.
*   **`LambdaRuntime` (String):** Python runtime for Lambda functions. Default: `python3.11`.
*   **`LambdaMemorySizeMain` (Number):** Memory (MB) for the main API handler Lambda. Default: `512`.
*   **`LambdaTimeoutMain` (Number):** Timeout (seconds) for the main API handler Lambda. Default: `60`.
*   **`LambdaMemorySizeCompletion` (Number):** Memory (MB) for the completion handler Lambda. Default: `1024`.
*   **`LambdaTimeoutCompletion` (Number):** Timeout (seconds) for the completion handler Lambda. Default: `300`.
*   **`LambdaMemorySizeQuery` (Number):** Memory (MB) for the query handler Lambda. Default: `256`.
*   **`LambdaTimeoutQuery` (Number):** Timeout (seconds) for the query handler Lambda. Default: `30`.
*   **`S3DocumentRetentionDays` (Number):** Days to retain documents in input/results S3 buckets (-1 for no rule, 0 is invalid in CFN for this setup). Default: `90`.

## Outputs

Once deployed, the CloudFormation stack will provide the following outputs:

*   **`ApiGatewayInvokeURL`:** The URL to invoke the deployed API.
*   **`InputS3BucketName`:** Name of the S3 bucket created for input documents.
*   **`ResultsS3BucketName`:** Name of the S3 bucket created for result documents.
*   **`JobsDynamoDBTableName`:** Name of the DynamoDB table for job tracking.
*   **`ProcessingSNSTopicARN`:** ARN of the SNS topic for processing notifications.

## Using the API

Refer to the OpenAPI specification (`docs/openapi.yaml` in the source repository) or the main project README for details on API endpoints, request/response schemas, and authentication (if API keys/Usage Plans were configured).

## License

This SAR application is licensed under the Apache-2.0 License. See the LICENSE_SAR.txt file.

## Source Code

The source code for this application can be found at [Your GitHub Repository URL - Placeholder].
