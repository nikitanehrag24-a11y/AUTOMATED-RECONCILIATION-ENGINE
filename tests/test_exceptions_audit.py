import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from engine.exceptions import ExceptionClassifier, ExceptionCategory, EscalationManager
from engine.audit import AuditLogger
from database.connection import SessionLocal, init_db, engine
from database.models import AuditLog, Base
import sqlalchemy

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    # Initialise in-memory SQLite database for testing exceptions & audit logging
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

def test_exception_classification_format_error():
    # Record has a parsing flag error
    record = {
        "id": "rec-1",
        "txn_id": "ERR001",
        "amount": "100.00",
        "currency": "INR",
        "direction": "CR",
        "raw_date": "2026-07-01",
        "metadata": {"format_error": True}
    }
    
    category, severity, tier, deadline = ExceptionClassifier.classify(record, [], [])
    assert category == ExceptionCategory.FORMAT_ERROR
    assert severity == "HIGH"
    assert tier == 2

def test_exception_classification_direction_reversal():
    # Same ref and amount, but same direction (both CR)
    record = {
        "id": "rec-1",
        "source_type": "INTERNAL",
        "txn_id": "TXN_REV",
        "amount": "500.00",
        "currency": "INR",
        "direction": "CR",
        "txn_date": datetime(2026, 7, 1)
    }
    opposite_pool = [
        {"id": "rec-2", "txn_id": "TXN_REV", "amount": Decimal("500.00"), "currency": "INR", "direction": "CR", "txn_date": datetime(2026, 7, 1)}
    ]
    
    category, severity, tier, deadline = ExceptionClassifier.classify(record, [], opposite_pool)
    assert category == ExceptionCategory.DIRECTION_REVERSAL
    assert severity == "CRITICAL"
    assert tier == 4
    
    # SLA deadline should be close to 30 mins
    time_diff = (deadline - datetime.utcnow()).total_seconds()
    assert 1700 <= time_diff <= 1900 # ~30 minutes (1800s)

def test_escalation_manager_sla_breach():
    # SLA deadline has passed
    past_deadline = datetime.utcnow() - timedelta(minutes=5)
    open_exceptions = [
        {
            "id": "exc-1",
            "status": "OPEN",
            "assigned_tier": 2,
            "severity": "MEDIUM",
            "sla_deadline": past_deadline
        }
    ]
    
    escalated = EscalationManager.check_sla_breaches(open_exceptions)
    assert len(escalated) == 1
    assert escalated[0]["assigned_tier"] == 3
    assert escalated[0]["status"] == "ESCALATED"

def test_cryptographic_audit_log(db_session):
    # Log first event
    log1 = AuditLogger.log_event(
        db_session,
        actor="analyst-1",
        action_type="INGEST",
        affected_records_list=["txn-1"],
        before_state_dict={},
        after_state_dict={"status": "ingested"},
        rationale="File upload"
    )
    
    # Log second event (chains onto the first)
    log2 = AuditLogger.log_event(
        db_session,
        actor="system",
        action_type="MATCH",
        affected_records_list=["txn-1", "txn-2"],
        before_state_dict={"status": "ingested"},
        after_state_dict={"status": "matched"},
        rationale="Exact match engine run"
    )
    
    # Verify log integrity
    is_valid, violations = AuditLogger.verify_log_integrity(db_session)
    assert is_valid is True
    assert len(violations) == 0
    
    # Deliberately tamper with log1 inside the database to test verification failure
    db_session.query(AuditLog).filter(AuditLog.id == log1.id).update({"actor": "malicious-user"})
    db_session.commit()
    
    is_valid_after_tamper, violations_after_tamper = AuditLogger.verify_log_integrity(db_session)
    assert is_valid_after_tamper is False
    assert len(violations_after_tamper) > 0
    assert violations_after_tamper[0]["log_id"] == log1.id
