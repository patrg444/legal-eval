import os
import json
import logging
import boto3
import time
from datetime import datetime, timezone
import requests # For callback URL

# Assuming ocr.py and its Textract interaction logic is available
# Need to adjust imports if ocr.py functions are refactored or directly used.
# For now, let's assume we might use some helpers or call Textract Get* directly.
from src.preprocess import ocr # For get_textract_results, or direct boto3 calls

# --- Configuration ---
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
RESULT_S3_BUCKET = os.environ.get("RESULT_S3_BUCKET")
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME")
DYNAMODB_JOBS_TABLE_NAME = os.environ.get("DYNAMODB_JOBS_TABLE_NAME")

# Configure logging
logger = logging.getLogger(__name__)
# Ensure logging is configured (e.g., by Lambda runtime or explicitly here)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)


s3_client = boto3.client("s3", region_name=AWS_REGION)
dynamodb_resource = boto3.resource("dynamodb", region_name=AWS_REGION)
sagemaker_runtime_client = boto3.client("sagemaker-runtime", region_name=AWS_REGION)
textract_client = ocr.get_textract_client(region_name=AWS_REGION) # Use configured Textract client

def update_job_status_in_db(job_id: str, new_status: str, updates: dict = None):
    """Updates the job status and other attributes in DynamoDB."""
    if not DYNAMODB_JOBS_TABLE_NAME:
        logger.error("DYNAMODB_JOBS_TABLE_NAME not configured. Cannot update job status.")
        return None

    table = dynamodb_resource.Table(DYNAMODB_JOBS_TABLE_NAME)
    timestamp = datetime.now(timezone.utc).isoformat()

    update_expression_parts = ["SET #status = :status", "#updated_at = :updated_at"]
    expression_attribute_values = {
        ":status": new_status,
        ":updated_at": timestamp,
    }
    expression_attribute_names = {
        "#status": "status", # 'status' is a reserved keyword in DynamoDB
        "#updated_at": "updated_at"
    }

    if updates:
        for key, value in updates.items():
            attr_name_placeholder = f"#{key}"
            attr_value_placeholder = f":{key}"
            update_expression_parts.append(f"{attr_name_placeholder} = {attr_value_placeholder}")
            expression_attribute_names[attr_name_placeholder] = key
            expression_attribute_values[attr_value_placeholder] = value
    
    update_expression = ", ".join(update_expression_parts)

    try:
        logger.info(f"Updating job {job_id} in DynamoDB. Status: {new_status}, Updates: {updates}")
        response = table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues="ALL_NEW",  # Returns all attributes of the item after the update
        )
        logger.info(f"DynamoDB update successful for job {job_id}. Response: {response}")
        return response.get("Attributes")
    except Exception as e:
        logger.error(f"Error updating job {job_id} in DynamoDB: {e}", exc_info=True)
        return None

def get_job_details_from_db(job_id: str) -> dict | None:
    """Retrieves job details from DynamoDB."""
    if not DYNAMODB_JOBS_TABLE_NAME:
        logger.error("DYNAMODB_JOBS_TABLE_NAME not configured. Cannot get job details.")
        return None
    table = dynamodb_resource.Table(DYNAMODB_JOBS_TABLE_NAME)
    try:
        response = table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        if item:
            logger.info(f"Retrieved job details for {job_id}: {item}")
            return item
        else:
            logger.warning(f"Job {job_id} not found in DynamoDB.")
            return None
    except Exception as e:
        logger.error(f"Error getting job {job_id} from DynamoDB: {e}", exc_info=True)
        return None

def invoke_sagemaker(text: str, job_id: str) -> dict | None:
    """Invokes SageMaker endpoint and returns the structured prediction."""
    if not SAGEMAKER_ENDPOINT_NAME:
        logger.error(f"Job {job_id}: SAGEMAKER_ENDPOINT_NAME not configured.")
        update_job_status_in_db(job_id, "FAILED", {"error_message": "SageMaker endpoint not configured."})
        return None
    try:
        logger.info(f"Job {job_id}: Invoking SageMaker endpoint {SAGEMAKER_ENDPOINT_NAME}.")
        payload = {"text": text, "tasks": ["clause_extraction", "risk_classification", "summarization"]}
        response = sagemaker_runtime_client.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT_NAME,
            ContentType="application/json",
            Body=json.dumps(payload),
            Accept="application/json"
        )
        response_body = json.loads(response["Body"].read().decode())
        logger.info(f"Job {job_id}: SageMaker invocation successful.")
        return response_body
    except Exception as e:
        logger.error(f"Job {job_id}: Error invoking SageMaker endpoint: {e}", exc_info=True)
        update_job_status_in_db(job_id, "FAILED", {"error_message": f"SageMaker invocation error: {str(e)}"})
        return None

def store_results_to_s3(job_id: str, result_data: dict, original_s3_uri: str, document_type: str) -> str | None:
    """Stores the final JSON result to the designated S3 bucket."""
    if not RESULT_S3_BUCKET:
        logger.error(f"Job {job_id}: RESULT_S3_BUCKET not configured.")
        update_job_status_in_db(job_id, "FAILED", {"error_message": "Result S3 bucket not configured."})
        return None

    # Enhance result_data with more metadata before storing
    final_result_payload = {
        "job_id": job_id,
        "original_s3_uri": original_s3_uri,
        "document_type": document_type,
        "processing_timestamp": datetime.now(timezone.utc).isoformat(),
        "analysis_results": result_data # This now nests the SageMaker output
    }
    
    result_key = f"results/{job_id}/output.json"
    try:
        s3_client.put_object(
            Bucket=RESULT_S3_BUCKET,
            Key=result_key,
            Body=json.dumps(final_result_payload, indent=2),
            ContentType="application/json",
        )
        result_s3_uri = f"s3://{RESULT_S3_BUCKET}/{result_key}"
        logger.info(f"Job {job_id}: Successfully stored results in S3: {result_s3_uri}")
        return result_s3_uri
    except Exception as e:
        logger.error(f"Job {job_id}: Error storing results to S3 (s3://{RESULT_S3_BUCKET}/{result_key}): {e}", exc_info=True)
        update_job_status_in_db(job_id, "FAILED", {"error_message": f"S3 storage error: {str(e)}"})
        return None

def send_callback_notification(callback_url: str, job_id: str, status: str, result_s3_uri: str | None = None, error_message: str | None = None):
    """Sends an HTTP POST request to the callback_url."""
    if not callback_url:
        logger.info(f"Job {job_id}: No callback URL provided. Skipping notification.")
        return

    payload = {
        "job_id": job_id,
        "status": status,
    }
    if result_s3_uri:
        payload["result_s3_uri"] = result_s3_uri
    if error_message:
        payload["error_message"] = error_message # Consistent naming

    try:
        logger.info(f"Job {job_id}: Sending callback to {callback_url} with payload: {payload}")
        response = requests.post(callback_url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Job {job_id}: Callback sent successfully to {callback_url}. Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Job {job_id}: Failed to send callback to {callback_url}: {e}")
    except Exception as e:
        logger.error(f"Job {job_id}: Unexpected error sending callback to {callback_url}: {e}")


def handle_textract_completion(sns_message: dict):
    """Handles Textract completion notifications."""
    textract_job_id = sns_message.get("JobId")
    textract_status = sns_message.get("Status")
    # The original API job_id needs to be retrieved.
    # Textract's SNS message itself doesn't directly carry our custom job_id.
    # Option 1: If `JobTag` was used with StartDocumentTextDetection and Textract includes it in SNS. (Check Textract docs)
    # Option 2: Query DynamoDB for an item matching `textract_job_id`. This requires storing `textract_job_id`
    #           when `StartDocumentTextDetection` is called by the main handler.
    # For this implementation, we'll assume the main handler needs to store `textract_job_id` associated with its `job_id`.
    # Let's assume `job_tag_or_id_from_sns` is how we get our internal `job_id`.
    # For now, we'll mock this lookup or assume JobTag contains our job_id.
    
    # Let's assume `JobTag` was set to our `job_id` when calling Textract
    api_job_id = sns_message.get("JobTag") 

    if not api_job_id:
        logger.error(f"Could not determine API job_id from Textract SNS message for TextractJobId: {textract_job_id}. SNS Message: {sns_message}")
        # Potentially send to a DLQ or log for manual intervention
        return

    logger.info(f"Processing Textract completion for API Job ID: {api_job_id}, Textract Job ID: {textract_job_id}, Status: {textract_status}")

    job_details = get_job_details_from_db(api_job_id)
    if not job_details:
        logger.error(f"Job details not found in DynamoDB for API Job ID: {api_job_id}. Cannot proceed.")
        return

    if textract_status == "SUCCEEDED":
        logger.info(f"Job {api_job_id}: Textract job SUCCEEDED. Fetching results.")
        # Use the ocr.get_textract_results function or similar logic here
        # For simplicity, let's assume it returns a string of all concatenated line text
        textract_blocks = ocr.get_textract_results(job_id=textract_job_id, region_name=AWS_REGION) # from ocr.py

        if textract_blocks is None: # Error already logged by get_textract_results
            update_job_status_in_db(api_job_id, "FAILED", {"error_message": "Failed to retrieve Textract results."})
            send_callback_notification(job_details.get("callback_url"), api_job_id, "FAILED", error_message="Failed to retrieve Textract results.")
            return

        # Process blocks to get clean text (simplified example)
        extracted_text = ""
        for block in textract_blocks:
            if block.get("BlockType") == "LINE":
                extracted_text += block.get("Text", "") + "\n"
        
        if not extracted_text.strip():
            logger.warning(f"Job {api_job_id}: Textract processing resulted in empty text.")
            update_job_status_in_db(api_job_id, "FAILED", {"error_message": "Textract OCR resulted in empty text."})
            send_callback_notification(job_details.get("callback_url"), api_job_id, "FAILED", error_message="Textract OCR resulted in empty text.")
            return

        logger.info(f"Job {api_job_id}: Text extracted successfully. Length: {len(extracted_text)}. Updating DB status.")
        update_job_status_in_db(api_job_id, "TEXTRACT_COMPLETED", {"extracted_text_preview": extracted_text[:200]}) # Store preview or S3 link to full text

        # --- Invoke SageMaker ---
        logger.info(f"Job {api_job_id}: Invoking SageMaker for further processing.")
        sagemaker_output = invoke_sagemaker(extracted_text, api_job_id) # invoke_sagemaker handles its own DDB error updates

        if sagemaker_output:
            # Store final results to S3
            result_s3_uri = store_results_to_s3(api_job_id, sagemaker_output, job_details.get("s3_uri"), job_details.get("document_type"))
            if result_s3_uri:
                final_status = "SUCCEEDED"
                update_job_status_in_db(api_job_id, final_status, {"result_s3_uri": result_s3_uri, "error_message": None}) # Clear any previous error
                send_callback_notification(job_details.get("callback_url"), api_job_id, final_status, result_s3_uri=result_s3_uri)
            else:
                # store_results_to_s3 already updated DDB and logged
                send_callback_notification(job_details.get("callback_url"), api_job_id, "FAILED", error_message="Failed to store final results to S3.")
        else:
            # invoke_sagemaker already updated DDB and logged
            send_callback_notification(job_details.get("callback_url"), api_job_id, "FAILED", error_message="SageMaker processing failed.")

    elif textract_status == "FAILED":
        logger.error(f"Job {api_job_id}: Textract job FAILED. StatusMessage: {sns_message.get('StatusMessage', 'N/A')}")
        error_msg = f"Textract processing failed: {sns_message.get('StatusMessage', 'Unknown Textract error')}"
        update_job_status_in_db(api_job_id, "FAILED", {"error_message": error_msg})
        send_callback_notification(job_details.get("callback_url"), api_job_id, "FAILED", error_message=error_msg)
    else:
        logger.warning(f"Job {api_job_id}: Textract job status is '{textract_status}'. No action taken by this handler for this status.")
        # Optionally update DynamoDB with this intermediate status if needed
        update_job_status_in_db(api_job_id, f"TEXTRACT_{textract_status}", {"textract_status_message": sns_message.get('StatusMessage')})


# Placeholder for SageMaker completion if it were truly async with SNS
# def handle_sagemaker_completion(sns_message: dict):
#     api_job_id = sns_message.get("api_job_id") # Assuming SageMaker SNS includes our job_id
#     logger.info(f"Processing SageMaker completion for API Job ID: {api_job_id}")
#     # ... logic to get SageMaker results, store to S3, update DynamoDB, send callback ...
#     pass


def handle_processing_completion(event, context):
    """
    Lambda handler triggered by SNS notifications for async task completions.
    """
    logger.info(f"Received SNS event: {json.dumps(event, indent=2)}")

    for record in event.get("Records", []):
        sns_notification = record.get("Sns")
        if not sns_notification:
            logger.warning("SNS data not found in record. Skipping.")
            continue

        message_str = sns_notification.get("Message")
        if not message_str:
            logger.warning("No 'Message' found in SNS notification. Skipping.")
            continue

        try:
            message_content = json.loads(message_str)
            logger.info(f"Parsed SNS message content: {message_content}")

            # Determine the source of the message (e.g., Textract, SageMaker)
            # Textract SNS messages typically include "JobId", "Status", "API", "DocumentLocation"
            # SageMaker async inference SNS might have "invocations-status", "inferenceId", etc.
            # This routing logic needs to be robust.
            if "JobId" in message_content and "API" in message_content and "StartDocumentTextDetection" in message_content.get("API", ""):
                logger.info("Detected Textract completion message.")
                handle_textract_completion(message_content)
            # elif "invocations-status" in message_content: # Example for SageMaker async if it sends SNS
            #     logger.info("Detected SageMaker completion message (placeholder).")
            #     handle_sagemaker_completion(message_content)
            else:
                logger.warning(f"Unknown SNS message structure: {message_content}. Cannot process.")

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from SNS message string: {e}. Message string: '{message_str}'")
        except Exception as e:
            # Catch-all for unexpected errors during message processing
            logger.error(f"Unexpected error processing SNS message: {e}", exc_info=True)
            # Depending on the error, you might try to extract an API Job ID and update DynamoDB to FAILED.
            # This is complex if the message structure is unknown or malformed.

    return {"statusCode": 200, "body": json.dumps("Processing complete messages handled.")}

# Example Usage (for local testing of individual functions, not full handler)
if __name__ == "__main__":
    # Mock environment variables for local testing
    os.environ["AWS_REGION"] = "us-east-1"
    os.environ["RESULT_S3_BUCKET"] = "your-local-results-bucket"
    os.environ["SAGEMAKER_ENDPOINT_NAME"] = "your-local-sagemaker-endpoint"
    os.environ["DYNAMODB_JOBS_TABLE_NAME"] = "contractintel-jobs" # Match your local table

    # Example: Test Textract completion handling
    sample_textract_sns_message = {
        "JobId": "textractjob12345",
        "Status": "SUCCEEDED",
        "API": "StartDocumentTextDetection",
        "JobTag": "api-job-67890", # Assuming JobTag carries our API job_id
        "DocumentLocation": {
            "S3ObjectName": "sample.pdf",
            "S3BucketName": "input-bucket"
        }
    }
    # To test this locally, you'd need to mock boto3 calls within handle_textract_completion
    # (e.g., get_job_details_from_db, ocr.get_textract_results, invoke_sagemaker, etc.)
    # print("Testing Textract completion locally (requires extensive mocking)...")
    # handle_textract_completion(sample_textract_sns_message)

    # Example: Test DynamoDB update
    # test_job_id = "test-job-" + str(uuid.uuid4())
    # print(f"Testing DynamoDB operations for job: {test_job_id}")
    # table = dynamodb_resource.Table(DYNAMODB_JOBS_TABLE_NAME)
    # table.put_item(Item={
    #     "job_id": test_job_id, "status": "PENDING", "timestamp": datetime.now(timezone.utc).isoformat(),
    #     "s3_uri": "s3://bucket/key.pdf", "callback_url": "http://example.com/callback"
    # })
    # print(get_job_details_from_db(test_job_id))
    # update_job_status_in_db(test_job_id, "TEXTRACT_COMPLETED", {"textract_job_id": "txt123"})
    # print(get_job_details_from_db(test_job_id))
    # update_job_status_in_db(test_job_id, "SUCCEEDED", {"result_s3_uri": "s3://res/out.json", "error_message": None})
    # print(get_job_details_from_db(test_job_id))
    pass
