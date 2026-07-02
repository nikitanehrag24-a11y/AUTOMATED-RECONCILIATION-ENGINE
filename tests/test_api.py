import pytest
from fastapi.testclient import TestClient
from api.main import app
from database.connection import SessionLocal, init_db, engine
from database.models import Base, BankConfiguration
import os
import json

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    # Initialise in-memory DB tables
    init_db()
    
    # Seed bank configuration for testing
    db = SessionLocal()
    bank = BankConfiguration(
        bank_code="HDFC",
        bank_name="HDFC Test Bank",
        supported_formats=["CSV"],
        timezone="Asia/Kolkata",
        settlement_cycle="T+1",
        column_mappings={
            "txn_id": "Transaction Reference",
            "txn_date": "Value Date",
            "amount": "Transaction Amount",
            "currency": "Currency",
            "direction": "Transaction Type",
            "counterparty_name": "Beneficiary Name",
            "counterparty_account": "Beneficiary Account Number",
            "bank_ref": "UTR Number",
            "narration": "Description",
            "settlement_date": "Settlement Date"
        },
        format_deviations={},
        reconciliation_window_days=5
    )
    db.add(bank)
    db.commit()
    db.close()
    
    yield
    Base.metadata.drop_all(bind=engine)

def test_upload_file_api():
    # Prepare dummy CSV content
    csv_content = (
        "Transaction Reference,Value Date,Transaction Amount,Currency,Transaction Type,"
        "Beneficiary Name,Beneficiary Account Number,UTR Number,Description,Settlement Date\n"
        "TXN_API_101,2026-07-01,150.00,INR,Credit,BENE1,ACC1,UTR1,Sample description,2026-07-02\n"
    )
    
    # Upload external file
    files = {"file": ("test_api.csv", csv_content, "text/csv")}
    data = {"bank_code": "HDFC", "format_type": "CSV", "source_type": "EXTERNAL"}
    
    response = client.post("/api/v1/files/upload", data=data, files=files)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "SUCCESS"
    assert res_data["records_ingested"] == 1
    assert res_data["anomalies_detected"] == 0

def test_upload_duplicate_file_prevention():
    csv_content = (
        "Transaction Reference,Value Date,Transaction Amount,Currency,Transaction Type,"
        "Beneficiary Name,Beneficiary Account Number,UTR Number,Description,Settlement Date\n"
        "TXN_API_101,2026-07-01,150.00,INR,Credit,BENE1,ACC1,UTR1,Sample description,2026-07-02\n"
    )
    # Re-uploading the exact same content should raise conflict (409) due to matching SHA-256 hash
    files = {"file": ("test_api.csv", csv_content, "text/csv")}
    data = {"bank_code": "HDFC", "format_type": "CSV", "source_type": "EXTERNAL"}
    
    response = client.post("/api/v1/files/upload", data=data, files=files)
    assert response.status_code == 409
    assert "duplicate" in response.json()["detail"].lower()

def test_trigger_reconciliation_api():
    # Upload matching ledger records
    # Note: To exact match, internal direction should be complementary (DR for external CR Credit)
    csv_content = (
        "Transaction Reference,Value Date,Transaction Amount,Currency,Transaction Type,"
        "Beneficiary Name,Beneficiary Account Number,UTR Number,Description,Settlement Date\n"
        "TXN_API_101,2026-07-01,150.00,INR,Debit,BENE1,ACC1,UTR1,Sample description,2026-07-02\n"
    )
    files = {"file": ("test_internal.csv", csv_content, "text/csv")}
    data = {"bank_code": "HDFC", "format_type": "CSV", "source_type": "INTERNAL"}
    client.post("/api/v1/files/upload", data=data, files=files)
    
    # Trigger matching job
    payload = {"bank_code": "HDFC", "run_date": "2026-07-01"}
    response = client.post("/api/v1/reconciliation/run", json=payload)
    
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "COMPLETED"
    assert res_data["matched_records"] == 2 # 1 internal + 1 external matched
    assert res_data["total_records"] == 2

def test_dashboard_and_audit_api():
    # Verify dashboard returns metrics
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["total_transactions"] >= 2
    assert res_data["matched_count"] >= 2
    assert res_data["match_rate"] == 100.0
    
    # Verify audit query works
    response_audit = client.get("/api/v1/audit")
    assert response_audit.status_code == 200
    assert len(response_audit.json()) >= 2
    
    # Verify audit log verification
    response_verify = client.get("/api/v1/audit/verify")
    assert response_verify.status_code == 200
    assert response_verify.json()["integrity_verified"] is True
