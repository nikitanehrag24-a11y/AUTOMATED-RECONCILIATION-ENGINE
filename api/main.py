from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, timedelta
from decimal import Decimal
import json
import uuid
from typing import List, Dict, Any, Optional, Tuple

from config.settings import settings
from config.loader import load_bank_config, load_all_bank_configs
from database.connection import get_db, init_db
from database.models import (
    RawTransaction, NormalisedTransaction, ReconciliationRun, 
    MatchResult, ExceptionRecord, AuditLog, User
)
from engine.parsers.csv_parser import CSVParser
from engine.parsers.mt940_parser import MT940Parser
from engine.parsers.camt053_parser import CAMT053Parser
from engine.normalisation import NormalisationEngine
from engine.matching import MatchingEngine
from engine.exceptions import ExceptionClassifier, EscalationManager
from engine.audit import AuditLogger
from engine.anomaly import AnomalyDetector
from api.schemas import RunTriggerRequest, ReconciliationRunResponse, ExceptionResponse, ExceptionResolveRequest, AuditLogResponse

app = FastAPI(
    title="Automated Reconciliation Engine API",
    description="Enterprise Multi-Bank Reconciliation API built for Zetheta Algorithms",
    version="1.0.0"
)

# Initialize DB tables on startup
@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/api/v1/files/upload", tags=["Ingestion"])
def upload_statement_file(
    bank_code: str = Form(..., example="HDFC"),
    format_type: str = Form(..., example="CSV"),
    source_type: str = Form(..., example="EXTERNAL"), # 'INTERNAL' (ledger) or 'EXTERNAL' (bank statement)
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Ingests and parses transaction files. Calculates the SHA-256 hash to prevent double upload,
    runs parsing validations, executes anomaly checks, normalises the transactions,
    and logs the action in the cryptographic audit trail.
    """
    # 1. Load config
    try:
        bank_config = load_bank_config(bank_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load configuration for bank {bank_code}: {e}")
        
    if format_type.upper() not in [f.upper() for f in bank_config.supported_formats]:
        raise HTTPException(status_code=400, detail=f"Format {format_type} not supported for bank {bank_code}")

    # Read file content
    content = file.file.read()
    
    # 2. Parse file
    format_type_upper = format_type.upper()
    if format_type_upper == "CSV":
        file_hash, raw_records, errors = CSVParser.parse(content, file.filename, bank_config)
    elif format_type_upper == "MT940":
        file_hash, raw_records, errors = MT940Parser.parse(content, file.filename, bank_config)
    elif format_type_upper == "CAMT053":
        file_hash, raw_records, errors = CAMT053Parser.parse(content, file.filename, bank_config)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format type: {format_type}")
        
    # Check if file has already been ingested
    duplicate_file = db.query(RawTransaction).filter(RawTransaction.file_hash == file_hash).first()
    if duplicate_file:
        raise HTTPException(status_code=409, detail=f"Duplicate upload: File with hash {file_hash} has already been ingested.")

    # Save raw transactions
    raw_tx_id = str(uuid.uuid4())
    raw_entry = RawTransaction(
        id=raw_tx_id,
        file_hash=file_hash,
        filename=file.filename,
        bank_code=bank_code,
        format_type=format_type_upper,
        raw_content=raw_records,
        ingestion_date=date.today()
    )
    db.add(raw_entry)
    db.commit()
    db.refresh(raw_entry)

    # 3. Normalise and run Anomaly Detection
    anomalies_flagged = 0
    normalised_inserts = []
    
    for raw_rec in raw_records:
        # Run anomaly detection rules
        # Amount Deviation (Z-score > 3.0)
        is_amount_anomaly, amount_msg = AnomalyDetector.detect_amount_deviation(
            db, bank_code, raw_rec.get("counterparty_name"), raw_rec["direction"], Decimal(raw_rec["amount"])
        )
        # Velocity Check (count > 200% of rolling hourly avg)
        # We parse the date first
        parsed_dt = NormalisationEngine.timezone_normalise(raw_rec["raw_date"], bank_config.timezone)
        is_velocity_anomaly, velocity_msg = AnomalyDetector.detect_velocity_alert(
            db, raw_rec.get("counterparty_name"), parsed_dt
        )
        
        # If anomalies found, flag in metadata
        anomaly_reasons = []
        if is_amount_anomaly:
            anomaly_reasons.append(amount_msg)
        if is_velocity_anomaly:
            anomaly_reasons.append(velocity_msg)
            
        if anomaly_reasons:
            anomalies_flagged += 1
            raw_rec["metadata"]["anomalous"] = True
            raw_rec["metadata"]["anomaly_reasons"] = anomaly_reasons

        # Normalise
        try:
            norm_rec = NormalisationEngine.normalise(raw_rec, bank_config, source_type.upper())
            norm_entry = NormalisedTransaction(
                id=str(uuid.uuid4()),
                raw_txn_id=raw_tx_id,
                source_type=norm_rec["source_type"],
                bank_code=norm_rec["bank_code"],
                txn_id=norm_rec["txn_id"],
                txn_date=norm_rec["txn_date"],
                amount=norm_rec["amount"],
                currency=norm_rec["currency"],
                direction=norm_rec["direction"],
                counterparty_name=norm_rec["counterparty_name"],
                counterparty_account=norm_rec["counterparty_account"],
                bank_ref=norm_rec["bank_ref"],
                narration=norm_rec["narration"],
                settlement_date=norm_rec["settlement_date"],
                is_reconciled=False
            )
            db.add(norm_entry)
            normalised_inserts.append(norm_entry.id)
        except Exception as norm_ex:
            errors.append({
                "row_number": raw_rec.get("metadata", {}).get("row_num", 0),
                "error_type": "NORMALISATION_ERROR",
                "error_message": str(norm_ex),
                "raw_line": json.dumps(raw_rec)
            })

    db.commit()

    # Log ingestion in cryptographic audit trail
    AuditLogger.log_event(
        db,
        actor="system_ingest",
        action_type="INGEST",
        affected_records_list=normalised_inserts,
        before_state_dict={},
        after_state_dict={"file_name": file.filename, "records_count": len(normalised_inserts), "anomalies_count": anomalies_flagged},
        rationale=f"Successfully ingested statement file {file.filename} for bank {bank_code}"
    )

    return {
        "status": "SUCCESS",
        "file_hash": file_hash,
        "filename": file.filename,
        "records_ingested": len(normalised_inserts),
        "anomalies_detected": anomalies_flagged,
        "parse_errors_count": len(errors),
        "errors": errors[:50]  # capped to avoid payload bloat
    }

@app.post("/api/v1/reconciliation/run", response_model=ReconciliationRunResponse, tags=["Reconciliation"])
def trigger_reconciliation_run(
    payload: RunTriggerRequest,
    db: Session = Depends(get_db)
):
    """
    Triggers the sequential matching pipeline (Exact -> Fuzzy -> Rule-Based) for a bank.
    Resolves matching records, raises classified exceptions, and logs metrics.
    """
    bank_code = payload.bank_code.upper()
    run_date = payload.run_date or date.today()
    
    # 1. Load config
    try:
        bank_config = load_bank_config(bank_code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bank config not found: {e}")

    start_time = datetime.utcnow()
    
    # Create run entry in DB
    run_id = str(uuid.uuid4())
    run_entry = ReconciliationRun(
        id=run_id,
        bank_code=bank_code,
        run_date=run_date,
        status="RUNNING"
    )
    db.add(run_entry)
    db.commit()

    # 2. Fetch unmatched transactions
    # Fetch all internal and external records for this bank that are unreconciled
    internal_query = db.query(NormalisedTransaction).filter(
        NormalisedTransaction.bank_code == bank_code,
        NormalisedTransaction.source_type == "INTERNAL",
        NormalisedTransaction.is_reconciled == False
    ).all()
    
    external_query = db.query(NormalisedTransaction).filter(
        NormalisedTransaction.bank_code == bank_code,
        NormalisedTransaction.source_type == "EXTERNAL",
        NormalisedTransaction.is_reconciled == False
    ).all()

    # Convert sqlalchemy objects to serializable dicts for the matching engine
    internal_pool = [{c.name: getattr(x, c.name) for c in x.__table__.columns} for x in internal_query]
    external_pool = [{c.name: getattr(x, c.name) for c in x.__table__.columns} for x in external_query]

    # 3. Match execution
    # Step 1: Exact Match
    exact_matches, unmatched_int, unmatched_ext = MatchingEngine.exact_match(internal_pool, external_pool)
    
    # Step 2: Fuzzy Match
    fuzzy_auto, fuzzy_review, unmatched_int, unmatched_ext = MatchingEngine.fuzzy_match(
        unmatched_int, unmatched_ext, bank_config
    )
    
    # Step 3: Rule-Based Match
    rule_simple, rule_splits, rule_netted, unmatched_int, unmatched_ext = MatchingEngine.rule_based_match(
        unmatched_int, unmatched_ext, bank_config
    )

    # 4. Save results to DB
    matched_ids = []
    
    # Save exact matches
    for col, ext in exact_matches:
        match_entry = MatchResult(
            run_id=run_id,
            internal_txn_id=col["id"],
            external_txn_id=ext["id"],
            match_type="EXACT",
            confidence_score=Decimal("1.00")
        )
        db.add(match_entry)
        db.query(NormalisedTransaction).filter(NormalisedTransaction.id.in_([col["id"], ext["id"]])).update({"is_reconciled": True}, synchronize_session=False)
        matched_ids.extend([col["id"], ext["id"]])
        
    # Save fuzzy auto-matches
    for col, ext, score in fuzzy_auto:
        match_entry = MatchResult(
            run_id=run_id,
            internal_txn_id=col["id"],
            external_txn_id=ext["id"],
            match_type="FUZZY",
            confidence_score=Decimal(str(score))
        )
        db.add(match_entry)
        db.query(NormalisedTransaction).filter(NormalisedTransaction.id.in_([col["id"], ext["id"]])).update({"is_reconciled": True}, synchronize_session=False)
        matched_ids.extend([col["id"], ext["id"]])

    # Save simple rule-based matches (e.g. date offset)
    for col, ext, rule_name in rule_simple:
        match_entry = MatchResult(
            run_id=run_id,
            internal_txn_id=col["id"],
            external_txn_id=ext["id"],
            match_type="RULE",
            rule_name=rule_name,
            confidence_score=Decimal("0.80")
        )
        db.add(match_entry)
        db.query(NormalisedTransaction).filter(NormalisedTransaction.id.in_([col["id"], ext["id"]])).update({"is_reconciled": True}, synchronize_session=False)
        matched_ids.extend([col["id"], ext["id"]])

    # Save split matches (many internal to one external)
    for ints, ext, rule_name in rule_splits:
        for col in ints:
            match_entry = MatchResult(
                run_id=run_id,
                internal_txn_id=col["id"],
                external_txn_id=ext["id"],
                match_type="RULE",
                rule_name=rule_name,
                confidence_score=Decimal("0.80")
            )
            db.add(match_entry)
            db.query(NormalisedTransaction).filter(NormalisedTransaction.id == col["id"]).update({"is_reconciled": True}, synchronize_session=False)
            matched_ids.append(col["id"])
        db.query(NormalisedTransaction).filter(NormalisedTransaction.id == ext["id"]).update({"is_reconciled": True}, synchronize_session=False)
        matched_ids.append(ext["id"])

    # Save netted matches (one internal to many external)
    for col, exts, rule_name in rule_netted:
        for ext in exts:
            match_entry = MatchResult(
                run_id=run_id,
                internal_txn_id=col["id"],
                external_txn_id=ext["id"],
                match_type="RULE",
                rule_name=rule_name,
                confidence_score=Decimal("0.80")
            )
            db.add(match_entry)
            db.query(NormalisedTransaction).filter(NormalisedTransaction.id == ext["id"]).update({"is_reconciled": True}, synchronize_session=False)
            matched_ids.append(ext["id"])
        db.query(NormalisedTransaction).filter(NormalisedTransaction.id == col["id"]).update({"is_reconciled": True}, synchronize_session=False)
        matched_ids.append(col["id"])

    # 5. Handle Fuzzy Review & Exceptions
    exception_inserts = []
    
    # Fuzzy Review -> Creates Exception (REFERENCE_TRUNCATED or AMOUNT_MISMATCH) for operator approval
    for col, ext, score in fuzzy_review:
        # Generate exception
        cat, sev, tier, deadline = ExceptionClassifier.classify(col, internal_pool, external_pool)
        exc_entry = ExceptionRecord(
            run_id=run_id,
            normalised_txn_id=col["id"],
            category=cat,
            severity=sev,
            status="UNDER_REVIEW",
            assigned_tier=tier,
            sla_deadline=deadline,
            resolution_details={"suggested_match_id": ext["id"], "confidence": score}
        )
        db.add(exc_entry)
        exception_inserts.append(exc_entry.id)

    # All other unmatched items (both internal and external) raise exceptions
    all_unmatched = unmatched_int + unmatched_ext
    for record in all_unmatched:
        cat, sev, tier, deadline = ExceptionClassifier.classify(record, internal_pool, external_pool)
        
        # Check if exception entry already exists for this txn to prevent constraint violations
        existing_exc = db.query(ExceptionRecord).filter(ExceptionRecord.normalised_txn_id == record["id"]).first()
        if not existing_exc:
            exc_entry = ExceptionRecord(
                run_id=run_id,
                normalised_txn_id=record["id"],
                category=cat,
                severity=sev,
                status="OPEN",
                assigned_tier=tier,
                sla_deadline=deadline
            )
            db.add(exc_entry)
            exception_inserts.append(exc_entry.id)

    db.commit()

    # Calculate run stats
    end_time = datetime.utcnow()
    duration = int((end_time - start_time).total_seconds() * 1000)
    
    total_processed = len(internal_pool) + len(external_pool)
    matched_count = len(matched_ids)
    exception_count = len(exception_inserts)
    match_rate = Decimal(str((matched_count / total_processed) * 100)) if total_processed > 0 else Decimal("0.00")
    
    db.query(ReconciliationRun).filter(ReconciliationRun.id == run_id).update({
        "status": "COMPLETED",
        "total_records": total_processed,
        "matched_records": matched_count,
        "exception_records": exception_count,
        "match_rate": match_rate,
        "processing_time_ms": duration
    })
    db.commit()

    # Log reconciliation execution in cryptographic audit trail
    AuditLogger.log_event(
        db,
        actor="system_match",
        action_type="MATCH",
        affected_records_list=matched_ids + exception_inserts,
        before_state_dict={},
        after_state_dict={"matched_count": matched_count, "exceptions_raised": exception_count, "match_rate": float(match_rate)},
        rationale=f"Triggered reconciliation run for bank {bank_code} on date {run_date}"
    )

    # Return updated run record
    run_db = db.query(ReconciliationRun).filter(ReconciliationRun.id == run_id).first()
    return run_db

@app.get("/api/v1/reconciliation/runs", response_model=List[ReconciliationRunResponse], tags=["Reconciliation"])
def list_reconciliation_runs(db: Session = Depends(get_db)):
    """Lists all reconciliation runs."""
    return db.query(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).all()

@app.get("/api/v1/exceptions", response_model=List[ExceptionResponse], tags=["Exceptions"])
def list_exceptions(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    bank_code: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Lists all exception records with dynamic filtering."""
    query = db.query(ExceptionRecord)
    if status:
        query = query.filter(ExceptionRecord.status == status)
    if severity:
        query = query.filter(ExceptionRecord.severity == severity)
    if category:
        query = query.filter(ExceptionRecord.category == category)
    if bank_code:
        # Join to normalised_transactions to filter by bank
        query = query.join(NormalisedTransaction).filter(NormalisedTransaction.bank_code == bank_code)
        
    return query.order_by(ExceptionRecord.created_at.desc()).all()

@app.put("/api/v1/exceptions/{id}/resolve", response_model=ExceptionResponse, tags=["Exceptions"])
def resolve_exception(
    id: str,
    payload: ExceptionResolveRequest,
    db: Session = Depends(get_db)
):
    """
    Manually resolves an exception. Matches two records, 
    clears exception status, and audit logs the rationale.
    """
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.id == id).first()
    if not exc:
        raise HTTPException(status_code=404, detail="Exception record not found")
        
    if exc.status in ("RESOLVED", "CLOSED"):
        raise HTTPException(status_code=400, detail="Exception is already resolved")

    # Perform manual matching logic
    affected_ids = [exc.normalised_txn_id]
    
    if payload.resolution_type == "MANUAL_MATCH":
        matching_txn_id = payload.resolution_details.get("matching_txn_id")
        if not matching_txn_id:
            raise HTTPException(status_code=400, detail="MANUAL_MATCH requires a matching_txn_id")
            
        # Reconcile both transactions
        db.query(NormalisedTransaction).filter(
            NormalisedTransaction.id.in_([exc.normalised_txn_id, matching_txn_id])
        ).update({"is_reconciled": True}, synchronize_session=False)
        
        # Create MatchResult
        match_entry = MatchResult(
            run_id=exc.run_id,
            internal_txn_id=exc.normalised_txn_id if db.query(NormalisedTransaction).filter(NormalisedTransaction.id == exc.normalised_txn_id).first().source_type == "INTERNAL" else matching_txn_id,
            external_txn_id=matching_txn_id if db.query(NormalisedTransaction).filter(NormalisedTransaction.id == exc.normalised_txn_id).first().source_type == "INTERNAL" else exc.normalised_txn_id,
            match_type="RULE",
            rule_name="MANUAL_OVERRIDE",
            confidence_score=Decimal("1.00")
        )
        db.add(match_entry)
        affected_ids.append(matching_txn_id)
        
    elif payload.resolution_type == "VOID_ENTRY":
        db.query(NormalisedTransaction).filter(NormalisedTransaction.id == exc.normalised_txn_id).update({"is_reconciled": True}, synchronize_session=False)
        
    exc.status = "RESOLVED"
    exc.resolution_type = payload.resolution_type
    exc.resolution_details = payload.resolution_details
    exc.resolved_at = datetime.utcnow()
    
    db.commit()
    db.refresh(exc)

    # Log in audit trail
    AuditLogger.log_event(
        db,
        actor="operator_user",
        action_type="RESOLVE",
        affected_records_list=affected_ids,
        before_state_dict={"status": "UNRECONCILED", "exception_id": id},
        after_state_dict={"status": "RECONCILED", "resolution_type": payload.resolution_type},
        rationale=payload.resolution_details.get("reason", "Operator resolved manually")
    )

    return exc

@app.get("/api/v1/audit", response_model=List[AuditLogResponse], tags=["Audit"])
def query_audit_trail(db: Session = Depends(get_db)):
    """Queries the append-only audit trail."""
    return db.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()

@app.get("/api/v1/audit/verify", tags=["Audit"])
def verify_audit_trail_integrity(db: Session = Depends(get_db)):
    """Triggers log verification to check if the audit log has been tampered with."""
    is_valid, violations = AuditLogger.verify_log_integrity(db)
    return {
        "status": "SECURE" if is_valid else "CORRUPTED",
        "integrity_verified": is_valid,
        "tampered_records_count": len(violations),
        "violations": violations
    }

@app.get("/api/v1/dashboard/summary", tags=["Dashboard"])
def get_dashboard_summary(db: Session = Depends(get_db)):
    """Returns real-time dashboard KPIs, trends, and exception aggregates."""
    # Count totals
    total_txns = db.query(func.count(NormalisedTransaction.id)).scalar() or 0
    matched_count = db.query(func.count(NormalisedTransaction.id)).filter(NormalisedTransaction.is_reconciled == True).scalar() or 0
    exception_count = db.query(func.count(ExceptionRecord.id)).scalar() or 0
    unresolved_count = db.query(func.count(ExceptionRecord.id)).filter(ExceptionRecord.status == "OPEN").scalar() or 0
    
    match_rate = (matched_count / total_txns) * 100 if total_txns > 0 else 0.0
    
    # Exceptions by Category
    cat_stats = db.query(ExceptionRecord.category, func.count(ExceptionRecord.id)).group_by(ExceptionRecord.category).all()
    exceptions_by_category = {cat: count for cat, count in cat_stats}
    
    # Exceptions by Severity
    sev_stats = db.query(ExceptionRecord.severity, func.count(ExceptionRecord.id)).group_by(ExceptionRecord.severity).all()
    exceptions_by_severity = {sev: count for sev, count in sev_stats}
    
    # Avg Processing time
    avg_proc = db.query(func.avg(ReconciliationRun.processing_time_ms)).scalar() or 0.0
    
    # Daily trend (last 7 runs)
    runs = db.query(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(7).all()
    daily_trend = [
        {"run_date": r.run_date.isoformat(), "match_rate": float(r.match_rate), "matched": r.matched_records, "exceptions": r.exception_records} 
        for r in reversed(runs)
    ]

    return {
        "total_transactions": total_txns,
        "matched_count": matched_count,
        "match_rate": round(match_rate, 2),
        "exception_count": exception_count,
        "exceptions_by_category": exceptions_by_category,
        "exceptions_by_severity": exceptions_by_severity,
        "unresolved_count": unresolved_count,
        "avg_processing_time": round(float(avg_proc), 2),
        "daily_trend": daily_trend
    }
