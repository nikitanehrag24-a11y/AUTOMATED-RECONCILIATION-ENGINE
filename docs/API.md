# REST API Reference Manual: Automated Reconciliation Engine

## 1. Overview
The Reconciliation API is built using **FastAPI** and runs on port `8000`. All requests must supply the appropriate headers for authentication if enabled.

---

## 2. Endpoints Summary

### 2.1 File Ingestion
- **POST** `/api/v1/files/upload`
  - Ingests bank statement or internal ledger files. Calculates file SHA-256 hash to prevent duplicate uploads.
  - **Form Data**:
    - `bank_code`: Bank config code (e.g. `HDFC`, `ICICI`, `AXIS`)
    - `format_type`: File format (`CSV`, `MT940`, `CAMT053`)
    - `source_type`: Source indicator (`INTERNAL` or `EXTERNAL`)
    - `file`: Ingestion payload (multipart file)
  - **Response (200 OK)**:
    ```json
    {
      "status": "SUCCESS",
      "file_hash": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
      "filename": "hdfc_statement.csv",
      "records_ingested": 125,
      "anomalies_detected": 2,
      "parse_errors_count": 0,
      "errors": []
    }
    ```

### 2.2 Execute Matching Pipeline
- **POST** `/api/v1/reconciliation/run`
  - Triggers the sequential matching engine (Exact -> Fuzzy -> Rule-Based) for a bank and run date.
  - **Request Body (JSON)**:
    ```json
    {
      "bank_code": "HDFC",
      "run_date": "2026-07-01"
    }
    ```
  - **Response (200 OK)**:
    ```json
    {
      "id": "e81d77a0-0bbf-4630-9b88-1d2be11802a4",
      "bank_code": "HDFC",
      "run_date": "2026-07-01",
      "status": "COMPLETED",
      "total_records": 250,
      "matched_records": 210,
      "exception_records": 40,
      "match_rate": 84.00,
      "processing_time_ms": 125,
      "created_at": "2026-07-01T12:00:00.000Z"
    }
    ```

### 2.3 Exception Management
- **GET** `/api/v1/exceptions`
  - Queries exceptions queue.
  - **Query Parameters**:
    - `status`: `OPEN`, `UNDER_REVIEW`, `RESOLVED`
    - `severity`: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`
    - `category`: `MISSING_INTERNAL`, `DIRECTION_REVERSAL`, etc.
    - `bank_code`: `HDFC`, etc.
  - **Response (200 OK)**:
    ```json
    [
      {
        "id": "90e66d40-cc5c-42cb-b1b1-bd2cc584b42b",
        "run_id": "e81d77a0-0bbf-4630-9b88-1d2be11802a4",
        "normalised_txn_id": "7fa12bf1-99bc-4ad2-bf01-cb993d98be22",
        "category": "DIRECTION_REVERSAL",
        "severity": "CRITICAL",
        "status": "OPEN",
        "assigned_tier": 4,
        "sla_deadline": "2026-07-01T12:30:00.000Z",
        "created_at": "2026-07-01T12:00:00.000Z"
      }
    ]
    ```

- **PUT** `/api/v1/exceptions/{id}/resolve`
  - Resolves an open exception.
  - **Request Body (JSON)**:
    ```json
    {
      "resolution_type": "MANUAL_MATCH",
      "resolution_details": {
        "matching_txn_id": "89ab3cdf-77bc-4ad9-bf90-cb883d98be11",
        "reason": "Operator matched UTR manually after bank verification"
      }
    }
    ```

### 2.4 Cryptographic Audit Trail
- **GET** `/api/v1/audit`
  - Fetches list of all system audit logs.
- **GET** `/api/v1/audit/verify`
  - Re-computes and checks all SHA-256 chain links to verify integrity.
  - **Response (200 OK)**:
    ```json
    {
      "status": "SECURE",
      "integrity_verified": true,
      "tampered_records_count": 0,
      "violations": []
    }
    ```

### 2.5 Real-time Dashboard Summary
- **GET** `/api/v1/dashboard/summary`
  - Fetch dashboard KPIs, categorisation charts, and rolling 7-day trend.
