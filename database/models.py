import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, DateTime, Date, 
    ForeignKey, Table, Index, JSON, Text, Enum, CHAR
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)  # 'ANALYST', 'MANAGER', 'COMPLIANCE', 'ADMIN'
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    exceptions = relationship("ExceptionRecord", back_populates="assigned_user")

class BankConfiguration(Base):
    __tablename__ = 'bank_configurations'
    
    bank_code = Column(String(20), primary_key=True)  # e.g., 'HDFC', 'ICICI', 'AXIS'
    bank_name = Column(String(100), nullable=False)
    supported_formats = Column(JSON, nullable=False)  # Array of formats e.g., ["CSV", "MT940"]
    timezone = Column(String(50), nullable=False, default='UTC')
    settlement_cycle = Column(String(10), nullable=False, default='T+1')
    column_mappings = Column(JSON, nullable=True)  # Column mappings for CSV format
    format_deviations = Column(JSON, nullable=True)  # Standard deviations or regex adjustments
    reconciliation_window_days = Column(Integer, default=5)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    runs = relationship("ReconciliationRun", back_populates="bank")
    raw_transactions = relationship("RawTransaction", back_populates="bank")
    normalised_transactions = relationship("NormalisedTransaction", back_populates="bank")

class ReconciliationRun(Base):
    __tablename__ = 'reconciliation_runs'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    bank_code = Column(String(20), ForeignKey('bank_configurations.bank_code'), nullable=False)
    run_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False)  # 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'
    total_records = Column(Integer, default=0)
    matched_records = Column(Integer, default=0)
    exception_records = Column(Integer, default=0)
    match_rate = Column(Numeric(5, 2), default=0.0)
    processing_time_ms = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    bank = relationship("BankConfiguration", back_populates="runs")
    match_results = relationship("MatchResult", back_populates="run")
    exceptions = relationship("ExceptionRecord", back_populates="run")

class RawTransaction(Base):
    __tablename__ = 'raw_transactions'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_hash = Column(String(64), nullable=False)
    filename = Column(String(255), nullable=False)
    bank_code = Column(String(20), ForeignKey('bank_configurations.bank_code'), nullable=False)
    format_type = Column(String(20), nullable=False)  # 'CSV', 'MT940', 'CAMT053'
    raw_content = Column(JSON, nullable=False)
    ingestion_date = Column(Date, nullable=False, default=lambda: datetime.utcnow().date())
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    bank = relationship("BankConfiguration", back_populates="raw_transactions")
    normalised_records = relationship("NormalisedTransaction", back_populates="raw_txn")

class NormalisedTransaction(Base):
    __tablename__ = 'normalised_transactions'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    raw_txn_id = Column(String(36), ForeignKey('raw_transactions.id'), nullable=True)
    source_type = Column(String(10), nullable=False)  # 'INTERNAL', 'EXTERNAL'
    bank_code = Column(String(20), ForeignKey('bank_configurations.bank_code'), nullable=False)
    txn_id = Column(String(64), nullable=False, index=True)
    txn_date = Column(DateTime(timezone=True), nullable=False)
    amount = Column(Numeric(18, 4), nullable=False)
    currency = Column(CHAR(3), nullable=False)
    direction = Column(CHAR(2), nullable=False)  # 'DR', 'CR'
    counterparty_name = Column(String(140), nullable=True)
    counterparty_account = Column(String(34), nullable=True)
    bank_ref = Column(String(35), nullable=True)
    narration = Column(String(500), nullable=True)
    settlement_date = Column(Date, nullable=True)
    is_reconciled = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    raw_txn = relationship("RawTransaction", back_populates="normalised_records")
    bank = relationship("BankConfiguration", back_populates="normalised_transactions")

# Define composite indexes for matching performance
Index('idx_normalised_matching', NormalisedTransaction.txn_id, NormalisedTransaction.amount, NormalisedTransaction.currency, NormalisedTransaction.direction)
Index('idx_normalised_lookup', NormalisedTransaction.bank_code, NormalisedTransaction.source_type, NormalisedTransaction.is_reconciled)

class MatchResult(Base):
    __tablename__ = 'match_results'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String(36), ForeignKey('reconciliation_runs.id'), nullable=False)
    internal_txn_id = Column(String(36), ForeignKey('normalised_transactions.id'), nullable=True)
    external_txn_id = Column(String(36), ForeignKey('normalised_transactions.id'), nullable=True)
    match_type = Column(String(20), nullable=False)  # 'EXACT', 'FUZZY', 'RULE'
    rule_name = Column(String(50), nullable=True)
    confidence_score = Column(Numeric(3, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    run = relationship("ReconciliationRun", back_populates="match_results")
    internal_txn = relationship("NormalisedTransaction", foreign_keys=[internal_txn_id])
    external_txn = relationship("NormalisedTransaction", foreign_keys=[external_txn_id])

class ExceptionRecord(Base):
    __tablename__ = 'exceptions'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String(36), ForeignKey('reconciliation_runs.id'), nullable=False)
    normalised_txn_id = Column(String(36), ForeignKey('normalised_transactions.id'), unique=True, nullable=False)
    category = Column(String(30), nullable=False, index=True)  # e.g., 'MISSING_INTERNAL'
    severity = Column(String(10), nullable=False)  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    status = Column(String(20), nullable=False, default='OPEN')  # 'OPEN', 'UNDER_REVIEW', 'RESOLVED', 'ESCALATED'
    assigned_tier = Column(Integer, default=2)  # 1 to 4
    assigned_user_id = Column(String(36), ForeignKey('users.id'), nullable=True)
    sla_deadline = Column(DateTime(timezone=True), nullable=False)
    resolution_type = Column(String(50), nullable=True)
    resolution_details = Column(JSON, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    run = relationship("ReconciliationRun", back_populates="exceptions")
    normalised_txn = relationship("NormalisedTransaction")
    assigned_user = relationship("User", back_populates="exceptions")

# Index for exception management queue (partial index equivalent)
Index('idx_open_exceptions', ExceptionRecord.status, mysql_length=10)

class AuditLog(Base):
    __tablename__ = 'audit_log'
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    actor = Column(String(50), nullable=False)  # 'system' or user_id
    action_type = Column(String(20), nullable=False)  # 'INGEST', 'NORMALISE', 'MATCH', 'RESOLVE', 'ESCALATE', 'OVERRIDE'
    affected_records = Column(JSON, nullable=False)  # JSON list of record UUIDs
    before_state = Column(JSON, nullable=True)
    after_state = Column(JSON, nullable=True)
    rationale = Column(String(500), nullable=False)
    sha256_hash = Column(String(64), nullable=False)
