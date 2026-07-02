from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import List, Dict, Any, Optional
from decimal import Decimal

class RunTriggerRequest(BaseModel):
    bank_code: str = Field(..., example="HDFC")
    run_date: Optional[date] = Field(default=None, description="Reconciliation run date (defaults to today)")

class ReconciliationRunResponse(BaseModel):
    id: str
    bank_code: str
    run_date: date
    status: str
    total_records: int
    matched_records: int
    exception_records: int
    match_rate: Decimal
    processing_time_ms: int
    created_at: datetime

    class Config:
        from_attributes = True

class ExceptionResolveRequest(BaseModel):
    resolution_type: str = Field(..., example="MANUAL_MATCH")
    resolution_details: Dict[str, Any] = Field(..., example={"matching_txn_id": "TXN999", "reason": "Operator verified manually"})

class ExceptionResponse(BaseModel):
    id: str
    run_id: str
    normalised_txn_id: str
    category: str
    severity: str
    status: str
    assigned_tier: int
    assigned_user_id: Optional[str] = None
    sla_deadline: datetime
    resolution_type: Optional[str] = None
    resolution_details: Optional[Dict[str, Any]] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True

class AuditLogResponse(BaseModel):
    id: str
    timestamp: datetime
    actor: str
    action_type: str
    affected_records: List[str]
    before_state: Dict[str, Any]
    after_state: Dict[str, Any]
    rationale: str
    sha256_hash: str

    class Config:
        from_attributes = True

class DashboardSummary(BaseModel):
    total_transactions: int
    matched_count: int
    match_rate: float
    exception_count: int
    exceptions_by_category: Dict[str, int]
    exceptions_by_severity: Dict[str, int]
    unresolved_count: int
    avg_processing_time: float
    daily_trend: List[Dict[str, Any]]
