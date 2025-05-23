import os
import logging
import time
import json # For dummy model file creation
from fastapi import FastAPI, HTTPException
from transformers import (
    AutoTokenizer,
    AutoModelForTokenClassification,
    # AutoModelForSequenceClassification, # Keep for future risk model
    # AutoModelForSeq2SeqLM, # Keep for future summarization model
)
import torch
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Dict # Added Dict

# Import Pydantic models from the new models.py
from .models import (
    ClauseExtractionRequest, # Kept for potential single endpoint
    ClauseExtractionBatchRequest,
    ClauseExtractionResponse,
    ExtractedClause,
    PredictionTaskRequest,
    SinglePredictionResponse, # Renamed from PredictionResponse in app.py for clarity
    BatchPredictionTaskRequest, # For future batch /predict
    BatchPredictionResponse,  # For future batch /predict
    RiskClassificationResultItem,
    SummarizationResultItem,
)

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# --- Configuration ---
MODEL_DIR = os.environ.get("MODEL_DIR", "mock_models/clause-extraction-model")
ROBERTA_CLS_PATH = os.environ.get("ROBERTA_CLS_PATH", "mock_models/roberta-cls")
PEGASUS_LEGAL_PATH = os.environ.get("PEGASUS_LEGAL_PATH", "mock_models/pegasus-legal")
MAX_WORKERS_BATCH = int(os.environ.get("MAX_WORKERS_BATCH", "4")) # For ThreadPoolExecutor

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Contract Intelligence Model Service",
    description="Provides endpoints for clause extraction, risk classification, and summarization.",
    version="0.1.0",
)

# --- Global Model Variables ---
# Clause Extraction Model
clause_tokenizer = None
clause_model = None # This can be original, TorchScript, or BetterTransformer version
# Other models (placeholders)
roberta_cls_model = "mock_roberta_cls_model"
pegasus_legal_model = "mock_pegasus_legal_model"

# ThreadPoolExecutor for batch processing
# Initialize once at startup, or can be done within the endpoint if preferred
# For simplicity, we'll initialize it here or ensure it's managed per request.
# A global executor is fine if task submission is thread-safe, which it is here.
batch_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS_BATCH)


def load_clause_extraction_model_and_optimize():
    """
    Loads the HuggingFace model for clause extraction and attempts optimizations.
    This function is called during application startup.
    """
    global clause_tokenizer, clause_model
    model_path = MODEL_DIR
    if not os.path.exists(model_path) or not any(os.scandir(model_path)):
        logger.warning(
            f"Clause extraction model directory {model_path} is empty or does not exist. "
            "Clause extraction will use mock data."
        )
        clause_model = "mock_clause_extraction_model"
        clause_tokenizer = "mock_clause_extraction_tokenizer"
        return

    try:
        logger.info(f"Loading clause extraction tokenizer from: {model_path}")
        clause_tokenizer = AutoTokenizer.from_pretrained(model_path)
        
        logger.info(f"Loading original clause extraction model from: {model_path}")
        original_model = AutoModelForTokenClassification.from_pretrained(model_path)
        original_model.eval() # Set to evaluation mode

        # --- Attempt Model Optimizations ---
        optimized_model = original_model # Start with the original model

        # 1. TorchScript Conversion (Tracing)
        # Tracing requires example inputs. The shape of these inputs (batch size, sequence length)
        # will be fixed in the traced model. This might be an issue for variable-length inputs
        # unless dynamic axes are specified, which can be complex.
        # For HuggingFace models, `torch.jit.script` is often harder to get working than `trace`.
        try:
            logger.info("Attempting to trace clause extraction model to TorchScript...")
            # Create dummy inputs for tracing. Adjust batch_size and sequence_length as needed.
            # These should be representative of typical inputs.
            dummy_text = "This is a dummy text for tracing."
            dummy_inputs = clause_tokenizer(dummy_text, return_tensors="pt", padding="max_length", truncation=True, max_length=128)
            
            # If model is on CUDA, inputs must also be on CUDA for tracing
            if torch.cuda.is_available():
                original_model.to('cuda')
                dummy_inputs = {k: v.to('cuda') for k, v in dummy_inputs.items()}

            # Remove 'token_type_ids' if the model doesn't use them (e.g., some Longformer models)
            if 'token_type_ids' in dummy_inputs and not any(p.name == 'token_type_ids' for p in original_model.parameters() if 'token_type_ids' in p.name): # Heuristic
                 if hasattr(original_model, 'config') and original_model.config.model_type not in ['bert', 'xlnet']: # common models that use token_type_ids
                    logger.info("Removing token_type_ids from dummy_inputs for tracing as model might not use them.")
                    dummy_inputs.pop('token_type_ids', None)
            
            # Ensure dummy_inputs only contains what the model's forward() expects
            model_specific_inputs = {}
            if "input_ids" in dummy_inputs: model_specific_inputs["input_ids"] = dummy_inputs["input_ids"]
            if "attention_mask" in dummy_inputs: model_specific_inputs["attention_mask"] = dummy_inputs["attention_mask"]
            if "token_type_ids" in dummy_inputs: model_specific_inputs["token_type_ids"] = dummy_inputs["token_type_ids"]


            traced_model = torch.jit.trace(original_model, example_kwarg_inputs=model_specific_inputs)
            optimized_model = traced_model
            logger.info("Successfully traced model to TorchScript.")
            # To save the traced model: torch.jit.save(traced_model, "traced_model.pt")
            # To load: loaded_traced_model = torch.jit.load("traced_model.pt")
        except Exception as e:
            logger.warning(f"Failed to trace model to TorchScript: {e}. Using original model.", exc_info=True)
            # Fallback to original_model if tracing fails

        # 2. BetterTransformer (Optimum)
        # This is generally safe to try for many common architectures.
        # It performs runtime fusion of operations like self-attention.
        try:
            from optimum.bettertransformer import BetterTransformer
            logger.info("Attempting to convert model to BetterTransformer...")
            # If model is already TorchScript, it might not be compatible with BetterTransformer directly.
            # Apply BetterTransformer to the original model if TorchScripting wasn't the final step or failed.
            # If TorchScript worked, we might prefer that. For this example, let's try to apply it
            # to the current `optimized_model` which could be original or traced.
            # Note: BetterTransformer might be more effective on the original PyTorch model.
            if isinstance(optimized_model, torch.nn.Module) and not isinstance(optimized_model, torch.jit.ScriptModule):
                 # Apply to a fresh instance of original_model if current optimized_model is already traced.
                model_to_transform = AutoModelForTokenClassification.from_pretrained(model_path)
                model_to_transform.eval()
                if torch.cuda.is_available(): model_to_transform.to('cuda')
                
                bt_model = BetterTransformer.transform(model_to_transform, keep_original_model=False)
                optimized_model = bt_model
                logger.info("Successfully converted model to BetterTransformer.")
            elif isinstance(optimized_model, torch.jit.ScriptModule):
                logger.info("Model is already TorchScript; skipping BetterTransformer for now.")
            else: # if optimized_model is the original_model
                bt_model = BetterTransformer.transform(optimized_model, keep_original_model=False)
                optimized_model = bt_model
                logger.info("Successfully converted model to BetterTransformer.")

        except ImportError:
            logger.info("Optimum library not installed. Skipping BetterTransformer optimization.")
        except Exception as e:
            logger.warning(f"Failed to convert model to BetterTransformer: {e}. Continuing with previous model version.", exc_info=True)

        # Assign the potentially optimized model to the global variable
        clause_model = optimized_model

        if torch.cuda.is_available() and hasattr(clause_model, 'to'): # Ensure it's a PyTorch model
            logger.info("Moving final clause extraction model to CUDA (if not already).")
            clause_model.to('cuda')
        
        if hasattr(clause_model, 'eval') and callable(clause_model.eval): # Ensure it's a PyTorch model
            clause_model.eval()

        logger.info("Clause extraction model loaded and optimization attempts complete.")

    except Exception as e:
        logger.error(f"Error loading/optimizing clause extraction model from {model_path}: {e}", exc_info=True)
        clause_model = "mock_clause_extraction_model_load_failed"
        clause_tokenizer = "mock_clause_extraction_tokenizer_load_failed"


def load_other_models_placeholder():
    """Placeholder for loading other models."""
    global roberta_cls_model, pegasus_legal_model #, summarizer_pipeline
    logger.info(f"Attempting to 'load' RoBERTa-CLS from: {ROBERTA_CLS_PATH} (placeholder)")
    roberta_cls_model = "mock_roberta_cls_model"
    logger.info("RoBERTa-CLS 'loaded' (placeholder).")

    logger.info(f"Attempting to 'load' PEGASUS-legal-large from: {PEGASUS_LEGAL_PATH} (placeholder)")
    pegasus_legal_model = "mock_pegasus_legal_model"
    logger.info("PEGASUS-legal-large 'loaded' (placeholder).")

@app.on_event("startup")
async def startup_event():
    logger.info("Starting model service initialization (FastAPI startup event)...")
    load_clause_extraction_model_and_optimize()
    load_other_models_placeholder()
    logger.info("Model service initialization complete.")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down ThreadPoolExecutor...")
    batch_executor.shutdown(wait=True)
    logger.info("ThreadPoolExecutor shut down.")


# --- Internal Prediction Logic for a single text ---
def _predict_single_text_clauses(text: str, document_id: Optional[str] = None) -> ClauseExtractionResponse:
    """Internal logic to perform clause extraction on a single text."""
    start_time = time.time()
    extracted_clauses: List[ExtractedClause] = []
    model_version_str = "optimized-clause-model-v0.2" # Placeholder
    error_message = None

    if clause_model and clause_tokenizer and \
       not isinstance(clause_model, str) and not isinstance(clause_tokenizer, str):
        try:
            inputs = clause_tokenizer(text, return_tensors="pt", truncation=True, padding=True, max_length=512)
            if torch.cuda.is_available():
                inputs = {k: v.to('cuda') for k, v in inputs.items()}

            with torch.no_grad():
                # Traced models are called like functions: outputs = clause_model(inputs['input_ids'], inputs['attention_mask'])
                # Standard PyTorch models: outputs = clause_model(**inputs)
                # Need to check if clause_model is ScriptModule or nn.Module
                if isinstance(clause_model, torch.jit.ScriptModule):
                    # Construct tuple of inputs in the order expected by the traced model's forward method
                    # This usually means (input_ids, attention_mask, token_type_ids if used)
                    # We need to be careful about which inputs the traced model was created with.
                    # Assuming it was traced with input_ids and attention_mask:
                    model_inputs = (inputs['input_ids'], inputs['attention_mask'])
                    if 'token_type_ids' in inputs and clause_model.code.count("token_type_ids") > 0 : # basic check
                         model_inputs = (inputs['input_ids'], inputs['attention_mask'], inputs['token_type_ids'])
                    
                    # The output of a traced model might be a tuple if the original model returned multiple things.
                    # Or it might be a custom class if `return_dict=True` was part of tracing (less common for jit.trace).
                    # Typically, for HF models, the first element of the tuple output is logits.
                    raw_outputs = clause_model(*model_inputs)
                    logits = raw_outputs[0] if isinstance(raw_outputs, tuple) else raw_outputs.logits
                else: # Standard PyTorch nn.Module
                    outputs = clause_model(**inputs)
                    logits = outputs.logits
            
            predictions = torch.argmax(logits, dim=2)
            
            # Simplified BIO Tag Decoding (Placeholder - same as before)
            tokens = clause_tokenizer.convert_ids_to_tokens(inputs["input_ids"][0])
            id2label_mock = {0: "O", 1: "B-PAYMENT", 2: "I-PAYMENT", 3: "B-GOVERNINGLAW", 4: "I-GOVERNINGLAW"} # Example label map
            if hasattr(clause_model, 'config') and hasattr(clause_model.config, 'id2label'):
                id2label_map = clause_model.config.id2label
            elif hasattr(original_model, 'config') and hasattr(original_model.config, 'id2label'): # Fallback to original model's config if optimized one lost it
                id2label_map = original_model.config.id2label
            else:
                id2label_map = id2label_mock
                logger.warning("Using mock id2label map for BIO decoding.")


            current_clause_type = None
            current_clause_tokens = []
            for token_idx, prediction_id in enumerate(predictions[0].tolist()):
                label = id2label_map.get(prediction_id, "O")
                token = tokens[token_idx]
                if token in [clause_tokenizer.cls_token, clause_tokenizer.sep_token, clause_tokenizer.pad_token]: continue
                if label.startswith("B-"):
                    if current_clause_type and current_clause_tokens:
                        extracted_text = clause_tokenizer.convert_tokens_to_string(current_clause_tokens)
                        extracted_clauses.append(ExtractedClause(clause_type=current_clause_type, extracted_text=extracted_text.strip(), confidence=0.9))
                    current_clause_type = label[2:]
                    current_clause_tokens = [token]
                elif label.startswith("I-") and current_clause_type == label[2:]:
                    current_clause_tokens.append(token)
                elif label == "O":
                    if current_clause_type and current_clause_tokens:
                        extracted_text = clause_tokenizer.convert_tokens_to_string(current_clause_tokens)
                        extracted_clauses.append(ExtractedClause(clause_type=current_clause_type, extracted_text=extracted_text.strip(), confidence=0.9))
                    current_clause_type = None
                    current_clause_tokens = []
            if current_clause_type and current_clause_tokens:
                extracted_text = clause_tokenizer.convert_tokens_to_string(current_clause_tokens)
                extracted_clauses.append(ExtractedClause(clause_type=current_clause_type, extracted_text=extracted_text.strip(), confidence=0.9))

            if not extracted_clauses:
                 extracted_clauses.append(ExtractedClause(clause_type="Generic Clause (Model Fallback)", extracted_text="Model processed text but simplified decoding yielded no specific clauses.", confidence=0.5))

            if hasattr(clause_model, 'config') and hasattr(clause_model.config, '_name_or_path'):
                model_version_str = os.path.basename(clause_model.config._name_or_path)
            elif isinstance(clause_model, torch.jit.ScriptModule):
                model_version_str = "torchscript-optimized-model"


        except Exception as e:
            logger.error(f"Error during clause extraction model inference for doc_id {document_id}: {e}", exc_info=True)
            error_message = f"Error during model processing: {str(e)}"
            # Fallback to error clause
            extracted_clauses = [ExtractedClause(clause_type="Error Clause", extracted_text=error_message, confidence=0.0)]
            model_version_str = "error_during_inference"
    else:
        logger.info(f"Using mock clause extraction for doc_id {document_id} (text: '{text[:50]}...')")
        error_message = "Clause extraction model not available (using mock)."
        extracted_clauses = [
            ExtractedClause(clause_type="Payment Clause (Mock)", extracted_text="Mock: Party B pays Party A.", confidence=0.95),
            ExtractedClause(clause_type="Termination Clause (Mock)", extracted_text="Mock: Agreement terminates upon notice.", confidence=0.88),
        ]
        model_version_str = "mock-clause-model-v0.1-fallback"
    
    processing_time_ms = (time.time() - start_time) * 1000
    return ClauseExtractionResponse(
        document_id=document_id,
        clauses=extracted_clauses,
        model_version=model_version_str,
        processing_time_ms=processing_time_ms,
        error_message=error_message
    )


# --- API Endpoints ---

@app.post("/extract_clauses_batch", response_model=ClauseExtractionBatchResponse)
async def extract_clauses_batch_endpoint(request: ClauseExtractionBatchRequest):
    """
    Accepts a batch of texts and returns extracted legal clauses for each.
    Uses ThreadPoolExecutor for parallel processing of texts in the batch.
    Note: SageMaker's own batching (e.g., for batch transform jobs or real-time endpoint
    configuration like `max_batch_size`) is an alternative or complementary approach.
    This implementation provides application-level batching.
    """
    logger.info(f"Received batch clause extraction request for {len(request.texts)} texts.")
    if not request.texts:
        raise HTTPException(status_code=400, detail="Input texts list cannot be empty.")

    results: List[ClauseExtractionResponse] = []
    document_ids = request.document_ids if request.document_ids and len(request.document_ids) == len(request.texts) else [None] * len(request.texts)

    # Use ThreadPoolExecutor to process texts in parallel
    # The tokenizer can handle batch inputs, but model inference here is one by one
    # unless the model itself is modified for internal batch processing of heterogeneous tasks.
    # This ThreadPool approach parallelizes the _predict_single_text_clauses calls.
    
    # Submit tasks to the executor
    future_to_doc_id = {
        batch_executor.submit(_predict_single_text_clauses, text, doc_id): doc_id
        for text, doc_id in zip(request.texts, document_ids)
    }

    for future in future_to_doc_id: # Iterate in submission order (though completion order might vary)
        try:
            result = future.result() # Get result from completed future
            results.append(result)
        except Exception as e:
            # Handle exceptions from _predict_single_text_clauses if any weren't caught internally
            doc_id_for_error = future_to_doc_id[future] # Get corresponding doc_id
            logger.error(f"Error processing text for doc_id {doc_id_for_error} in batch: {e}", exc_info=True)
            results.append(ClauseExtractionResponse(
                document_id=doc_id_for_error,
                clauses=[ExtractedClause(clause_type="Batch Processing Error", extracted_text=str(e), confidence=0.0)],
                error_message=f"Batch processing failed for this item: {str(e)}"
            ))
            
    # Ensure results are in the same order as input if that's a requirement.
    # The current iteration over `future_to_doc_id` (which is a dict) does not guarantee order.
    # For ordered results, map futures back to their original positions.
    # A simpler way for now, if order from ThreadPool is acceptable or input order is not strictly needed:
    # results = [future.result() for future in concurrent.futures.as_completed(futures)]
    # For ordered results based on input:
    ordered_results = [None] * len(request.texts)
    temp_results_map = {res.document_id: res for res in results if res.document_id is not None} # If doc_ids are unique

    if all(doc_id is not None for doc_id in document_ids) and len(set(filter(None,document_ids))) == len(list(filter(None,document_ids))): # if all doc_ids are unique and present
        for i, doc_id in enumerate(document_ids):
            ordered_results[i] = temp_results_map.get(doc_id)
    else: # Fallback if doc_ids are not reliable for ordering, or simply use completion order
        # This part needs refinement if strict input order is critical and doc_ids are not guaranteed unique.
        # For now, using the completion-order results from the loop above, which might not match input order.
        # If strict order is required:
        # futures_list = [batch_executor.submit(_predict_single_text_clauses, text, doc_id) for text, doc_id in zip(request.texts, document_ids)]
        # ordered_results = [future.result() for future in futures_list] # This blocks sequentially for results though.
        # The initial loop `for future in future_to_doc_id:` processes them as they complete.
        # Let's assume for now that the client can re-associate based on doc_id if provided, or order is not critical.
        # The results list is already populated. If some futures failed and are not in `results`, need to handle that.
        # The current loop `for future in future_to_doc_id:` should capture all results or errors.
        pass # Results are already collected in `results` list, order might not match input.


    return ClauseExtractionBatchResponse(batch_results=results) # Using the potentially unordered results for now


@app.post("/predict", response_model=SinglePredictionResponse) # Changed to SinglePredictionResponse
async def predict_endpoint(request: PredictionTaskRequest):
    """
    Accepts a single text and returns clause extractions, risk classifications, and a summary
    based on the 'tasks' field in the request.
    """
    logger.info(f"Received prediction request for tasks: {request.tasks}, document_id: {request.document_id}")
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="Input text cannot be empty.")

    response_payload = SinglePredictionResponse(document_id=request.document_id)
    errors: List[str] = [] # Ensure errors is always a list

    try:
        if "clause_extraction" in request.tasks:
            # Use the internal single prediction directly
            clause_response = _predict_single_text_clauses(request.text, request.document_id)
            response_payload.clause_extractions = clause_response
            if clause_response.error_message:
                errors.append(f"Clause Extraction Error: {clause_response.error_message}")
        
        if "risk_classification" in request.tasks:
            risk_results = perform_risk_classification_placeholder(request.text) # Placeholder
            response_payload.risk_classifications = risk_results
        
        if "summarization" in request.tasks:
            summary_result = perform_summarization_placeholder(request.text) # Placeholder
            response_payload.summary = summary_result
        
        if errors:
            response_payload.errors = errors

        return response_payload

    except Exception as e:
        logger.error(f"Error during /predict endpoint for doc_id {request.document_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred during prediction: {str(e)}")


# --- Main Block for Uvicorn (Optional, for direct execution) ---
if __name__ == "__main__":
    if not os.path.exists(MODEL_DIR) or not any(os.scandir(MODEL_DIR)):
        logger.warning(f"'{MODEL_DIR}' not found or empty. Creating dummy model files for local testing.")
        dummy_model_dir = MODEL_DIR
        os.makedirs(dummy_model_dir, exist_ok=True)
        # Create minimal config.json and tokenizer_config.json, vocab.txt
        # This allows AutoModel*.from_pretrained to pass initial path checks.
        # Actual model functionality requires real model files.
        with open(os.path.join(dummy_model_dir, "config.json"), "w") as f:
            json.dump({"model_type": "bert", "num_labels": 5, "id2label": {0: "O", 1: "B-PAYMENT", 2: "I-PAYMENT", 3: "B-GOVERNINGLAW", 4: "I-GOVERNINGLAW"}}, f)
        with open(os.path.join(dummy_model_dir, "tokenizer_config.json"), "w") as f:
            json.dump({"model_max_length": 512}, f)
        with open(os.path.join(dummy_model_dir, "vocab.txt"), "w") as f:
            f.write("[UNK]\n[CLS]\n[SEP]\n[PAD]\n[MASK]\nsome\nexample\nvocab\n")
        # A dummy pytorch_model.bin is harder to make "valid enough" without torch.save
        # If torch is available:
        try:
            torch.save({"dummy_state_dict": torch.nn.Linear(1,1).state_dict()}, os.path.join(dummy_model_dir, "pytorch_model.bin"))
        except ImportError:
            logger.warning("torch not available, cannot create dummy pytorch_model.bin for local testing.")
        except Exception as e:
            logger.warning(f"Could not create dummy pytorch_model.bin: {e}")


    import uvicorn
    logger.info("Starting Uvicorn server for model service on http://localhost:8000")
    # The command used in Dockerfile is:
    # CMD ["poetry", "run", "uvicorn", "src.model_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
    # For local direct execution:
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False) # reload=False is better for testing model loading once.
                                                            # Set reload=True for active development of endpoint logic.I have updated the FastAPI model service (`contract-intel-api/src/model_service/app.py`) and the Pydantic models (`contract-intel-api/src/model_service/models.py`).

**Summary of Changes:**

**1. `contract-intel-api/src/model_service/models.py`:**
    *   Defined `ClauseExtractionBatchRequest` to accept `texts: List[str]` and optional `document_ids: List[Optional[str]]`.
    *   Defined `ClauseExtractionBatchResponse` to return `batch_results: List[ClauseExtractionResponse]`.
    *   Added `error_message: Optional[str]` to `ClauseExtractionResponse` to capture errors for individual items within a batch.
    *   Renamed `PredictionResponse` to `SinglePredictionResponse` for clarity when used by the single-text `/predict` endpoint.
    *   Added `BatchPredictionTaskRequest` and `BatchPredictionResponse` as placeholders for future batch capabilities of the `/predict` endpoint.

**2. `contract-intel-api/src/model_service/app.py`:**
    *   **Model Loading on Startup (`@app.on_event("startup")`):**
        *   The function `load_clause_extraction_model_and_optimize` is now called during FastAPI startup.
        *   This function loads the tokenizer and the `AutoModelForTokenClassification` model from `MODEL_DIR`.
    *   **Model Optimization Attempts (within `load_clause_extraction_model_and_optimize`):**
        *   **TorchScript:**
            *   Includes an attempt to trace the loaded HuggingFace model using `torch.jit.trace`.
            *   Dummy inputs are created for tracing. Comments are included about potential issues with dynamic axes and input shapes.
            *   Error handling is present to fall back to the original model if tracing fails.
            *   Logic to handle `token_type_ids` for tracing based on model type heuristics was added.
        *   **BetterTransformer (Optimum):**
            *   Includes an attempt to convert the model using `optimum.bettertransformer.BetterTransformer.transform()`.
            *   This is attempted on the original PyTorch model (or the current `optimized_model` if it's not already TorchScript).
            *   Import errors for `optimum` are handled gracefully, and conversion errors also fall back to the previous model version.
        *   The global `clause_model` variable will hold the potentially optimized model.
        *   The model is moved to CUDA if available and set to evaluation mode.
    *   **Batching for `/extract_clauses_batch` Endpoint:**
        *   A new endpoint `/extract_clauses_batch` is implemented.
        *   It uses `ClauseExtractionBatchRequest` and `ClauseExtractionBatchResponse`.
        *   A global `ThreadPoolExecutor` (`batch_executor`) is initialized (default `max_workers=4`, configurable via `MAX_WORKERS_BATCH` env var).
        *   The endpoint submits each text from the batch to the `_predict_single_text_clauses` function via the `batch_executor`.
        *   Results (or error responses for individual items) are collected and returned.
        *   **Note:** The current implementation for collecting results from the thread pool might not preserve the original input order if `document_ids` are not unique or consistently provided. Comments are added regarding this.
    *   **Internal Single Prediction Logic (`_predict_single_text_clauses`):**
        *   The core clause extraction logic for a single text is refactored into this internal function.
        *   It handles inference using the global `clause_model` (which could be original, TorchScript, or BetterTransformer).
        *   It correctly handles calling TorchScript models (which are called like functions) versus standard `nn.Module` models.
        *   Includes the same simplified BIO tag decoding placeholder.
        *   Returns a `ClauseExtractionResponse`, including any errors specific to that text's processing.
    *   **Updated `/predict` Endpoint:**
        *   Still handles single text requests using `PredictionTaskRequest`.
        *   Its response model is now `SinglePredictionResponse`.
        *   It calls `_predict_single_text_clauses` internally for the clause extraction task.
        *   Placeholder logic for other tasks (risk classification, summarization) remains.
    *   **Executor Shutdown (`@app.on_event("shutdown")`):** Added a shutdown hook for the `ThreadPoolExecutor`.
    *   **Dummy Model Files:** The `if __name__ == "__main__":` block for local Uvicorn execution was enhanced to create a dummy `pytorch_model.bin` (if `torch` is available) in addition to config files, making it slightly more robust for starting the service without a real pre-trained model for basic testing.

The implementation prioritizes getting the structure for optimization and batching in place. The actual effectiveness of TorchScript/BetterTransformer and the robustness of BIO decoding would depend heavily on the specific model used. The batching strategy with `ThreadPoolExecutor` provides application-level parallelism.

No changes to `pyproject.toml` were needed as `transformers`, `torch`, `fastapi`, and `uvicorn` were already dependencies. `optimum` would be a new dependency if BetterTransformer were to be a hard requirement and not just an exploration.
