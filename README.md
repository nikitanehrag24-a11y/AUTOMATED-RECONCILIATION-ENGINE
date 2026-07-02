# Automated Reconciliation Engine for Multi-Bank Settlement

**ReconOps - Settlement Guardian (v1.0.0)**  
*Proprietary Intellectual Property - Zetheta Algorithms Private Limited*

---

## 1. Project Overview
This repository contains a production-grade, enterprise-scale financial infrastructure system for automated transaction reconciliation. It is designed to ingest heterogeneous banking statements, normalise transactions into a canonical format, execute a high-performance three-tier matching pipeline, and manage exception routing with immutable cryptographic audit logging.

### Architectural Blueprint
```
                                +-----------------------------+
                                |  1. Ingestion Layer         |
                                |  (CSV, MT940, CAMT.053)     |
                                +--------------+--------------+
                                               |
                                               v
                                +-----------------------------+
                                |  2. Normalisation Chain     |
                                |  (pytz, Banker's Rounding)  |
                                +--------------+--------------+
                                               |
                                               v
                                +-----------------------------+
                                |  3. Three-Tier Matcher      |
                                |  (Exact -> Fuzzy -> Rules)  |
                                +-------+--------------+------+
                                        |              |
                                 Matched|              |Unmatched
                                        v              v
                        +----------------------+      +----------------------+
                        |  Cryptographic Log   |      | 18 Exception Classes |
                        |  (SHA-256 Chained)   |      | & 4 Escalation Tiers |
                        +----------------------+      +----------------------+
```

---

## 2. Key Features & Rectifications
- **Multi-Format Parsers**: Implements native streaming CSV parsers, state-machine SWIFT MT940 statement parsers, and recursive ISO 20022 CAMT.053 XML parsers.
- **Three-Tier Matching Engine**: Supports $O(N+M)$ exact lookup, fuzzy matching (Token Set Ratio, Jaro-Winkler, Levenshtein, candidate blocking), and rules matching (clearing day offset calendars, currency tolerances, split transaction and netting subset-sum backtracking).
- **Corrected Compliance SLAs**: Rectifies the direction reversal SLA to **30 minutes** with direct Tier 4 Compliance routing to mitigate fraud risk.
- **Precision Rounding**: Employs Banker's Rounding (`ROUND_HALF_EVEN`) matching RBI and NPCI standards.
- **Immutable Log Ledger**: Implements SHA-256 chained log hashes to enforce tamper-evident historical audit entries matching Section 128 of the Companies Act, 2013.

---

## 3. Quick-Start Guide

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ (if running locally)

### Option A: Running via Docker (Recommended)
1. Clone this repository (ensure it is kept PRIVATE).
2. Configure `.env` from `.env.example`.
3. Build and launch services:
   ```bash
   docker compose up --build -d
   ```
4. Access portals:
   - Streamlit Operations Dashboard: `http://localhost:8501`
   - FastAPI REST API Docs: `http://localhost:8000/docs`

### Option B: Running Locally (Development)
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Seed initial bank configurations and generate demo datasets:
   ```bash
   python -m scripts.seed_data
   ```
3. Start the FastAPI backend:
   ```bash
   python -m uvicorn api.main:app --reload --port 8000
   ```
4. Launch the Streamlit dashboard in a separate terminal:
   ```bash
   streamlit run dashboard/app.py --server.port 8501
   ```

---

## 4. Run Automated Test Suite
Our codebase achieves high test coverage (>80% cumulative coverage target) across parser logic, normalisation, math rounding, fuzzy blocking, and API endpoints.

```bash
# Run pytest locally
python -m pytest -v --cov=engine --cov=api
```

---

## 5. Sample Datasets
The seeding script generates sample data in the `datasets/` folder containing known match profiles for demonstration:
- `hdfc_statement.csv` vs `hdfc_internal.csv` (CSV upload test)
- `axis_statement.txt` vs `axis_internal.csv` (SWIFT MT940 test)
- `icici_statement.xml` vs `icici_internal.csv` (CAMT.053 XML test)

---

## 6. Intellectual Property & Confidentiality Notice
**STRICTLY PRIVATE & CONFIDENTIAL**  
This software, related documentation, data specifications, and concepts are the exclusive property of **Zetheta Algorithms Private Limited**. Copying, public redistribution, sharing on public code platforms (like public GitHub repositories, portfolios), or social media publication is strictly prohibited. Compliance with Non-Disclosure covenants is required throughout and after the project lifecycle.
