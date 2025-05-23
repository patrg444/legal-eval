import os
import json
import logging
import uuid
import boto3
import requests # For callback URL
from botocore.exceptions import ClientError
from urllib.parse import urlparse

# Assuming ocr.py is in src.preprocess
# To make this work for local testing and Lambda deployment, adjust Python path if necessary
# For Lambda, you might package src as a layer or include it in the deployment.
try:
    from src.preprocess import ocr
except ImportError:
    # This is to help with local testing if you're running from the root of the project
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
    from src.preprocess import ocr


# --- Configuration ---
# Environment variables should be set in the Lambda configuration
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULT_S3_BUCKET = os.environ.get("RESULT_S3_BUCKET")
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME")
TEXTRACT_SQS_QUEUE_URL = os.environ.get("TEXTRACT_SQS_QUEUE_URL") # Used by ocr module
TEXTRACT_SNS_TOPIC_ARN = os.environ.get("TEXTRACT_SNS_TOPIC_ARN") # Used by ocr module
TEXTRACT_IAM_ROLE_ARN = os.environ.get("TEXTRACT_IAM_ROLE_ARN") # Used by ocr module

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

s3_client = boto3.client("s3", region_name=AWS_REGION)
sagemaker_runtime_client = boto3.client("sagemaker-runtime", region_name=AWS_REGION)

# --- Helper Functions ---

def parse_s3_uri(s3_uri: str) -> tuple[str, str] | None:
    """Parses an S3 URI into bucket and key."""
    try:
        parsed_url = urlparse(s3_uri)
        if parsed_url.scheme != "s3":
            logger.error(f"Invalid S3 URI scheme: {s3_uri}")
            return None
        bucket = parsed_url.netloc
        key = parsed_url.path.lstrip("/")
        return bucket, key
    except Exception as e:
        logger.error(f"Error parsing S3 URI {s3_uri}: {e}")
        return None

def invoke_sagemaker_endpoint(text: str, endpoint_name: str = SAGEMAKER_ENDPOINT_NAME) -> dict | None:
    """
    Invokes the SageMaker endpoint for predictions.
    """
    if not endpoint_name:
        logger.error("SAGEMAKER_ENDPOINT_NAME is not configured.")
        return None
    if not text:
        logger.error("Cannot invoke SageMaker endpoint with empty text.")
        return None

    try:
        # The payload format depends on what the SageMaker endpoint expects.
        # Assuming it expects a JSON with a "text" field and "tasks".
        payload = {"text": text, "tasks": ["clause_extraction", "risk_classification", "summarization"]}
        response = sagemaker_runtime_client.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Body=json.dumps(payload),
            Accept="application/json" # Specify we want JSON response
        )
        response_body = json.loads(response["Body"].read().decode())
        logger.info(f"Successfully invoked SageMaker endpoint {endpoint_name}.")
        return response_body
    except ClientError as e:
        logger.error(f"ClientError invoking SageMaker endpoint {endpoint_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error invoking SageMaker endpoint {endpoint_name}: {e}")
        return None

def store_results_s3(job_id: str, result_data: dict, bucket: str = RESULT_S3_BUCKET) -> str | None:
    """
    Stores the final JSON result to the designated S3 bucket.
    """
    if not bucket:
        logger.error("RESULT_S3_BUCKET is not configured.")
        return None
    if not job_id: # Ensure job_id is valid for key construction
        logger.error("Invalid job_id for storing results.")
        return None

    result_key = f"results/{job_id}/output.json"
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=result_key,
            Body=json.dumps(result_data, indent=2),
            ContentType="application/json",
        )
        result_s3_uri = f"s3://{bucket}/{result_key}"
        logger.info(f"Successfully stored results in S3: {result_s3_uri}")
        return result_s3_uri
    except ClientError as e:
        logger.error(f"ClientError storing results to S3 (s3://{bucket}/{result_key}): {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error storing results to S3 (s3://{bucket}/{result_key}): {e}")
        return None


def send_callback(callback_url: str, job_id: str, status: str, result_s3_uri: str | None = None, error_message: str | None = None):
    """
    Sends an HTTP POST request to the callback_url.
    """
    payload = {
        "job_id": job_id,
        "status": status,
    }
    if result_s3_uri:
        payload["result_s3_uri"] = result_s3_uri
    if error_message:
        payload["error"] = error_message

    try:
        logger.info(f"Sending callback to {callback_url} with payload: {payload}")
        response = requests.post(callback_url, json=payload, timeout=10) # 10-second timeout
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        logger.info(f"Callback sent successfully to {callback_url}. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send callback to {callback_url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending callback to {callback_url}: {e}")


# --- Lambda Handler ---

def process_contract_request(event, context):
    """
    Lambda handler triggered by API Gateway for contract processing.
    Expected event:
    {
        "document_type": "NDA", // Example
        "s3_uri": "s3://bucket/path/to/document.pdf",
        "callback_url": "https://your-service.com/callback" // Optional
    }
    """
    job_id = str(uuid.uuid4())
    logger.info(f"Starting contract processing for job_id: {job_id}. Event: {event}")

    # --- Validate Input ---
    document_type = event.get("document_type")
    s3_uri = event.get("s3_uri")
    callback_url = event.get("callback_url")

    if not document_type or not s3_uri:
        logger.error("Missing 'document_type' or 's3_uri' in the event.")
        response = {"job_id": job_id, "status": "FAILED", "error": "Missing 'document_type' or 's3_uri'."}
        if callback_url:
            send_callback(callback_url, job_id, "FAILED", error_message="Missing 'document_type' or 's3_uri'.")
        return {"statusCode": 400, "body": json.dumps(response)}

    s3_parse_result = parse_s3_uri(s3_uri)
    if not s3_parse_result:
        logger.error(f"Invalid S3 URI: {s3_uri}")
        response = {"job_id": job_id, "status": "FAILED", "error": f"Invalid S3 URI: {s3_uri}."}
        if callback_url:
            send_callback(callback_url, job_id, "FAILED", error_message=f"Invalid S3 URI: {s3_uri}.")
        return {"statusCode": 400, "body": json.dumps(response)}
    s3_bucket, s3_key = s3_parse_result

    # --- Check Environment Variable Dependencies ---
    if not RESULT_S3_BUCKET:
        logger.error("Configuration error: RESULT_S3_BUCKET is not set.")
        response = {"job_id": job_id, "status": "FAILED", "error": "Server configuration error."}
        # No callback here for server misconfiguration to avoid spamming if continuously triggered
        return {"statusCode": 500, "body": json.dumps(response)}
    if not TEXTRACT_SQS_QUEUE_URL or not TEXTRACT_SNS_TOPIC_ARN or not TEXTRACT_IAM_ROLE_ARN:
        logger.error("Configuration error: Textract SQS/SNS/IAM Role ARNs are not fully set.")
        response = {"job_id": job_id, "status": "FAILED", "error": "Server configuration error (Textract)."}
        return {"statusCode": 500, "body": json.dumps(response)}


    # --- 1. Initiate OCR (Textract) ---
    logger.info(f"Job {job_id}: Initiating OCR for {s3_uri}")
    # The ocr.start_textract_job function is expected to configure NotificationChannel
    # to use the central SNS topic (TEXTRACT_SNS_TOPIC_ARN).
    # It also needs the TEXTRACT_IAM_ROLE_ARN that Textract will assume to publish to SNS.
    # The `job_id` (our API job_id) should be passed as `JobTag` to Textract
    # so the completion handler can correlate Textract's job with our API job.
    textract_call_response = ocr.start_textract_job( # Assuming this function is updated or directly use boto3 here
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        region_name=AWS_REGION,
        sns_topic_arn=TEXTRACT_SNS_TOPIC_ARN, # Central SNS topic for Textract notifications
        iam_role_arn=TEXTRACT_IAM_ROLE_ARN,   # Role Textract assumes to publish to SNS
        client_request_token=job_id, # Use API job_id as client request token for idempotency
        job_tag=job_id # Pass API job_id as JobTag for Textract to include in SNS message
    )

    textract_job_id = textract_call_response if isinstance(textract_call_response, str) else (textract_call_response.get("JobId") if isinstance(textract_call_response, dict) else None)


    if not textract_job_id:
        logger.error(f"Job {job_id}: Failed to start Textract job for {s3_uri}.")
        # Update DynamoDB to FAILED
        error_message_ocr_start = "Failed to initiate document processing (OCR)."
        update_job_status_in_db(job_id, "FAILED", {"error_message": error_message_ocr_start})
        # No callback here as per original logic, but could be added
        return {"statusCode": 500, "body": json.dumps({"job_id": job_id, "status": "FAILED", "error": error_message_ocr_start})}

    logger.info(f"Job {job_id}: Textract job initiated with Textract JobId: {textract_job_id}. ")

    # Update DynamoDB with Textract Job ID
    update_job_status_in_db(job_id, "TEXTRACT_STARTED", {"textract_job_id": textract_job_id})

    # --- Return Initial Response ---
    # The primary Lambda now returns "PROCESSING_STARTED" (or similar) and the job_id.
    # All further processing is asynchronous.
    initial_response_payload = {
        "job_id": job_id,
        "s3_uri": s3_uri,
        "status": "PROCESSING_STARTED", # Or PENDING_TEXTRACT
        "message": "Document processing initiated. Awaiting Textract completion.",
        "textract_job_id": textract_job_id # Include for client reference if useful
    }
    # No direct callback here for "IN_PROGRESS"; callback will be sent by completion handler.

    return {
        "statusCode": 202, # Accepted
        "body": json.dumps(initial_response_payload),
    }


# --- Helper to update DynamoDB (can be moved to a shared utils if needed) ---
from datetime import datetime, timezone
dynamodb_resource = boto3.resource("dynamodb", region_name=AWS_REGION)
DYNAMODB_JOBS_TABLE_NAME = os.environ.get("DYNAMODB_JOBS_TABLE_NAME")

def update_job_status_in_db(job_id: str, new_status: str, updates: dict = None):
    """Updates the job status and other attributes in DynamoDB."""
    if not DYNAMODB_JOBS_TABLE_NAME:
        logger.error("DYNAMODB_JOBS_TABLE_NAME not configured. Cannot update job status.")
        return

    table = dynamodb_resource.Table(DYNAMODB_JOBS_TABLE_NAME)
    timestamp = datetime.now(timezone.utc).isoformat()

    update_expression_parts = ["SET #status_val = :status_val", "#updated_at_val = :updated_at_val"]
    expression_attribute_values = {
        ":status_val": new_status,
        ":updated_at_val": timestamp,
    }
    expression_attribute_names = {
        "#status_val": "status", # 'status' is a reserved keyword
        "#updated_at_val": "updated_at"
    }

    if updates:
        for key, value in updates.items():
            # Ensure keys don't conflict with reserved words or existing placeholders
            attr_name_placeholder = f"#{key.replace('-', '_')}_val" # Basic sanitization for attribute names
            attr_value_placeholder = f":{key.replace('-', '_')}_val"
            
            if attr_name_placeholder in expression_attribute_names: # Handle potential collisions if keys are similar
                idx = 0
                while f"{attr_name_placeholder}{idx}" in expression_attribute_names:
                    idx += 1
                attr_name_placeholder = f"{attr_name_placeholder}{idx}"
                attr_value_placeholder = f":{attr_value_placeholder.lstrip(':')}{idx}"


            update_expression_parts.append(f"{attr_name_placeholder} = {attr_value_placeholder}")
            expression_attribute_names[attr_name_placeholder] = key
            expression_attribute_values[attr_value_placeholder] = value
    
    update_expression = ", ".join(update_expression_parts)

    try:
        logger.info(f"Updating job {job_id} in DynamoDB. Status: {new_status}, Updates: {updates}")
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="UPDATED_NEW",
        )
        logger.info(f"DynamoDB update successful for job {job_id}.")
    except Exception as e:
        logger.error(f"Error updating job {job_id} in DynamoDB: {e}", exc_info=True)


# --- Example Usage (for local testing, not part of Lambda deployment) ---
if __name__ == "__main__":
    # This local test block needs significant updates to mock DynamoDB and the new Textract call signature.
    # For brevity, focusing on the handler logic changes above.
    # To test locally:
    # 1. Set up mock DynamoDB (e.g., using `moto.mock_dynamodb2`).
    # 2. Mock `ocr.start_textract_job` or the direct boto3 Textract client call.
    # 3. Ensure environment variables like DYNAMODB_JOBS_TABLE_NAME, TEXTRACT_SNS_TOPIC_ARN, TEXTRACT_IAM_ROLE_ARN are set.

    print("--- Local Test Placeholder ---")
    print("To test 'process_contract_request' locally, you need to:")
    print("1. Mock AWS services (DynamoDB, Textract).")
    print("2. Set all required environment variables (see Lambda config in Terraform).")
    print("3. Create a sample event similar to API Gateway input.")
    
    # Example of what a test call might look like (needs full mocking context):
    # sample_event_local = {
    #     "document_type": "txt",
    #     "s3_uri": "s3://my-test-bucket/sample.txt",
    #     "callback_url": "http://localhost/callback"
    # }
    # with moto.mock_dynamodb(), moto.mock_textract(): # Simplified moto context
    #     # Setup mock DynamoDB table
    #     # Setup mock Textract start_document_text_detection response
    #     # Set env vars:
    #     os.environ["DYNAMODB_JOBS_TABLE_NAME"] = "contractintel-jobs-local"
    #     os.environ["TEXTRACT_SNS_TOPIC_ARN"] = "arn:aws:sns:us-east-1:000000000000:dummy-topic"
    #     os.environ["TEXTRACT_IAM_ROLE_ARN"] = "arn:aws:iam::000000000000:role/dummy-role"
    #     # ... (other env vars)
    #
    #     # Call the handler
    #     # response = process_contract_request(sample_event_local, {})
    #     # print(json.dumps(response, indent=2))
    pass
