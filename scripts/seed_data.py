import os
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from database.connection import SessionLocal, init_db
from database.models import BankConfiguration

def seed_database_configs():
    print("Seeding bank configurations...")
    init_db()
    db = SessionLocal()
    
    # 1. HDFC Config
    if not db.query(BankConfiguration).filter(BankConfiguration.bank_code == "HDFC").first():
        db.add(BankConfiguration(
            bank_code="HDFC",
            bank_name="HDFC Bank Limited",
            supported_formats=["CSV", "MT940"],
            timezone="Asia/Kolkata",
            settlement_cycle="T+1",
            reconciliation_window_days=5,
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
            format_deviations={
                "mt940": {
                    "decimal_separator": ",",
                    "date_format": "YYMMDD",
                    "omit_funds_code": True,
                    "narration_line_range": "4-6",
                    "account_subfield_pos": "line 2"
                }
            }
        ))
        
    # 2. ICICI Config
    if not db.query(BankConfiguration).filter(BankConfiguration.bank_code == "ICICI").first():
        db.add(BankConfiguration(
            bank_code="ICICI",
            bank_name="ICICI Bank Limited",
            supported_formats=["CSV", "CAMT053"],
            timezone="Asia/Kolkata",
            settlement_cycle="T+0",
            reconciliation_window_days=5,
            column_mappings={
                "txn_id": "ICICI Transaction ID",
                "txn_date": "Booking Date",
                "amount": "Amount",
                "currency": "Transaction Currency",
                "direction": "DR/CR Indicator",
                "counterparty_name": "Counterparty Name",
                "counterparty_account": "Counterparty IBAN",
                "bank_ref": "Host Reference Number",
                "narration": "Remarks",
                "settlement_date": "Value Date"
            },
            format_deviations={
                "camt053": {
                    "namespace": "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08",
                    "balance_type": "CLBD"
                }
            }
        ))

    # 3. Axis Config
    if not db.query(BankConfiguration).filter(BankConfiguration.bank_code == "AXIS").first():
        db.add(BankConfiguration(
            bank_code="AXIS",
            bank_name="Axis Bank Limited",
            supported_formats=["CSV", "MT940"],
            timezone="Asia/Kolkata",
            settlement_cycle="T+1",
            reconciliation_window_days=5,
            column_mappings={
                "txn_id": ["Axis Ref 1", "Axis Ref 2"],
                "txn_date": "Date of Transaction",
                "amount": "Debit/Credit Amount",
                "currency": "Transaction Currency",
                "direction": "D/C Type",
                "counterparty_name": "Beneficiary",
                "counterparty_account": "Beneficiary Account",
                "bank_ref": "Axis UTR",
                "narration": "Narration String",
                "settlement_date": "Settlement Booking Date"
            },
            format_deviations={
                "mt940": {
                    "decimal_separator": ".",
                    "date_format": "YYMMDD",
                    "omit_funds_code": False,
                    "narration_line_range": "3-5",
                    "account_subfield_pos": "line 3"
                }
            }
        ))

    db.commit()
    db.close()
    print("Bank configurations seeded successfully!")

def create_sample_datasets():
    print("Creating sample dataset files in datasets/...")
    os.makedirs("datasets", exist_ok=True)
    
    # --- 1. HDFC CSV files ---
    # External Bank Statement (CR/DR indicator: Credit / Debit)
    hdfc_external_content = (
        "Transaction Reference,Value Date,Transaction Amount,Currency,Transaction Type,"
        "Beneficiary Name,Beneficiary Account Number,UTR Number,Description,Settlement Date\n"
        "TXNHDFC001,2026-07-01,15000.00,INR,Credit,Sharma Enterprises,11223344,UTRHDFC001,Customer payment,2026-07-01\n" # Exact match
        "TXNHDFC002,2026-07-01,25000.50,INR,Credit,Verma Corp,55667788,UTRHDFC002,Invoice settlement,2026-07-01\n" # Amount mismatch internal
        "TXNHDFC003,2026-07-01,12000.00,INR,Debit,Salaries,99990000,UTRHDFC003,Salaries payroll,2026-07-01\n" # Direction reversal internal
        "TXNHDFC004,2026-07-01,50.00,INR,Debit,HDFC Bank,11223344,UTRHDFC004,Service Charge,2026-07-01\n" # Fee deduction (unmatched)
        "TXNHDFC005,2026-07-01,4500.00,INR,Credit,Rohan Gupta,44332211,UTRHDFC005,UPI transfer,2026-07-01\n" # Date offset match
    )
    with open("datasets/hdfc_statement.csv", "w", encoding="utf-8") as f:
        f.write(hdfc_external_content)

    # Internal Ledger (CR/DR complementary: Debit / Credit)
    hdfc_internal_content = (
        "Transaction Reference,Value Date,Transaction Amount,Currency,Transaction Type,"
        "Beneficiary Name,Beneficiary Account Number,UTR Number,Description,Settlement Date\n"
        "TXNHDFC001,2026-07-01,15000.00,INR,Debit,Sharma Enterprises,11223344,UTRHDFC001,Customer payment,2026-07-01\n" # Match TXNHDFC001
        "TXNHDFC002,2026-07-01,25000.00,INR,Debit,Verma Corp,55667788,UTRHDFC002,Invoice settlement,2026-07-01\n" # Mismatch (25000.00 vs 25000.50)
        "TXNHDFC003,2026-07-01,12000.00,INR,Debit,Salaries,99990000,UTRHDFC003,Salaries payroll,2026-07-01\n" # Reversal (both Debit/DR)
        "TXNHDFC005,2026-07-03,4500.00,INR,Debit,Rohan Gupta,44332211,UTRHDFC005,UPI transfer,2026-07-03\n" # Date offset (T+2)
    )
    with open("datasets/hdfc_internal.csv", "w", encoding="utf-8") as f:
        f.write(hdfc_internal_content)

    # --- 2. AXIS MT940 file ---
    axis_mt940_content = (
        ":20:AXISREF\n"
        ":25:999888777\n"
        ":28C:001\n"
        ":60F:C260701INR50000.00\n"
        ":61:2607010701CR3500.00S123REF001\n"
        ":86:/NAME/Aman Gupta/ACCT/12345/DESC/Direct deposit\n"
        ":61:2607010701DR500.00S123REF002\n"
        ":86:/NAME/Merchant/ACCT/67890/DESC/POS purchase\n"
        ":62F:C260701INR53000.00\n"
    )
    with open("datasets/axis_statement.txt", "w", encoding="utf-8") as f:
        f.write(axis_mt940_content)
        
    axis_internal_content = (
        "Axis Ref 1,Axis Ref 2,Date of Transaction,Debit/Credit Amount,Transaction Currency,D/C Type,"
        "Beneficiary,Beneficiary Account,Axis UTR,Narration String,Settlement Booking Date\n"
        "REF,001,2026-07-01,3500.00,INR,Debit,Aman Gupta,12345,REF001,Direct deposit,2026-07-01\n"
        "REF,002,2026-07-01,500.00,INR,Credit,Merchant,67890,REF002,POS purchase,2026-07-01\n"
    )
    with open("datasets/axis_internal.csv", "w", encoding="utf-8") as f:
        f.write(axis_internal_content)

    # --- 3. ICICI CAMT.053 XML file ---
    icici_xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <Stmt>
      <Acct>
        <Id>
          <IBAN>INICICI0099</IBAN>
        </Id>
      </Acct>
      <Bal>
        <Tp>
          <CdOrPrtry>
            <Cd>CLBD</Cd>
          </CdOrPrtry>
        </Tp>
        <Dt>
          <Dt>2026-07-01</Dt>
        </Dt>
      </Bal>
      <Ntry>
        <Amt Ccy="INR">6000.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt>
          <DtTm>2026-07-01T12:00:00Z</DtTm>
        </BookgDt>
        <NtryDtls>
          <TxDtls>
            <Refs>
              <EndToEndId>TXNICICI888</EndToEndId>
            </Refs>
            <RltdPties>
              <Dbtr>
                <Nm>Rahul Dev</Nm>
              </Dbtr>
              <DbtrAcct>
                <Id>
                  <IBAN>IN88887777</IBAN>
                </Id>
              </DbtrAcct>
            </RltdPties>
          </TxDtls>
        </NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""
    with open("datasets/icici_statement.xml", "w", encoding="utf-8") as f:
        f.write(icici_xml_content)

    icici_internal_content = (
        "ICICI Transaction ID,Booking Date,Amount,Transaction Currency,DR/CR Indicator,"
        "Counterparty Name,Counterparty IBAN,Host Reference Number,Remarks,Value Date\n"
        "TXNICICI888,2026-07-01,6000.00,INR,Debit,Rahul Dev,IN88887777,UTR888,Remarks,2026-07-01\n"
    )
    with open("datasets/icici_internal.csv", "w", encoding="utf-8") as f:
        f.write(icici_internal_content)

    print("Sample datasets created successfully!")

if __name__ == "__main__":
    seed_database_configs()
    create_sample_datasets()
