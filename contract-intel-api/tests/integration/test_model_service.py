import pytest
from fastapi.testclient import TestClient
import tempfile
import os
import json
import shutil
from transformers import AutoTokenizer, AutoModelForTokenClassification
import torch

# Import the FastAPI app object from src.model_service.app
# The import path needs to be correct based on PYTHONPATH in the test environment.
# Assuming tests are run from the root of the project.
from src.model_service.app import app as model_service_app
from src.model_service.models import ClauseExtractionBatchRequest

# Name of a very small model suitable for token classification (even if not fine-tuned for legal text)
# Using a tiny BERT model. You might need to `pip install sentencepiece` for some tokenizers.
# A specific small model that supports AutoModelForTokenClassification would be ideal.
# 'prajjwal1/bert-tiny' is a general BERT, might need config for token classification.
# Let's use a model specifically for token classification if readily available and small.
# If not, we'll use a generic one and configure it.
# For simplicity, using 'distilbert-base-uncased' as it's smaller than 'bert-base-uncased'
# and can be configured for token classification.
# A truly minimal model would be best, but let's proceed with a standard small one.
# Using a very specific tiny model: 'hf-internal-testing/tiny-random-BertForTokenClassification'
# This model is extremely small and designed for testing.
# If this model is not available or causes issues, will fallback to creating a dummy structure.
TEST_MODEL_NAME = "hf-internal-testing/tiny-random-BertForTokenClassification"


@pytest.fixture(scope="module")
def setup_test_model_dir():
    """
    Sets up a temporary directory with a minimal HuggingFace model for testing.
    The model service will load the model from this directory via MODEL_DIR env var.
    """
    with tempfile.TemporaryDirectory(prefix="test_model_") as tmpdir:
        model_loaded_successfully = False
        try:
            print(f"Attempting to download test model '{TEST_MODEL_NAME}' to '{tmpdir}'")
            tokenizer = AutoTokenizer.from_pretrained(TEST_MODEL_NAME)
            # Ensure the model is configured for token classification with some labels
            # The number of labels should match what the app's BIO decoding logic might expect (even if simplified)
            # The dummy app uses a mock id2label map with 5 labels (O, B-PAYMENT, I-PAYMENT, B-GOVERNINGLAW, I-GOVERNINGLAW)
            model = AutoModelForTokenClassification.from_pretrained(TEST_MODEL_NAME, num_labels=5, id2label={0: "O", 1: "B-PAYMENT", 2: "I-PAYMENT", 3: "B-GOVERNINGLAW", 4: "I-GOVERNINGLAW"}, ignore_mismatched_sizes=True)
            
            tokenizer.save_pretrained(tmpdir)
            model.save_pretrained(tmpdir)
            print(f"Test model '{TEST_MODEL_NAME}' saved to '{tmpdir}'")
            model_loaded_successfully = True
        except Exception as e:
            print(f"Failed to download/save pretrained test model '{TEST_MODEL_NAME}': {e}. "
                  "Falling back to creating dummy model files.")
            # Create minimal config.json, tokenizer_config.json, vocab.txt, and dummy pytorch_model.bin
            # This matches the logic in app.py's __main__ block for when MODEL_DIR is empty.
            with open(os.path.join(tmpdir, "config.json"), "w") as f:
                json.dump({"model_type": "bert", "num_labels": 5, "id2label": {0: "O", 1: "B-PAYMENT", 2: "I-PAYMENT", 3: "B-GOVERNINGLAW", 4: "I-GOVERNINGLAW"}}, f)
            with open(os.path.join(tmpdir, "tokenizer_config.json"), "w") as f:
                json.dump({"model_max_length": 128}, f) # Small max length for testing
            with open(os.path.join(tmpdir, "vocab.txt"), "w") as f:
                f.write("[UNK]\n[CLS]\n[SEP]\n[PAD]\n[MASK]\ntest\nvocabulary\n")
            # Create a minimal valid PyTorch state_dict for a token classification head
            dummy_model_state = {
                "classifier.weight": torch.randn(5, 128), # num_labels, hidden_size (tiny-random-Bert's hidden_size is 128)
                "classifier.bias": torch.randn(5)
            }
            # To make it a full BertForTokenClassification state_dict, we'd need all bert layers.
            # For this fallback, we'll assume the app might try to load a model that only has a classifier head
            # or that the from_pretrained will handle missing base model weights if config matches.
            # A more robust dummy would involve creating all expected weights for the specified model_type.
            # For "bert", it would be e.g. bert.embeddings..., bert.encoder...
            # For simplicity, this dummy pytorch_model.bin might not be fully loadable by from_pretrained
            # without `ignore_mismatched_sizes=True` or if the config expects more layers.
            # The app.py's startup logic for dummy model creation is more basic.
            # Let's use a very simple state_dict, assuming from_pretrained might init others randomly.
            if 'hf-internal-testing' in TEST_MODEL_NAME : # tiny-random-BertForTokenClassification has hidden_size 128
                 minimal_state_dict = {
                    "bert.embeddings.word_embeddings.weight": torch.randn(128,128), # vocab_size, hidden_size
                    "classifier.weight": torch.randn(5, 128),
                    "classifier.bias": torch.randn(5)
                 }
                 torch.save(minimal_state_dict, os.path.join(tmpdir, "pytorch_model.bin"))
            else: # Fallback for other models if TEST_MODEL_NAME changes
                 torch.save(dummy_model_state, os.path.join(tmpdir, "pytorch_model.bin"))

            print(f"Created dummy model files in '{tmpdir}' as a fallback.")

        original_model_dir = os.environ.get("MODEL_DIR")
        os.environ["MODEL_DIR"] = tmpdir
        
        # Yield the directory path so it can be used in tests if needed
        yield tmpdir
        
        # Teardown: Restore original MODEL_DIR and the temp directory is cleaned up by TemporaryDirectory context manager
        if original_model_dir:
            os.environ["MODEL_DIR"] = original_model_dir
        else:
            if "MODEL_DIR" in os.environ: # Check if it was set by this fixture
                 del os.environ["MODEL_DIR"]
        
        # Clean up global model variables in the app to ensure next test run (if any) reloads
        # This is important if tests run in the same Python process without full app restarts.
        # Accessing globals directly is a bit of a hack for testing, but necessary here.
        model_service_app.clause_model = None
        model_service_app.clause_tokenizer = None


# TestClient needs to be initialized after MODEL_DIR is set, so use it inside the test function
# or pass it as a fixture that depends on setup_test_model_dir.

@pytest.fixture(scope="module")
def client(setup_test_model_dir): # client fixture depends on model setup
    # This will trigger the app's startup event, which loads the model
    with TestClient(model_service_app) as c:
        yield c


def test_extract_clauses_batch_endpoint(client: TestClient): # client is now a fixture
    """
    Tests the /extract_clauses_batch endpoint of the model service.
    """
    # Prepare a sample request payload
    payload_dict = {
        "texts": ["This is the first contract document.", "Clause two: another agreement detail."],
        "document_ids": ["doc1", "doc2"]
    }
    # Pydantic model can also be used:
    # payload = ClauseExtractionBatchRequest(texts=["text1", "text2"], document_ids=["doc1", "doc2"])
    # client.post("/extract_clauses_batch", json=payload.dict())

    response = client.post("/extract_clauses_batch", json=payload_dict)

    assert response.status_code == 200, f"Response error: {response.text}"
    response_data = response.json()

    assert "batch_results" in response_data
    assert len(response_data["batch_results"]) == 2

    for i, result in enumerate(response_data["batch_results"]):
        assert result["document_id"] == payload_dict["document_ids"][i]
        assert "clauses" in result
        assert isinstance(result["clauses"], list)
        # Check if model_version is present (can be mock or from model config)
        assert "model_version" in result
        assert result["model_version"] is not None

        # Verify simplified model output:
        # The hf-internal-testing/tiny-random-BertForTokenClassification model, even with configured labels,
        # will output somewhat random classifications as its weights are random.
        # The key is that it *produces* output in the expected structure, not its accuracy.
        # The BIO decoding in app.py is also very simplified.
        # We expect at least one clause, even if it's a fallback "Generic Clause".
        assert len(result["clauses"]) > 0
        first_clause = result["clauses"][0]
        assert "clause_type" in first_clause
        assert "extracted_text" in first_clause
        # Example: if the dummy model or fallback is hit
        # assert first_clause["clause_type"] == "Generic Clause (Model Fallback)" or \
        #        first_clause["clause_type"] in ["PAYMENT", "GOVERNINGLAW"] # From dummy id2label

        if result.get("error_message"):
            print(f"Warning: Processing for doc_id {result['document_id']} had an error: {result['error_message']}")


def test_predict_endpoint_single_text(client: TestClient):
    """
    Tests the /predict endpoint for a single text, focusing on clause extraction part.
    """
    payload = {
        "text": "This is a sample contract for prediction.",
        "document_id": "doc_predict_1",
        "tasks": ["clause_extraction"] # Focus on the implemented part
    }

    response = client.post("/predict", json=payload)
    assert response.status_code == 200, f"Response error: {response.text}"
    response_data = response.json()

    assert response_data["document_id"] == "doc_predict_1"
    assert "clause_extractions" in response_data
    assert response_data["clause_extractions"] is not None
    
    clause_result = response_data["clause_extractions"]
    assert "clauses" in clause_result
    assert isinstance(clause_result["clauses"], list)
    assert len(clause_result["clauses"]) > 0 # Similar check as above for batch
    
    first_clause = clause_result["clauses"][0]
    assert "clause_type" in first_clause
    assert "extracted_text" in first_clause

    # Check that other (placeholder) tasks are not present or are None
    assert "risk_classifications" not in response_data or response_data["risk_classifications"] is None
    assert "summary" not in response_data or response_data["summary"] is None

    if response_data.get("errors"):
        print(f"Warning: /predict endpoint returned errors: {response_data['errors']}")

# Add more tests as needed, e.g., for empty input, specific error conditions, other tasks if implemented.
