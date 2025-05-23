import logging
import json
import os
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Default SQS queue name, can be overridden by environment variable
DEFAULT_SQS_QUEUE_URL = os.environ.get("TEXTRACT_SQS_QUEUE_URL")
# Default SNS topic ARN, can be overridden by environment variable
DEFAULT_SNS_TOPIC_ARN = os.environ.get("TEXTRACT_SNS_TOPIC_ARN")
# Default IAM role ARN, can be overridden by environment variable
DEFAULT_IAM_ROLE_ARN = os.environ.get("TEXTRACT_IAM_ROLE_ARN")


def get_textract_client(region_name: str = "us-east-1"):
    """
    Initializes and returns a Textract client with a retry mechanism.

    Args:
        region_name (str): AWS region name.

    Returns:
        boto3.client: Textract client.
    """
    retry_config = Config(
        retries={
            "max_attempts": 5,
            "mode": "adaptive",
        }
    )
    return boto3.client("textract", region_name=region_name, config=retry_config)


def get_sqs_client(region_name: str = "us-east-1"):
    """
    Initializes and returns an SQS client.

    Args:
        region_name (str): AWS region name.

    Returns:
        boto3.client: SQS client.
    """
    return boto3.client("sqs", region_name=region_name)


def start_textract_job(
    s3_bucket: str,
    s3_key: str,
    region_name: str = "us-east-1",
    sns_topic_arn: str = DEFAULT_SNS_TOPIC_ARN,
    iam_role_arn: str = DEFAULT_IAM_ROLE_ARN,
    sqs_queue_url: str = DEFAULT_SQS_QUEUE_URL,
) -> str | None:
    """
    Starts an asynchronous Textract job for document text detection.

    Args:
        s3_bucket (str): S3 bucket name where the document is located.
        s3_key (str): S3 key of the document.
        region_name (str): AWS region name.
        sns_topic_arn (str): SNS topic ARN for Textract to publish completion status.
        iam_role_arn (str): IAM role ARN with permissions for Textract to access S3 and SNS.
        sqs_queue_url (str): SQS queue URL to send a message with the JobId.


    Returns:
        str | None: The JobId if the job started successfully, None otherwise.
    """
    if not sns_topic_arn:
        logger.error("SNS_TOPIC_ARN is not configured.")
        return None
    if not iam_role_arn:
        logger.error("IAM_ROLE_ARN is not configured.")
        return None
    if not sqs_queue_url:
        logger.error("SQS_QUEUE_URL is not configured.")
        return None

    textract_client = get_textract_client(region_name)
    sqs_client = get_sqs_client(region_name)
    s3_uri = f"s3://{s3_bucket}/{s3_key}"

    try:
        response = textract_client.start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}},
            NotificationChannel={
                "SNSTopicArn": sns_topic_arn,
                "RoleArn": iam_role_arn,
            },
        )
        job_id = response.get("JobId")
        if job_id:
            logger.info(
                f"Successfully started Textract job for {s3_uri}. JobId: {job_id}"
            )
            # Send a message to SQS with the JobId and original S3 URI
            sqs_message_body = json.dumps({"JobId": job_id, "S3Uri": s3_uri})
            sqs_client.send_message(
                QueueUrl=sqs_queue_url, MessageBody=sqs_message_body
            )
            logger.info(f"Sent message to SQS queue {sqs_queue_url} for JobId {job_id}")
            return job_id
        else:
            logger.error(f"Failed to start Textract job for {s3_uri}. Response: {response}")
            return None
    except ClientError as e:
        logger.error(f"ClientError starting Textract job for {s3_uri}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error starting Textract job for {s3_uri}: {e}")
        return None


def get_textract_results(
    job_id: str, region_name: str = "us-east-1", max_results: int = 1000
) -> list[dict] | None:
    """
    Retrieves the results of a Textract document text detection job.

    Args:
        job_id (str): The JobId of the Textract job.
        region_name (str): AWS region name.
        max_results (int): Maximum number of results to fetch per page.

    Returns:
        list[dict] | None: A list of Textract block dictionaries or None if an error occurs.
    """
    textract_client = get_textract_client(region_name)
    all_blocks = []
    next_token = None

    try:
        while True:
            params = {"JobId": job_id, "MaxResults": max_results}
            if next_token:
                params["NextToken"] = next_token

            response = textract_client.get_document_text_detection(**params)

            job_status = response.get("JobStatus")
            if job_status == "SUCCEEDED":
                blocks = response.get("Blocks", [])
                all_blocks.extend(blocks)
                next_token = response.get("NextToken")
                if not next_token:
                    break  # All pages processed
            elif job_status == "IN_PROGRESS":
                logger.info(f"Textract job {job_id} is still in progress.")
                # Depending on the use case, you might want to wait or return a specific status
                return None # Or raise an exception, or return a status object
            elif job_status in ["FAILED", "PARTIAL_SUCCESS"]:
                logger.error(
                    f"Textract job {job_id} failed or partially succeeded. Status: {job_status}. "
                    f"StatusMessage: {response.get('StatusMessage')}"
                )
                return None
            else:
                logger.warning(f"Textract job {job_id} has an unknown status: {job_status}")
                return None

        logger.info(f"Successfully retrieved {len(all_blocks)} blocks for Textract job {job_id}.")
        # Placeholder for actual result processing
        # process_textract_blocks(all_blocks, job_id)
        return all_blocks

    except ClientError as e:
        logger.error(f"ClientError getting Textract results for job {job_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Textract results for job {job_id}: {e}")
        return None


def process_sqs_message(message: dict, region_name: str = "us-east-1"):
    """
    Processes a message from SQS, assuming it's a Textract completion notification.

    Args:
        message (dict): The SQS message body, expected to be a JSON string
                        from SNS containing Textract job completion details.
        region_name (str): AWS region name.
    """
    try:
        logger.info(f"Processing SQS message: {message}")
        if not isinstance(message, dict) or "Message" not in message:
            logger.error(f"Invalid SQS message format: {message}")
            return

        # The actual message from SNS is a JSON string within the 'Message' field
        sns_message_str = message.get("Message")
        if not sns_message_str:
            logger.error(f"SQS message does not contain 'Message' field: {message}")
            return

        sns_message = json.loads(sns_message_str)

        job_id = sns_message.get("JobId")
        status = sns_message.get("Status")
        s3_object_info = sns_message.get("DocumentLocation", {}).get("S3ObjectName") # s3_key
        s3_bucket_info = sns_message.get("DocumentLocation", {}).get("S3BucketName") # s3_bucket

        if not job_id:
            logger.error(f"No JobId found in SNS message: {sns_message}")
            return

        logger.info(
            f"Received Textract completion notification for JobId: {job_id}, "
            f"Status: {status}, S3Object: s3://{s3_bucket_info}/{s3_object_info}"
        )

        if status == "SUCCEEDED":
            textract_results = get_textract_results(job_id, region_name)
            if textract_results:
                logger.info(f"Successfully processed Textract results for JobId: {job_id}")
                # Placeholder: Add logic to store or further process results
                # For example, save to a database or trigger next step in workflow
                # store_results(job_id, s3_object_info, textract_results)
            else:
                logger.error(f"Failed to retrieve Textract results for JobId: {job_id}")
        elif status == "FAILED":
            error_message = sns_message.get("StatusMessage", "Unknown error")
            logger.error(
                f"Textract job {job_id} for S3 object {s3_object_info} failed. "
                f"Error: {error_message}"
            )
            # Placeholder: Add logic for handling failed jobs (e.g., dead-letter queue, notifications)
        else:
            logger.warning(
                f"Textract job {job_id} for S3 object {s3_object_info} has status {status}. "
                f"Message: {sns_message.get('StatusMessage')}"
            )

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from SQS message: {e}. Message: {message.get('Message', '')}")
    except Exception as e:
        logger.error(f"Unexpected error processing SQS message for job {job_id if 'job_id' in locals() else 'unknown'}: {e}")


if __name__ == "__main__":
    # Example Usage (requires AWS credentials and configured resources)
    # Ensure environment variables for SQS_QUEUE_URL, SNS_TOPIC_ARN, IAM_ROLE_ARN are set
    # and the specified S3 bucket/object exist.

    print("Starting OCR processing example...")

    # 1. Configure your S3 bucket and document key
    test_s3_bucket = os.environ.get("TEST_S3_BUCKET")
    test_s3_key = os.environ.get("TEST_S3_KEY") # e.g., "documents/sample.pdf"

    if not test_s3_bucket or not test_s3_key:
        print(
            "Please set TEST_S3_BUCKET and TEST_S3_KEY environment variables for the example."
        )
    elif not DEFAULT_SQS_QUEUE_URL or not DEFAULT_SNS_TOPIC_ARN or not DEFAULT_IAM_ROLE_ARN:
        print(
            "Please set TEXTRACT_SQS_QUEUE_URL, TEXTRACT_SNS_TOPIC_ARN, and "
            "TEXTRACT_IAM_ROLE_ARN environment variables."
        )
    else:
        print(f"Attempting to start Textract job for s3://{test_s3_bucket}/{test_s3_key}")
        # 2. Start Textract Job
        job_id = start_textract_job(
            s3_bucket=test_s3_bucket,
            s3_key=test_s3_key,
            # region_name="your-aws-region", # Optional, defaults to us-east-1
            # sqs_queue_url="your-sqs-queue-url", # Optional, defaults to env var
            # sns_topic_arn="your-sns-topic-arn", # Optional, defaults to env var
            # iam_role_arn="your-iam-role-arn" # Optional, defaults to env var
        )

        if job_id:
            print(f"Textract job started with JobId: {job_id}")
            print(
                "You would typically wait for an SQS message indicating job completion."
            )
            print(
                "For this example, we are not simulating the SQS message polling. "
                "You can manually check the AWS console for job status and then "
                "use get_textract_results with the JobId if needed, or test "
                "process_sqs_message with a sample SQS message."
            )

            # 3. (Illustrative) Simulating an SQS message processing
            #    In a real scenario, an SQS listener would receive this message.
            #    This is a simplified example of what an SNS notification to SQS might look like.
            sample_sns_message_body = {
                "Type": "Notification",
                "MessageId": "some-message-id",
                "TopicArn": DEFAULT_SNS_TOPIC_ARN,
                "Subject": "Amazon Textract Notification",
                "Message": json.dumps({
                    "JobId": job_id,
                    "Status": "SUCCEEDED",  # or FAILED
                    "API": "StartDocumentTextDetection",
                    "Timestamp": "1678886400000", # Example timestamp
                    "DocumentLocation": {
                        "S3ObjectName": test_s3_key,
                        "S3BucketName": test_s3_bucket,
                    }
                    # "StatusMessage": "Processed successfully." # if SUCCEEDED
                    # "StatusMessage": "An error occurred." # if FAILED
                }),
                "Timestamp": "2023-03-15T12:00:00.000Z", # Example timestamp
                "SignatureVersion": "1",
                # ... other SNS fields
            }
            print(f"\nSimulating processing of an SQS message for JobId: {job_id} (if it were SUCCEEDED)")
            # process_sqs_message(sample_sns_message_body) # This would call get_textract_results

            # 4. (Illustrative) Manually fetch results if job ID is known and job is complete
            # print(f"\nAttempting to fetch results directly for JobId: {job_id}")
            # results = get_textract_results(job_id)
            # if results:
            # print(f"Retrieved {len(results)} blocks.")
            # else:
            # print("Could not retrieve results directly (job might still be in progress or failed).")

        else:
            print(f"Failed to start Textract job for s3://{test_s3_bucket}/{test_s3_key}")

    print("\nOCR processing example finished.")
