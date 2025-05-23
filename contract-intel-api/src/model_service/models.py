from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

# --- Clause Extraction Models ---

class ClauseExtractionRequest(BaseModel): # For single text processing
    text: str
    document_id: Optional[str] = None

class ExtractedClause(BaseModel):
    clause_type: str = Field(..., example="Governing Law")
    extracted_text: str = Field(..., example="This agreement shall be governed by the laws of the State of Example.")
    confidence: Optional[float] = Field(None, example=0.95)
    page: Optional[int] = Field(None)
    # bounding_box: Optional[List[float]] = Field(None) # Example

class ClauseExtractionResponse(BaseModel): # Response for a single text's processing
    document_id: Optional[str] = None
    clauses: List[ExtractedClause] = Field(
        ...,
        example=[
            ExtractedClause(clause_type="Governing Law", extracted_text="This agreement shall be governed by the laws of the State of Example.", confidence=0.92),
            ExtractedClause(clause_type="Payment Clause", extracted_text="Payment shall be made by Party B to Party A within 30 days.", confidence=0.88)
        ]
    )
    model_version: Optional[str] = Field(None, example="legal-longformer-v1.0")
    processing_time_ms: Optional[float] = Field(None, example=150.75)
    error_message: Optional[str] = None # If processing this specific text failed in a batch

class ClauseExtractionBatchRequest(BaseModel):
    texts: List[str] # List of texts to process
    document_ids: Optional[List[Optional[str]]] = None # Optional: corresponding doc_ids for each text

class ClauseExtractionBatchResponse(BaseModel):
    batch_results: List[ClauseExtractionResponse] # List of results for each input text
    # Optional: add aggregated stats like total_processing_time_ms, errors_count

# --- Pydantic Models for the main /predict endpoint in app.py ---
# These are adapted from the existing app.py placeholders for consistency

class PredictionTaskRequest(BaseModel):
    text: str # For single prediction
    # Optional: specify tasks to run, defaults to all if not provided or if endpoint is specific
    tasks: List[str] = Field(default_factory=lambda: ["clause_extraction", "risk_classification", "summarization"])
    document_id: Optional[str] = None

# Batch version for /predict if it were to support batching for multiple tasks
class BatchPredictionTaskRequest(BaseModel):
    texts: List[str]
    document_ids: Optional[List[Optional[str]]] = None
    # Task configuration could be global or per-item if complex
    tasks: List[str] = Field(default_factory=lambda: ["clause_extraction", "risk_classification", "summarization"])


class RiskClassificationResultItem(BaseModel):
    risk_category: str = Field(..., example="High Risk")
    score: float = Field(..., example=0.78)
    details: Optional[str] = None

class SummarizationResultItem(BaseModel):
    summary: str = Field(..., example="This is a mock summary of the provided legal text...")

class SinglePredictionResponse(BaseModel): # Response for a single text to /predict
    document_id: Optional[str] = None
    request_id: Optional[str] = None
    # Changed to use the more detailed ClauseExtractionResponse
    clause_extractions: Optional[ClauseExtractionResponse] = None
    risk_classifications: Optional[List[RiskClassificationResultItem]] = None
    summary: Optional[SummarizationResultItem] = None
    errors: Optional[List[str]] = None
    # model_versions: Optional[Dict[str, str]] = None # To specify version for each task's model

# Response for a batch request to /predict
class BatchPredictionResponse(BaseModel):
    batch_results: List[SinglePredictionResponse]
    # Optional: aggregated stats

# It's good practice to also define request models for other tasks if they become specific endpoints
class RiskClassificationRequest(BaseModel):
    text: str
    document_id: Optional[str] = None

class RiskClassificationResponse(BaseModel):
    document_id: Optional[str] = None
    risk_classifications: List[RiskClassificationResultItem]
    model_version: Optional[str] = None
    processing_time_ms: Optional[float] = None

class SummarizationRequest(BaseModel):
    text: str
    document_id: Optional[str] = None

class SummarizationResponse(BaseModel):
    document_id: Optional[str] = None
    summary: str
    model_version: Optional[str] = None
    processing_time_ms: Optional[float] = None
