import streamlit as st
import pandas as pd
import altair as alt
from datetime import datetime, date
from decimal import Decimal
import json

# Setup page layout
st.set_page_config(page_title="ReconOps - Settlement Guardian", layout="wide", initial_sidebar_state="expanded")

# Direct database access imports for fast loading
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from database.connection import SessionLocal
from database.models import (
    NormalisedTransaction, ReconciliationRun, ExceptionRecord, 
    AuditLog, RawTransaction, BankConfiguration
)
from reports.generator import ReportGenerator
from engine.audit import AuditLogger
from api.main import trigger_reconciliation_run
from api.schemas import RunTriggerRequest

db = SessionLocal()

# Custom Styling
st.markdown("""
    <style>
        .main-header { font-size: 32px; font-weight: bold; color: #0f172a; margin-bottom: 20px; }
        .sub-text { color: #64748b; margin-top: -15px; margin-bottom: 30px; }
        .card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; text-align: center; }
        .card-title { font-size: 16px; color: #64748b; font-weight: 500; }
        .card-value { font-size: 28px; font-weight: bold; color: #0284c7; margin-top: 5px; }
    </style>
""", unsafe_type_html=True)

# Sidebar navigation
st.sidebar.title("ReconOps Gateway")
st.sidebar.write("Settlement Guardian Operator Portal")
menu = st.sidebar.radio(
    "Navigation",
    ["Overview Dashboard", "File Ingestion Monitor", "Run Reconciliation", "Exception Queue", "Audit & Security Center", "Compliance Reports"]
)

st.sidebar.markdown("---")
st.sidebar.write("**Proprietary Intellectual Property**")
st.sidebar.info("Attribution: Zetheta Algorithms Private Limited. Under Strict NDA.")

# Retrieve bank list
banks = [b.bank_code for b in db.query(BankConfiguration).all()]
if not banks:
    # Seed default banks if not present
    from database.connection import init_db
    init_db()
    for code, name, formats in [("HDFC", "HDFC Bank", ["CSV", "MT940"]), ("ICICI", "ICICI Bank", ["CSV", "CAMT053"]), ("AXIS", "Axis Bank", ["CSV", "MT940"])]:
        if not db.query(BankConfiguration).filter(BankConfiguration.bank_code == code).first():
            db.add(BankConfiguration(
                bank_code=code, bank_name=name, supported_formats=formats, 
                timezone="Asia/Kolkata", settlement_cycle="T+1", reconciliation_window_days=5
            ))
    db.commit()
    banks = [b.bank_code for b in db.query(BankConfiguration).all()]

# ----------------- PAGE 1: OVERVIEW DASHBOARD -----------------
if menu == "Overview Dashboard":
    st.markdown("<div class='main-header'>Reconciliation Overview</div>", unsafe_type_html=True)
    st.markdown("<div class='sub-text'>Real-time status of multi-bank settlements and exception queues.</div>", unsafe_type_html=True)
    
    # Query metrics
    total_txns = db.query(func.count(NormalisedTransaction.id)).scalar() or 0
    matched = db.query(func.count(NormalisedTransaction.id)).filter(NormalisedTransaction.is_reconciled == True).scalar() or 0
    open_exc = db.query(func.count(ExceptionRecord.id)).filter(ExceptionRecord.status == "OPEN").scalar() or 0
    sla_breaches = db.query(func.count(ExceptionRecord.id)).filter(
        ExceptionRecord.status == "OPEN", ExceptionRecord.sla_deadline < datetime.utcnow()
    ).scalar() or 0
    
    match_rate = (matched / total_txns) * 100 if total_txns > 0 else 0.0
    
    # Renders KPI columns
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"<div class='card'><div class='card-title'>Total Transactions</div><div class='card-value'>{total_txns:,}</div></div>", unsafe_type_html=True)
    with c2:
        st.markdown(f"<div class='card'><div class='card-title'>Match Rate</div><div class='card-value'>{match_rate:.2f}%</div></div>", unsafe_type_html=True)
    with c3:
        st.markdown(f"<div class='card'><div class='card-title'>Open Exceptions</div><div class='card-value'>{open_exc}</div></div>", unsafe_type_html=True)
    with c4:
        st.markdown(f"<div class='card'><div class='card-title'>SLA Violations</div><div class='card-value' style='color:#ef4444;'>{sla_breaches}</div></div>", unsafe_type_html=True)

    st.write("### Reconciliation Run History")
    # Query run history
    runs = db.query(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(10).all()
    if runs:
        run_data = []
        for r in runs:
            run_data.append({
                "Run ID": r.id[:8],
                "Bank Code": r.bank_code,
                "Run Date": r.run_date.isoformat(),
                "Status": r.status,
                "Total Records": r.total_records,
                "Matched": r.matched_records,
                "Exceptions": r.exception_records,
                "Match Rate": f"{float(r.match_rate):.2f}%",
                "Execution Time (ms)": r.processing_time_ms
            })
        df_runs = pd.DataFrame(run_data)
        st.dataframe(df_runs, use_container_width=True)
        
        # Plot match rate trend
        st.write("### Match Rate Trend")
        df_runs_reversed = df_runs.iloc[::-1]
        chart = alt.Chart(df_runs_reversed).mark_line(point=True).encode(
            x='Run Date:O',
            y='Total Records:Q',
            color='Bank Code:N',
            tooltip=['Bank Code', 'Match Rate', 'Total Records']
        ).properties(height=300)
        st.altair_chart(chart, use_container_width=True)
    else:
        st.info("No reconciliation runs executed yet. Ingest files and run reconciliation.")

# ----------------- PAGE 2: FILE INGESTION MONITOR -----------------
elif menu == "File Ingestion Monitor":
    st.markdown("<div class='main-header'>File Ingestion Center</div>", unsafe_type_html=True)
    st.markdown("<div class='sub-text'>Upload and parse bank statement files (CSV, SWIFT MT940, ISO 20022 CAMT.053).</div>", unsafe_type_html=True)

    c1, c2 = st.columns([1, 2])
    with c1:
        st.write("### Upload New Statement")
        bank_code = st.selectbox("Select Bank Partner", banks)
        format_type = st.selectbox("File Format", ["CSV", "MT940", "CAMT053"])
        source_type = st.selectbox("Ledger Source", ["EXTERNAL", "INTERNAL"], help="INTERNAL represents ERP ledger, EXTERNAL represents Bank statement line")
        uploaded_file = st.file_uploader("Choose statement file", type=["csv", "txt", "xml"])
        
        if uploaded_file is not None:
            if st.button("Process File", use_container_width=True):
                # Trigger upload logic
                from api.main import upload_statement_file
                class MockUploadFile:
                    def __init__(self, file_obj, name):
                        self.file = file_obj
                        self.filename = name
                        
                mock_file = MockUploadFile(uploaded_file, uploaded_file.name)
                try:
                    res = upload_statement_file(
                        bank_code=bank_code,
                        format_type=format_type,
                        source_type=source_type,
                        file=mock_file,
                        db=db
                    )
                    st.success(f"Successfully processed: Ingested {res['records_ingested']} records. Anomalies detected: {res['anomalies_detected']}.")
                    if res["parse_errors_count"] > 0:
                        st.warning(f"Raised {res['parse_errors_count']} parsing warnings. Check logs.")
                except Exception as e:
                    st.error(f"Failed to process statement: {e}")
                    
    with c2:
        st.write("### Ingestion History")
        raw_files = db.query(RawTransaction).order_by(RawTransaction.created_at.desc()).limit(15).all()
        if raw_files:
            file_data = []
            for rf in raw_files:
                # Count normalized count
                norm_count = db.query(func.count(NormalisedTransaction.id)).filter(
                    NormalisedTransaction.raw_txn_id == rf.id
                ).scalar() or 0
                
                file_data.append({
                    "Filename": rf.filename,
                    "Bank": rf.bank_code,
                    "Format": rf.format_type,
                    "Ingestion Date": rf.ingestion_date.isoformat(),
                    "SHA-256 Hash": f"{rf.file_hash[:16]}...",
                    "Parsed Records": norm_count,
                    "Upload Time": rf.created_at.strftime("%H:%M:%S")
                })
            st.dataframe(pd.DataFrame(file_data), use_container_width=True)
        else:
            st.info("No statement files ingested yet.")

# ----------------- PAGE 3: RUN RECONCILIATION -----------------
elif menu == "Run Reconciliation":
    st.markdown("<div class='main-header'>Run Reconciliation Engine</div>", unsafe_type_html=True)
    st.markdown("<div class='sub-text'>Manually run matching matching rules on all unmatched ledger and statement entries.</div>", unsafe_type_html=True)
    
    c1, c2 = st.columns([1, 2])
    with c1:
        st.write("### Match Configurations")
        bank_code = st.selectbox("Bank", banks)
        run_date = st.date_input("Run Date", date.today())
        
        # Count unmatched pool size
        unmatched_int = db.query(func.count(NormalisedTransaction.id)).filter(
            NormalisedTransaction.bank_code == bank_code,
            NormalisedTransaction.source_type == "INTERNAL",
            NormalisedTransaction.is_reconciled == False
        ).scalar() or 0
        
        unmatched_ext = db.query(func.count(NormalisedTransaction.id)).filter(
            NormalisedTransaction.bank_code == bank_code,
            NormalisedTransaction.source_type == "EXTERNAL",
            NormalisedTransaction.is_reconciled == False
        ).scalar() or 0
        
        st.write(f"**Unreconciled Internal Ledger Pool:** {unmatched_int} records")
        st.write(f"**Unreconciled External Statement Pool:** {unmatched_ext} records")
        
        if unmatched_int == 0 and unmatched_ext == 0:
            st.info("Unreconciled pool is empty. Please upload statements first.")
        else:
            if st.button("Execute Match Pipeline", use_container_width=True):
                with st.spinner("Executing Matching: Exact -> Fuzzy -> Rule-Based..."):
                    payload = RunTriggerRequest(bank_code=bank_code, run_date=run_date)
                    try:
                        res = trigger_reconciliation_run(payload, db)
                        st.success("Reconciliation Run Completed successfully!")
                        st.balloons()
                        st.experimental_rerun()
                    except Exception as ex:
                        st.error(f"Reconciliation run failed: {ex}")
                        
    with c2:
        st.write("### System Configurations")
        configs = db.query(BankConfiguration).all()
        config_data = []
        for c in configs:
            config_data.append({
                "Bank Code": c.bank_code,
                "Bank Name": c.bank_name,
                "Supported Formats": ", ".join(c.supported_formats),
                "Timezone": c.timezone,
                "Settlement Cycle": c.settlement_cycle,
                "Reconciliation Window (Days)": c.reconciliation_window_days
            })
        st.dataframe(pd.DataFrame(config_data), use_container_width=True)

# ----------------- PAGE 4: EXCEPTION QUEUE -----------------
elif menu == "Exception Queue":
    st.markdown("<div class='main-header'>Exception Management Queue</div>", unsafe_type_html=True)
    st.markdown("<div class='sub-text'>Review, resolve, and audit unmatched items.</div>", unsafe_type_html=True)

    # Filtering options
    status_filter = st.selectbox("Status Filter", ["OPEN", "UNDER_REVIEW", "RESOLVED"])
    
    # Query exceptions
    exceptions = db.query(ExceptionRecord).filter(ExceptionRecord.status == status_filter).all()
    
    if exceptions:
        exc_data = []
        for exc in exceptions:
            txn = db.query(NormalisedTransaction).filter(NormalisedTransaction.id == exc.normalised_txn_id).first()
            if not txn:
                continue
                
            exc_data.append({
                "Exception ID": exc.id,
                "Category": exc.category,
                "Severity": exc.severity,
                "Bank Code": txn.bank_code,
                "Direction": txn.direction,
                "Amount": float(txn.amount),
                "Currency": txn.currency,
                "UTR / Reference": txn.txn_id,
                "Date": txn.txn_date.strftime("%Y-%m-%d"),
                "SLA Deadline": exc.sla_deadline.strftime("%Y-%m-%d %H:%M UTC")
            })
            
        df_exc = pd.DataFrame(exc_data)
        st.dataframe(df_exc, use_container_width=True)
        
        st.write("### Resolve Exception Record")
        selected_exc_id = st.selectbox("Select Exception ID to Resolve", df_exc["Exception ID"].tolist())
        
        if selected_exc_id:
            # Fetch specific exception details
            selected_exc = db.query(ExceptionRecord).filter(ExceptionRecord.id == selected_exc_id).first()
            selected_txn = db.query(NormalisedTransaction).filter(NormalisedTransaction.id == selected_exc.normalised_txn_id).first()
            
            st.info(f"**Transaction Ref:** {selected_txn.txn_id} | **Amount:** {selected_txn.amount} {selected_txn.currency} | **Direction:** {selected_txn.direction} | **Narration:** {selected_txn.narration}")
            
            # Suggest matching candidates from the database (opposite source, same amount within tolerance)
            lookup_source = "EXTERNAL" if selected_txn.source_type == "INTERNAL" else "INTERNAL"
            lookup_dir = "CR" if selected_txn.direction == "DR" else "DR"
            
            candidates = db.query(NormalisedTransaction).filter(
                NormalisedTransaction.bank_code == selected_txn.bank_code,
                NormalisedTransaction.source_type == lookup_source,
                NormalisedTransaction.direction == lookup_dir,
                NormalisedTransaction.is_reconciled == False
            ).all()
            
            candidate_options = {}
            for c in candidates:
                key = f"{c.txn_id} | Amount: {c.amount} {c.currency} | Date: {c.txn_date.strftime('%Y-%m-%d')}"
                candidate_options[key] = c.id
                
            resolution_type = st.selectbox("Resolution Type", ["MANUAL_MATCH", "VOID_ENTRY"])
            
            matching_txn_id = None
            if resolution_type == "MANUAL_MATCH":
                if candidate_options:
                    selected_candidate_key = st.selectbox("Select Matching Candidate Record", list(candidate_options.keys()))
                    matching_txn_id = candidate_options[selected_candidate_key]
                else:
                    st.warning("No unmatched candidates found in the database. You can still void the entry or check other banks.")
            
            reason = st.text_input("Resolution Rationale / Audit Justification", value="Verified manually by operator")
            
            if st.button("Submit Resolution", use_container_width=True):
                # Call api resolution logic
                from api.main import resolve_exception
                from api.schemas import ExceptionResolveRequest
                
                details = {"reason": reason}
                if matching_txn_id:
                    details["matching_txn_id"] = matching_txn_id
                    
                payload = ExceptionResolveRequest(
                    resolution_type=resolution_type,
                    resolution_details=details
                )
                try:
                    resolve_exception(id=selected_exc_id, payload=payload, db=db)
                    st.success(f"Exception {selected_exc_id[:8]} resolved successfully!")
                    st.experimental_rerun()
                except Exception as ex:
                    st.error(f"Failed to resolve exception: {ex}")
    else:
        st.success(f"Great job! No open exceptions in status '{status_filter}'")

# ----------------- PAGE 5: AUDIT & SECURITY CENTER -----------------
elif menu == "Audit & Security Center":
    st.markdown("<div class='main-header'>Cryptographic Audit Log & Security Center</div>", unsafe_type_html=True)
    st.markdown("<div class='sub-text'>Tamper-evident, blockchain-style audit trail of all manual and automated reconciliation events.</div>", unsafe_type_html=True)

    # Integrity verification trigger
    st.write("### Cryptographic Chain Integrity Verification")
    if st.button("Execute Log Integrity Verification Check", use_container_width=True):
        is_valid, violations = AuditLogger.verify_log_integrity(db)
        if is_valid:
            st.success("Integrity Verification: SECURE. No tampering detected. All SHA-256 chain links match.")
        else:
            st.error(f"Integrity Verification: COMPROMISED. Found {len(violations)} tampered log records! Details: {violations}")

    st.write("### Historical Log Entries")
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()
    if logs:
        log_data = []
        for l in logs:
            log_data.append({
                "Time": l.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "Actor": l.actor,
                "Action": l.action_type,
                "Affected Records": len(l.affected_records),
                "Rationale": l.rationale,
                "SHA-256 Signature": f"{l.sha256_hash[:16]}..."
            })
        st.dataframe(pd.DataFrame(log_data), use_container_width=True)
    else:
        st.info("Audit log is empty.")

# ----------------- PAGE 6: COMPLIANCE REPORTS -----------------
elif menu == "Compliance Reports":
    st.markdown("<div class='main-header'>Compliance Report Center</div>", unsafe_type_html=True)
    st.markdown("<div class='sub-text'>Generate and download statutory compliance reports.</div>", unsafe_type_html=True)

    st.write("### Generate Compliance Reports")
    report_type = st.selectbox(
        "Report Template",
        ["Daily Reconciliation Report", "Exception Ageing Analysis Report", "Audit & Compliance Sign-off Report"]
    )
    
    bank_code = st.selectbox("Bank Code (if applicable)", banks)
    run_date = st.date_input("Run Date (if applicable)", date.today())
    
    if st.button("Generate Report HTML", use_container_width=True):
        if report_type == "Daily Reconciliation Report":
            html_content = ReportGenerator.generate_daily_reconciliation_report(db, bank_code, run_date)
        elif report_type == "Exception Ageing Analysis Report":
            html_content = ReportGenerator.generate_exception_ageing_report(db)
        else:
            html_content = ReportGenerator.generate_audit_compliance_report(db)
            
        st.success("Report generated successfully!")
        
        # Renders direct preview
        st.write("#### Report Preview")
        st.components.v1.html(html_content, height=450, scrolling=True)
        
        # Download button
        st.download_button(
            label="Download HTML Report File",
            data=html_content,
            file_name=f"{report_type.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.html",
            mime="text/html",
            use_container_width=True
        )
