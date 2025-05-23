import os
import json
import logging
import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger(__name__)
# Ensure logging is configured (e.g., by Lambda runtime or explicitly here)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
    logger.setLevel(logging.INFO)

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_JOBS_TABLE_NAME = os.environ.get("DYNAMODB_JOBS_TABLE_NAME")

dynamodb_client = boto3.client("dynamodb", region_name=AWS_REGION)

def get_contract_status(event, context):
    """
    Lambda handler for GET /v1/contracts/{job_id}
    Retrieves job status from DynamoDB.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    if not DYNAMODB_JOBS_TABLE_NAME:
        logger.error("Configuration error: DYNAMODB_JOBS_TABLE_NAME is not set.")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Server configuration error."}),
            "headers": {"Content-Type": "application/json"},
        }

    try:
        # API Gateway path parameters are in 'pathParameters'
        job_id = event.get("pathParameters", {}).get("job_id")
        if not job_id:
            logger.warning("Missing 'job_id' in pathParameters.")
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing 'job_id' in path."}),
                "headers": {"Content-Type": "application/json"},
            }

        logger.info(f"Querying DynamoDB for job_id: {job_id}")
        
        response = dynamodb_client.get_item(
            TableName=DYNAMODB_JOBS_TABLE_NAME,
            Key={"job_id": {"S": job_id}} # Assuming job_id is a String ('S')
        )

        item = response.get("Item")

        if not item:
            logger.info(f"Job {job_id} not found in DynamoDB.")
            return {
                "statusCode": 404,
                "body": json.dumps({"error": f"Job {job_id} not found."}),
                "headers": {"Content-Type": "application/json"},
            }

        logger.info(f"Job {job_id} found. Item: {item}")

        # Construct response based on JobStatusResponse schema
        # DynamoDB stores items with type descriptors (e.g., {"S": "value"})
        # We need to deserialize these into plain values.
        
        status_response = {
            "job_id": item.get("job_id", {}).get("S"),
            "status": item.get("status", {}).get("S"), # 'status' is a reserved word, ensure it was stored with an alias or correctly.
                                                      # Assuming it's stored as 'status' string.
            "result_s3_uri": item.get("result_s3_uri", {}).get("S"), # Optional
            "error_message": item.get("error_message", {}).get("S"),   # Optional
            "created_at": item.get("created_at", {}).get("S"),       # Optional, if stored
            "updated_at": item.get("updated_at", {}).get("S"),       # Optional, if stored
            "textract_job_id": item.get("textract_job_id", {}).get("S") # Optional
        }
        
        # Clean up None values for optional fields to match Pydantic model behavior (exclude if None)
        cleaned_status_response = {k: v for k, v in status_response.items() if v is not None}


        return {
            "statusCode": 200,
            "body": json.dumps(cleaned_status_response),
            "headers": {"Content-Type": "application/json"},
        }

    except ClientError as e:
        logger.error(f"DynamoDB ClientError: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Error accessing job data."}),
            "headers": {"Content-Type": "application/json"},
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "An unexpected server error occurred."}),
            "headers": {"Content-Type": "application/json"},
        }

if __name__ == "__main__":
    # Example local test (requires DYNAMODB_JOBS_TABLE_NAME to be set)
    # And a DynamoDB instance (local or AWS) with the table and an item.
    # os.environ["DYNAMODB_JOBS_TABLE_NAME"] = "contractintel-jobs" # Your table name
    # os.environ["AWS_REGION"] = "us-east-1"
    
    # sample_event_get = {
    #     "pathParameters": {
    #         "job_id": "your-test-job-id" # Replace with an actual job_id in your table
    #     }
    # }
    # response = get_contract_status(sample_event_get, {})
    # print(json.dumps(response, indent=2))
    pass
