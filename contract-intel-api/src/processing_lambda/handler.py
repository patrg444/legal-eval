import os
import json
import logging
import time
import boto3
import requests # For callback URL
from botocore.exceptions import ClientError
from urllib.parse import urlparse
import datetime

# Assuming ocr.py is in src.preprocess
# Adjust Python path if necessary for Lambda deployment.
try:
    from src.preprocess import ocr
except ImportError:
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
    from src.preprocess import ocr

# --- Configuration ---
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
RESULT_S3_BUCKET = os.environ.get("RESULT_S3_BUCKET") # Already used by main handler
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME") # Already used by main handler

# Textract specific (from ocr.py or passed if modified)
TEXTRACT_SQS_QUEUE_URL = os.environ.get("TEXTRACT_SQS_QUEUE_URL")
TEXTRACT_SNS_TOPIC_ARN = os.environ.get("TEXTRACT_SNS_TOPIC_ARN") # Textract's own SNS for its job completion
TEXTRACT_IAM_ROLE_ARN = os.environ.get("TEXTRACT_IAM_ROLE_ARN")

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)
s3_client = boto3.client("s3", region_name=AWS_REGION)
sagemaker_runtime_client = boto3.client("sagemaker-runtime", region_name=AWS_REGION)
# Textract client is initialized within ocr.py

# --- Helper Functions --- (Some might be duplicated from api/handler.py, consider a shared utils module later)
def parse_s3_uri(s3_uri: str) -> tuple[str, str] | None:
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

def update_job_status_in_dynamodb(job_id: str, status: str, updates: dict | None = None):
    if not DYNAMODB_TABLE_NAME:
        logger.error("DYNAMODB_TABLE_NAME not configured. Cannot update job status.")
        return False
    try:
        timestamp = datetime.datetime.utcnow().isoformat()
        expression_attribute_values = {":status": {"S": status}, ":updatedAt": {"S": timestamp}}
        update_expression = "SET job_status = :status, updatedAt = :updatedAt" # job_status is preferred over status

        if updates:
            for key, value in updates.items():
                # Assuming value is already in DynamoDB format, e.g., {"S": "some_string"}
                expression_attribute_values[f":{key}"] = value
                update_expression += f", {key} = :{key}"

        dynamodb_client.update_item(
            TableName=DYNAMODB_TABLE_NAME,
            Key={"job_id": {"S": job_id}},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
        )
        logger.info(f"Job {job_id} status updated to {status} in DynamoDB.")
        return True
    except ClientError as e:
        logger.error(f"ClientError updating DynamoDB for job {job_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error updating DynamoDB for job {job_id}: {e}")
        return False


def get_job_details_from_dynamodb(job_id: str) -> dict | None:
    if not DYNAMODB_TABLE_NAME:
        logger.error("DYNAMODB_TABLE_NAME not configured. Cannot get job details.")
        return None
    try:
        response = dynamodb_client.get_item(
            TableName=DYNAMODB_TABLE_NAME, Key={"job_id": {"S": job_id}}
        )
        if "Item" in response:
            # Deserialize DynamoDB item
            item = {k: list(v.values())[0] for k, v in response["Item"].items()}
            return item
        else:
            logger.warning(f"Job {job_id} not found in DynamoDB.")
            return None
    except ClientError as e:
        logger.error(f"ClientError getting job {job_id} from DynamoDB: {e}")
        return None

def invoke_sagemaker_endpoint(text: str, endpoint_name: str = SAGEMAKER_ENDPOINT_NAME) -> dict | None:
    # (Same as in api/handler.py - consider shared utils)
    if not endpoint_name:
        logger.error("SAGEMAKER_ENDPOINT_NAME is not configured.")
        return None
    if not text: # Added check
        logger.error("Cannot invoke SageMaker with empty text.")
        return None
    try:
        payload = {"text": text, "tasks": ["clause_extraction", "risk_classification", "summarization"]}
        response = sagemaker_runtime_client.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Body=json.dumps(payload),
            Accept="application/json"
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
    # (Same as in api/handler.py - consider shared utils)
    if not bucket:
        logger.error("RESULT_S3_BUCKET is not configured.")
        return None
    result_key = f"results/{job_id}/output.json" # Standardized key
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

def send_callback(callback_url: str, payload: dict):
    # (Modified from api/handler.py - consider shared utils)
    try:
        logger.info(f"Sending callback to {callback_url} with payload: {payload}")
        response = requests.post(callback_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Callback sent successfully to {callback_url}. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send callback to {callback_url}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending callback to {callback_url}: {e}")


def extract_text_with_textract(job_id: str, s3_bucket: str, s3_key: str) -> str | None:
    """
    Simplified Textract interaction for this subtask.
    Starts a Textract job and polls for its completion, then extracts text.
    This is a blocking operation from the perspective of this Lambda.
    In a more advanced setup, Textract completion would trigger a new event.
    """
    logger.info(f"Job {job_id}: Starting Textract for s3://{s3_bucket}/{s3_key}")

    # Use the existing ocr.start_textract_job, but note it sends its own SQS message
    # which we are currently ignoring in this simplified flow.
    # The critical part is getting the Textract Job ID.
    # We need to ensure TEXTRACT_SQS_QUEUE_URL, TEXTRACT_SNS_TOPIC_ARN, TEXTRACT_IAM_ROLE_ARN
    # are correctly set for ocr.py if it relies on them from os.environ.
    ocr.DEFAULT_SQS_QUEUE_URL = TEXTRACT_SQS_QUEUE_URL
    ocr.DEFAULT_SNS_TOPIC_ARN = TEXTRACT_SNS_TOPIC_ARN
    ocr.DEFAULT_IAM_ROLE_ARN = TEXTRACT_IAM_ROLE_ARN

    textract_job_id = ocr.start_textract_job(
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        region_name=AWS_REGION,
        # These are now set globally for ocr module for this invocation
    )

    if not textract_job_id:
        logger.error(f"Job {job_id}: Failed to start Textract job via ocr.py.")
        update_job_status_in_dynamodb(job_id, "FAILED", {"error_message": {"S": "Textract job initiation failed."}})
        return None

    update_job_status_in_dynamodb(job_id, "TEXTRACT_IN_PROGRESS", {"textract_job_id": {"S": textract_job_id}})
    logger.info(f"Job {job_id}: Textract job started: {textract_job_id}. Polling for completion...")

    # Polling for Textract results (simplification for this subtask)
    # In a full async setup, Textract completion (via its SNS->SQS) would trigger this lambda again
    # or another dedicated lambda.
    max_polls = 60 # Approx 10 minutes if polling every 10 seconds
    poll_interval_seconds = 10
    for _ in range(max_polls):
        time.sleep(poll_interval_seconds)
        # The ocr.get_textract_results function is designed to fetch results if job SUCCEEDED
        # It returns None if IN_PROGRESS or FAILED.
        textract_blocks = ocr.get_textract_results(textract_job_id, region_name=AWS_REGION)

        if textract_blocks is not None: # Implies job SUCCEEDED as per ocr.py logic
            logger.info(f"Job {job_id}: Textract job {textract_job_id} SUCCEEDED.")
            # Combine text from blocks (simplified extraction)
            extracted_text = ""
            for block in textract_blocks:
                if block.get("BlockType") == "LINE":
                    extracted_text += block.get("Text", "") + "\n"
            if not extracted_text.strip():
                logger.warning(f"Job {job_id}: Textract job {textract_job_id} SUCCEEDED but no text was extracted.")
                update_job_status_in_dynamodb(job_id, "FAILED", {"error_message": {"S": "Textract completed but no text extracted."}})
                return None
            logger.info(f"Job {job_id}: Successfully extracted text from Textract job {textract_job_id}.")
            return extracted_text.strip()
        else:
            # Check DynamoDB status to see if ocr.get_textract_results logged a failure or if it's still IN_PROGRESS
            # This part is a bit complex because ocr.get_textract_results itself doesn't return the job status string.
            # We assume if it returns None, it's either still IN_PROGRESS or it logged an error.
            # For simplicity, we just continue polling. A more robust poller would check job status API.
            logger.info(f"Job {job_id}: Textract job {textract_job_id} still in progress or get_textract_results returned None. Polling again...")
            # To prevent infinite loops on true failures, one might need to call Textract's Get API directly here.
            # For now, relying on ocr.get_textract_results's logging for failures.

    logger.error(f"Job {job_id}: Textract job {textract_job_id} timed out after polling.")
    update_job_status_in_dynamodb(job_id, "FAILED", {"error_message": {"S": "Textract job polling timed out."}})
    return None


# --- Lambda Handler ---
def process_sns_event(event, context):
    logger.info(f"Received SNS event: {json.dumps(event)}")

    if not DYNAMODB_TABLE_NAME:
        logger.critical("DYNAMODB_TABLE_NAME environment variable not set. Exiting.")
        return {"status": "ERROR", "message": "Configuration error"}

    message_str = event["Records"][0]["Sns"]["Message"]
    message = json.loads(message_str)
    logger.info(f"Parsed SNS message: {message}")

    job_id = message.get("job_id")
    s3_uri = message.get("s3_uri")
    # document_type = message.get("document_type") # Not directly used by this simplified worker

    if not job_id or not s3_uri:
        logger.error("Missing job_id or s3_uri in SNS message.")
        return {"status": "ERROR", "message": "Invalid SNS message payload"}

    # --- Retrieve full job details (like callback_url) from DynamoDB ---
    job_details = get_job_details_from_dynamodb(job_id)
    if not job_details:
        # This shouldn't happen if the main lambda created the item before SNS publish
        logger.error(f"Job {job_id} not found in DynamoDB. Cannot proceed.")
        return {"status": "ERROR", "message": f"Job {job_id} not found"}
    
    callback_url = job_details.get("callback_url") # String or None
    original_document_type = job_details.get("document_type") # For final result storage

    # --- 1. Textract Processing ---
    s3_parse_result = parse_s3_uri(s3_uri)
    if not s3_parse_result:
        update_job_status_in_dynamodb(job_id, "FAILED", {"error_message": {"S": f"Invalid S3 URI: {s3_uri}"}})
        if callback_url: send_callback(callback_url, {"job_id": job_id, "status": "FAILED", "error": f"Invalid S3 URI: {s3_uri}"})
        return {"status": "FAILED", "job_id": job_id, "error": f"Invalid S3 URI: {s3_uri}"}
    s3_bucket, s3_key = s3_parse_result

    extracted_text = extract_text_with_textract(job_id, s3_bucket, s3_key)
    if not extracted_text:
        # extract_text_with_textract already updated DynamoDB and sent callback if needed.
        logger.error(f"Job {job_id}: Text extraction failed.")
        # Callback for text extraction failure
        if callback_url: send_callback(callback_url, {"job_id": job_id, "status": "FAILED", "error": "Text extraction failed."})
        return {"status": "FAILED", "job_id": job_id, "error": "Text extraction failed"}

    update_job_status_in_dynamodb(job_id, "TEXTRACT_COMPLETED", {"extracted_text_s3_placeholder": {"S": "Text stored or passed directly"}}) # Placeholder

    # --- 2. SageMaker Processing ---
    update_job_status_in_dynamodb(job_id, "MODEL_IN_PROGRESS")
    sagemaker_predictions = invoke_sagemaker_endpoint(extracted_text)

    if not sagemaker_predictions:
        logger.error(f"Job {job_id}: Failed to get predictions from SageMaker.")
        update_job_status_in_dynamodb(job_id, "FAILED", {"error_message": {"S": "SageMaker prediction failed."}})
        if callback_url: send_callback(callback_url, {"job_id": job_id, "status": "FAILED", "error": "SageMaker prediction failed."})
        return {"status": "FAILED", "job_id": job_id, "error": "SageMaker prediction failed"}

    # --- 3. Store Results & Finalize ---
    final_result_data = {
        "job_id": job_id,
        "original_s3_uri": s3_uri,
        "document_type": original_document_type, # Use original type
        "textract_job_id": job_details.get("textract_job_id", "N/A_POLLING_MODE"), # If polling, it might be in job_details
        "status": "SUCCEEDED", # Tentative, updated by store_results_s3
        "sagemaker_output": sagemaker_predictions,
        "extracted_clauses": sagemaker_predictions.get("clauses", []), # Using 'clauses' from model_service/models.py
        "risk_assessments": sagemaker_predictions.get("risks", []),   # Using 'risks'
        "summary": sagemaker_predictions.get("summary", {}).get("summary_text"), # Using 'summary_text'
        "processed_at": datetime.datetime.utcnow().isoformat()
    }

    result_s3_uri = store_results_s3(job_id, final_result_data)
    if result_s3_uri:
        logger.info(f"Job {job_id}: Final results stored at {result_s3_uri}")
        update_job_status_in_dynamodb(job_id, "SUCCEEDED", {"result_s3_uri": {"S": result_s3_uri}})
        if callback_url: send_callback(callback_url, {"job_id": job_id, "status": "SUCCEEDED", "result_s3_uri": result_s3_uri})
        return {"status": "SUCCEEDED", "job_id": job_id, "result_s3_uri": result_s3_uri}
    else:
        logger.error(f"Job {job_id}: Failed to store final results to S3.")
        update_job_status_in_dynamodb(job_id, "FAILED", {"error_message": {"S": "Failed to store results in S3."}})
        if callback_url: send_callback(callback_url, {"job_id": job_id, "status": "FAILED", "error": "Failed to store results in S3."})
        return {"status": "FAILED", "job_id": job_id, "error": "Failed to store results in S3"}
