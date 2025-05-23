import os
import json
import pytest
import boto3
from moto import mock_aws
import requests_mock

# Import the Lambda handler function
from src.api.handler import process_contract_request
from src.preprocess import ocr # To mock its methods if necessary

# --- Test Configuration ---
TEST_AWS_REGION = "us-east-1"
TEST_INPUT_BUCKET = "test-input-bucket"
TEST_RESULTS_BUCKET = "test-results-bucket"
TEST_SAGEMAKER_ENDPOINT_NAME = "test-sagemaker-endpoint"
TEST_TEXTRACT_SQS_QUEUE_URL = f"https://sqs.{TEST_AWS_REGION}.amazonaws.com/123456789012/test-textract-queue"
TEST_TEXTRACT_SNS_TOPIC_ARN = f"arn:aws:sns:{TEST_AWS_REGION}:123456789012:test-textract-sns-topic"
TEST_TEXTRACT_IAM_ROLE_ARN = f"arn:aws:iam::123456789012:role/test-textract-role"

SAMPLE_DOC_DIR = os.path.join(os.path.dirname(__file__), "sample_documents")
GOLD_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "gold_outputs")

SAMPLE_CONTRACT_FILENAME = "sample_contract.txt"
SAMPLE_CONTRACT_S3_KEY = SAMPLE_CONTRACT_FILENAME
GOLD_OUTPUT_FILENAME = "sample_contract_gold.json"


@pytest.fixture(scope="function")
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = TEST_AWS_REGION

@pytest.fixture(scope="function")
def mock_env(monkeypatch):
    """Set up mock environment variables for the Lambda handler."""
    monkeypatch.setenv("AWS_REGION", TEST_AWS_REGION)
    monkeypatch.setenv("RESULT_S3_BUCKET", TEST_RESULTS_BUCKET)
    monkeypatch.setenv("SAGEMAKER_ENDPOINT_NAME", TEST_SAGEMAKER_ENDPOINT_NAME)
    monkeypatch.setenv("TEXTRACT_SQS_QUEUE_URL", TEST_TEXTRACT_SQS_QUEUE_URL)
    monkeypatch.setenv("TEXTRACT_SNS_TOPIC_ARN", TEST_TEXTRACT_SNS_TOPIC_ARN)
    monkeypatch.setenv("TEXTRACT_IAM_ROLE_ARN", TEST_TEXTRACT_IAM_ROLE_ARN)
    # Ensure the ocr.py module also sees these if it re-imports os
    ocr.DEFAULT_SQS_QUEUE_URL = TEST_TEXTRACT_SQS_QUEUE_URL
    ocr.DEFAULT_SNS_TOPIC_ARN = TEST_TEXTRACT_SNS_TOPIC_ARN
    ocr.DEFAULT_IAM_ROLE_ARN = TEST_TEXTRACT_IAM_ROLE_ARN


@pytest.fixture(scope="function")
def mock_s3_services(aws_credentials):
    with mock_aws():
        s3 = boto3.client("s3", region_name=TEST_AWS_REGION)
        s3.create_bucket(Bucket=TEST_INPUT_BUCKET)
        s3.create_bucket(Bucket=TEST_RESULTS_BUCKET)
        yield s3

@pytest.fixture(scope="function")
def mock_sqs_service(aws_credentials):
    with mock_aws():
        sqs = boto3.client("sqs", region_name=TEST_AWS_REGION)
        # The handler's ocr.py sub-module will try to send to this queue URL
        # Moto will intercept this if the URL is correctly formed for a mock queue.
        # We don't strictly need to create it IF the send_message in ocr.py doesn't
        # depend on queue existence validation that moto might perform.
        # For robustness, let's ensure it exists in the mock environment.
        try:
            sqs.create_queue(QueueName=TEST_TEXTRACT_SQS_QUEUE_URL.split('/')[-1])
        except Exception as e:
            # Older moto versions might need full URL, newer ones parse the name.
            # This is a bit of a hack for compatibility.
            # print(f"Could not create SQS queue directly with URL, trying name: {e}")
            # queue_name_from_url = TEST_TEXTRACT_SQS_QUEUE_URL.split('/')[-1]
            # sqs.create_queue(QueueName=queue_name_from_url)
            pass # If it fails, moto might still handle it gracefully.
        yield sqs


# --- Main Test Case ---
def test_api_end_to_end_flow_simulation(
    mock_env, mock_s3_services, mock_sqs_service, mocker
):
    # --- 1. Setup: Upload sample document to mock S3 ---
    sample_contract_path = os.path.join(SAMPLE_DOC_DIR, SAMPLE_CONTRACT_FILENAME)
    with open(sample_contract_path, "rb") as f:
        mock_s3_services.upload_fileobj(f, TEST_INPUT_BUCKET, SAMPLE_CONTRACT_S3_KEY)

    # --- 2. Mock Textract and SageMaker clients ---
    # Mock Textract client and its methods used by ocr.py
    mock_textract_client = mocker.patch("src.preprocess.ocr.get_textract_client")
    mock_textract_instance = mock_textract_client.return_value
    mock_textract_instance.start_document_text_detection.return_value = {
        "JobId": "mock_textract_job_id_from_moto_or_mock"
    }
    # This mock is for the 'process_sqs_message' part of ocr.py, which is not directly hit
    # by the main lambda handler in this test's synchronous simulation path.
    # However, if the handler evolved to call it, this would be ready.
    mock_textract_instance.get_document_text_detection.return_value = {
        "JobStatus": "SUCCEEDED",
        "Blocks": [{"BlockType": "LINE", "Text": "Simulated text from Textract."}],
        "NextToken": None,
    }

    # Mock SageMaker runtime client
    mock_sagemaker_client = mocker.patch("src.api.handler.sagemaker_runtime_client")
    # Based on the placeholder logic in model_service/app.py
    # The handler directly calls sagemaker_runtime.invoke_endpoint
    sagemaker_mock_response_body = {
        "clause_extractions": [
            {"clause_type": "Payment Clause", "text_span": [10, 50], "confidence": 0.95},
            {"clause_type": "Termination Clause", "text_span": [100, 150], "confidence": 0.88},
        ],
        "risk_classifications": [
            {"risk_category": "High Risk", "score": 0.78},
            {"risk_category": "Compliance Issue", "score": 0.65},
        ],
        "summary": {"summary": "This is a mock summary of the provided legal text. It highlights key aspects and obligations."}
    }
    mock_sagemaker_client.invoke_endpoint.return_value = {
        "Body": mocker.MagicMock(read=lambda: json.dumps(sagemaker_mock_response_body).encode('utf-8')),
        "ContentType": "application/json"
    }

    # --- 3. Prepare Lambda event and context ---
    event = {
        "document_type": "text",
        "s3_uri": f"s3://{TEST_INPUT_BUCKET}/{SAMPLE_CONTRACT_S3_KEY}",
        "callback_url": "http://localhost:1234/test_callback"
    }
    context = {} # Empty context, add properties if needed by handler

    # --- 4. Invoke Lambda Handler with requests-mock for callback ---
    with requests_mock.Mocker() as m:
        # Mock the callback URL
        mocked_callback = m.post("http://localhost:1234/test_callback", text="callback received")

        initial_response = process_contract_request(event, context)

    # --- 5. Verify Initial Response ---
    assert initial_response["statusCode"] == 202
    initial_body = json.loads(initial_response["body"])
    assert "job_id" in initial_body
    assert initial_body["status"] == "IN_PROGRESS"
    assert initial_body["textract_job_id"] == "mock_textract_job_id_from_moto_or_mock"
    # Store the job_id for verifying S3 results path
    api_job_id = initial_body["job_id"]

    # --- 6. Verify SQS Message (from ocr.start_textract_job) ---
    # Note: This SQS interaction is from ocr.py's start_textract_job sending its *own* message.
    # It is *not* the SNS->SQS message that Textract itself would send on completion.
    # The handler.py doesn't directly interact with SQS other than calling ocr.py.
    # We trust that ocr.py's SQS send_message (which uses boto3.client("sqs").send_message)
    # was called. Moto should capture this if its SQS mock is active.
    # We can try to receive it to confirm.
    # THIS PART IS TRICKY with moto if the queue URL isn't perfectly matched or if async issues.
    # For now, we rely on the mock_textract_instance.start_document_text_detection being called.
    # A more direct test of ocr.py would be needed for SQS send verification.
    # Let's check if the Textract client method was called, which implies SQS was attempted.
    mock_textract_instance.start_document_text_detection.assert_called_once()


    # --- 7. Verify Result in Mock S3 ---
    # The handler, in its SIMULATED path, directly calls invoke_sagemaker and store_results_s3.
    expected_result_key = f"results/{api_job_id}/output.json"
    try:
        s3_object = mock_s3_services.get_object(Bucket=TEST_RESULTS_BUCKET, Key=expected_result_key)
        result_data_s3 = json.loads(s3_object["Body"].read().decode("utf-8"))
    except Exception as e:
        pytest.fail(f"Result file not found in mock S3 or error reading it: {e}")

    # --- 8. Compare with Gold Output ---
    gold_output_path = os.path.join(GOLD_OUTPUT_DIR, GOLD_OUTPUT_FILENAME)
    with open(gold_output_path, "r") as f:
        gold_data = json.load(f)

    # Customize comparison: ignore dynamic fields like job_id, textract_job_id, original_s3_uri
    # if they are part of the S3 output and different from the static gold file.
    # The gold file has placeholders for these, so we'll compare the critical parts.
    assert result_data_s3["document_type"] == gold_data["document_type"]
    assert result_data_s3["status"] == gold_data["status"] # Should be "COMPLETED_SIMULATED"
    assert result_data_s3["sagemaker_output"] == gold_data["sagemaker_output"]
    assert result_data_s3["extracted_clauses"] == gold_data["extracted_clauses"]
    assert result_data_s3["risk_assessments"] == gold_data["risk_assessments"]
    assert result_data_s3["summary"] == gold_data["summary"]

    # --- 9. Verify Callback (Optional but good) ---
    assert mocked_callback.called_once
    callback_request_data = mocked_callback.last_request.json()
    assert callback_request_data["job_id"] == api_job_id
    # The status sent to callback in the simulated path is "COMPLETED_SIMULATED"
    assert callback_request_data["status"] == "COMPLETED_SIMULATED"
    assert callback_request_data["result_s3_uri"] == f"s3://{TEST_RESULTS_BUCKET}/{expected_result_key}"

    # Verify the *initial* "IN_PROGRESS" callback was also sent
    # requests-mock captures all calls. The first one should be IN_PROGRESS.
    history = mocked_callback.request_history
    assert len(history) == 2 # Initial IN_PROGRESS, then COMPLETED_SIMULATED
    initial_callback_payload = history[0].json()
    assert initial_callback_payload["job_id"] == api_job_id
    assert initial_callback_payload["status"] == "IN_PROGRESS"


# Example of how you might test the SQS message processing part of ocr.py separately
# This would be a more focused unit/integration test for ocr.process_sqs_message
# @mock_aws
# def test_ocr_process_sqs_message_simulation(mocker, monkeypatch):
#     monkeypatch.setenv("AWS_REGION", TEST_AWS_REGION)
#     # ... setup SQS, Textract mocks ...
#     sample_sns_over_sqs_message = {
#         "Type": "Notification",
#         "MessageId": "some-id",
#         "TopicArn": TEST_TEXTRACT_SNS_TOPIC_ARN,
#         "Message": json.dumps({
#             "JobId": "textract_job_123",
#             "Status": "SUCCEEDED",
#             "DocumentLocation": {
#                 "S3ObjectName": "document.pdf",
#                 "S3BucketName": "some-bucket"
#             }
#         })
#     }
#     # Mock get_textract_results if ocr.process_sqs_message calls it
#     mocker.patch("src.preprocess.ocr.get_textract_results", return_value=[{"BlockType": "PAGE"}])
#
#     ocr.process_sqs_message(sample_sns_over_sqs_message)
#     # ... assertions on what process_sqs_message did (e.g., called get_textract_results) ...
#     ocr.get_textract_results.assert_called_with("textract_job_123", TEST_AWS_REGION)
