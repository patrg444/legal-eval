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
    # The ocr.start_textract_job function is expected to send an SQS message with its own job_id (textract_job_id)
    # and the original s3_uri. Our main job_id here is for the overall API request.
    # We will store this main job_id along with the textract_job_id if needed later for correlation.
    textract_job_id = ocr.start_textract_job(
        s3_bucket=s3_bucket,
        s3_key=s3_key,
        region_name=AWS_REGION,
        # These will be picked up from env vars by the ocr module if not passed,
        # but passing them explicitly for clarity if desired:
        sqs_queue_url=TEXTRACT_SQS_QUEUE_URL,
        sns_topic_arn=TEXTRACT_SNS_TOPIC_ARN,
        iam_role_arn=TEXTRACT_IAM_ROLE_ARN,
    )

    if not textract_job_id:
        logger.error(f"Job {job_id}: Failed to start Textract job for {s3_uri}.")
        response_payload = {
            "job_id": job_id,
            "s3_uri": s3_uri,
            "status": "FAILED",
            "error": "Failed to initiate document processing (OCR).",
        }
        if callback_url:
            send_callback(callback_url, job_id, "FAILED", error_message="Failed to initiate document processing (OCR).")
        return {"statusCode": 500, "body": json.dumps(response_payload)}

    logger.info(f"Job {job_id}: Textract job initiated with textract_job_id: {textract_job_id}. "
                f"SQS message sent by Textract wrapper to queue: {TEXTRACT_SQS_QUEUE_URL}")

    # --- Orchestration Note ---
    # At this point, the Textract job has started. The actual text extraction is asynchronous.
    # A separate Lambda function (e.g., triggered by SQS message from Textract completion via SNS)
    # would handle:
    #   1. Retrieving Textract results (clean text).
    #   2. Invoking SageMaker endpoint with the clean text.
    #   3. Storing the final combined result to S3.
    #   4. Sending a final callback.

    # For THIS SUBTASK, we are simulating the next steps as if Textract was synchronous
    # to demonstrate SageMaker invocation and result storage.
    # In a real-world scenario, this handler would likely return "IN_PROGRESS" now.

    # --- SIMULATION: Assume Textract is complete and text is available ---
    # This is a placeholder. In a real system, this text would come from Textract output
    # processed by another Lambda.
    logger.warning(f"Job {job_id}: SIMULATING Textract completion and SageMaker invocation. "
                   f"This part would typically be in a separate Lambda triggered by Textract SQS message.")
    simulated_extracted_text = f"This is simulated extracted text from {s3_key} for document type {document_type}."

    # --- 2. Invoke SageMaker Endpoint (Simulated Path) ---
    if not SAGEMAKER_ENDPOINT_NAME: # Check here as it's specific to this simulated path
        logger.error(f"Job {job_id}: SAGEMAKER_ENDPOINT_NAME not configured for simulated SageMaker call.")
        # Not failing the whole job here as the Textract part was submitted.
        # The "real" Textract completion handler would face this.
        # For now, we'll just log and not proceed with SageMaker part of simulation.
    else:
        logger.info(f"Job {job_id}: Invoking SageMaker endpoint '{SAGEMAKER_ENDPOINT_NAME}' with simulated text.")
        sagemaker_predictions = invoke_sagemaker_endpoint(simulated_extracted_text)

        if sagemaker_predictions:
            logger.info(f"Job {job_id}: Received predictions from SageMaker.")
            # Structure the final result
            final_result = {
                "job_id": job_id,
                "original_s3_uri": s3_uri,
                "document_type": document_type,
                "textract_job_id": textract_job_id, # For reference
                "status": "COMPLETED_SIMULATED", # Indicate simulation
                "sagemaker_output": sagemaker_predictions,
                # Add other fields as per the defined schema in the issue
                "extracted_clauses": sagemaker_predictions.get("clause_extractions", []),
                "risk_assessments": sagemaker_predictions.get("risk_classifications", []),
                "summary": sagemaker_predictions.get("summary", {}).get("summary"),
            }

            # --- 3. Store Results (Simulated Path) ---
            result_s3_uri = store_results_s3(job_id, final_result)
            if result_s3_uri:
                logger.info(f"Job {job_id}: Final results stored at {result_s3_uri}")
                final_status_for_callback = "COMPLETED_SIMULATED"
            else:
                logger.error(f"Job {job_id}: Failed to store final results to S3.")
                final_status_for_callback = "FAILED_SIMULATED_STORAGE"
                # Potentially update final_result status before callback if needed

            # --- 4. Callback (Simulated Path) ---
            if callback_url:
                send_callback(callback_url, job_id, final_status_for_callback, result_s3_uri=result_s3_uri)
        else:
            logger.error(f"Job {job_id}: Failed to get predictions from SageMaker for simulated text.")
            if callback_url: # Send callback indicating SageMaker failure in simulated path
                send_callback(callback_url, job_id, "FAILED_SIMULATED_SAGEMAKER",
                              error_message="SageMaker processing failed in simulated path.")
    # End of SIMULATION block

    # --- Return Initial Response ---
    # The primary Lambda returns "IN_PROGRESS" because Textract is async.
    # The callback (if provided) for the *initial* request might also indicate "IN_PROGRESS".
    # A more refined callback strategy could be used for the initial vs. final notification.
    initial_response_payload = {
        "job_id": job_id,
        "s3_uri": s3_uri,
        "textract_job_id": textract_job_id,
        "status": "IN_PROGRESS",
        "message": "Document processing initiated. Textract job started.",
        "simulation_note": "SageMaker invocation and result storage are SIMULATED in this response path "
                           "for demonstration. Actual processing is asynchronous."
    }
    if callback_url: # Optional: send an initial "IN_PROGRESS" callback
        send_callback(callback_url, job_id, "IN_PROGRESS", error_message=None)


    return {
        "statusCode": 202, # Accepted
        "body": json.dumps(initial_response_payload),
    }


# --- Example Usage (for local testing, not part of Lambda deployment) ---
if __name__ == "__main__":
    # Mock event and context
    sample_event = {
        "document_type": "NDA",
        "s3_uri": "s3://your-actual-test-bucket/sample-document.pdf", # Replace with a real S3 URI if testing Textract
        "callback_url": "https://webhook.site/your-unique-id" # Replace with a real callback URL (e.g., from webhook.site)
    }
    sample_context = {}

    # Set environment variables for local testing:
    os.environ["AWS_REGION"] = "us-east-1" # Your AWS region
    os.environ["RESULT_S3_BUCKET"] = "your-results-s3-bucket-name" # Replace with your bucket
    os.environ["SAGEMAKER_ENDPOINT_NAME"] = "your-sagemaker-endpoint-name" # Replace if testing SageMaker
    os.environ["TEXTRACT_SQS_QUEUE_URL"] = "your-textract-sqs-queue-url" # From previous setup
    os.environ["TEXTRACT_SNS_TOPIC_ARN"] = "your-textract-sns-topic-arn" # From previous setup
    os.environ["TEXTRACT_IAM_ROLE_ARN"] = "your-textract-iam-role-arn"   # From previous setup

    # Create dummy S3 bucket and object for Textract if they don't exist (requires AWS CLI or boto3 setup)
    # For a quick local test without actual AWS calls for Textract/Sagemaker, you might need to
    # further mock the boto3 clients within the functions if ocr.start_textract_job and
    # invoke_sagemaker_endpoint are called.
    # The current code will attempt to make real AWS calls if not mocked.

    print("--- Running Local Test ---")
    if not os.environ.get("RESULT_S3_BUCKET") or \
       not os.environ.get("TEXTRACT_SQS_QUEUE_URL") or \
       not os.environ.get("TEXTRACT_SNS_TOPIC_ARN") or \
       not os.environ.get("TEXTRACT_IAM_ROLE_ARN"):
        print("Warning: Some environment variables for AWS resources are not set. "
              "Full functionality testing requires these to be configured.")
        print("Proceeding with potentially limited local test...")


    # To prevent actual AWS calls during a simple structural test, you could temporarily
    # assign mock functions or use a mocking library like `unittest.mock`.
    # For this example, we'll let it run but it will fail if resources aren't available.

    # A more robust local test might involve:
    # from unittest.mock import patch
    # @patch('src.preprocess.ocr.start_textract_job')
    # @patch('boto3.client')
    # def run_test(mock_boto_client, mock_start_textract):
    #     mock_s3 = mock_boto_client.return_value
    #     mock_sagemaker = mock_boto_client.return_value
    #     mock_start_textract.return_value = "fake-textract-job-id"
    #     mock_sagemaker.invoke_endpoint.return_value = {
    #         "Body": io.BytesIO(json.dumps({"summary": "mock summary"}).encode())
    #     }
    #     mock_s3.put_object.return_value = {}
    #     response = process_contract_request(sample_event, sample_context)
    #     print("\n--- Lambda Response ---")
    #     print(json.dumps(response, indent=2))
    # run_test()

    # Direct call for now (will make AWS calls if env vars are set and valid)
    response = process_contract_request(sample_event, sample_context)
    print("\n--- Lambda Response ---")
    print(json.dumps(response, indent=2))
    print("\nNote: If testing actual AWS integration, ensure the S3 URI, SQS, SNS, IAM Role, and SageMaker endpoint are correctly configured.")
