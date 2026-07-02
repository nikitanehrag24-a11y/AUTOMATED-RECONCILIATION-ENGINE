import pytest
from decimal import Decimal
from engine.parsers.csv_parser import CSVParser
from engine.parsers.mt940_parser import MT940Parser
from engine.parsers.camt053_parser import CAMT053Parser
from config.loader import BankConfig, CSVColumnMapping, FormatDeviations, MT940Deviations, CAMT053Deviations

# Mock BankConfig for CSV
@pytest.fixture
def hdfc_test_config():
    return BankConfig(
        bank_code="HDFC",
        bank_name="HDFC Test Bank",
        supported_formats=["CSV", "MT940"],
        timezone="Asia/Kolkata",
        settlement_cycle="T+1",
        column_mappings=CSVColumnMapping(
            txn_id="Transaction Reference",
            txn_date="Value Date",
            amount="Transaction Amount",
            currency="Currency",
            direction="Transaction Type",
            counterparty_name="Beneficiary Name",
            counterparty_account="Beneficiary Account Number",
            bank_ref="UTR Number",
            narration="Description",
            settlement_date="Settlement Date"
        ),
        format_deviations=FormatDeviations(
            mt940=MT940Deviations(
                decimal_separator=",",
                date_format="YYMMDD",
                omit_funds_code=True,
                narration_line_range="4-6",
                account_subfield_pos="line 2"
            )
        )
    )

@pytest.fixture
def axis_test_config():
    return BankConfig(
        bank_code="AXIS",
        bank_name="Axis Test Bank",
        supported_formats=["CSV"],
        timezone="Asia/Kolkata",
        settlement_cycle="T+1",
        column_mappings=CSVColumnMapping(
            txn_id=["Axis Ref 1", "Axis Ref 2"],
            txn_date="Date of Transaction",
            amount="Debit/Credit Amount",
            currency="Transaction Currency",
            direction="D/C Type",
            counterparty_name="Beneficiary",
            counterparty_account="Beneficiary Account",
            bank_ref="Axis UTR",
            narration="Narration String",
            settlement_date="Settlement Booking Date"
        )
    )

@pytest.fixture
def icici_test_config():
    return BankConfig(
        bank_code="ICICI",
        bank_name="ICICI Bank Limited",
        supported_formats=["CAMT053"],
        timezone="Asia/Kolkata",
        settlement_cycle="T+0",
        format_deviations=FormatDeviations(
            camt053=CAMT053Deviations(
                namespace="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08",
                balance_type="CLBD"
            )
        )
    )

# --- CSV parser tests ---
def test_csv_parser_valid(hdfc_test_config):
    csv_data = (
        "Transaction Reference,Value Date,Transaction Amount,Currency,Transaction Type,"
        "Beneficiary Name,Beneficiary Account Number,UTR Number,Description,Settlement Date\n"
        "TXN001,2026-07-01,1500.50,INR,Credit,John Doe,123456789,UTR999,Test Narration,2026-07-02\n"
        "TXN002,2026-07-01,500.00,INR,Debit,Jane Doe,987654321,UTR888,Test Withdrawal,2026-07-02\n"
    ).encode('utf-8')
    
    file_hash, valid, errors = CSVParser.parse(csv_data, "test.csv", hdfc_test_config)
    assert len(valid) == 2
    assert len(errors) == 0
    assert valid[0]["txn_id"] == "TXN001"
    assert valid[0]["amount"] == "1500.50"
    assert valid[0]["currency"] == "INR"
    assert valid[0]["direction"] == "CR"

def test_csv_parser_split_reference_axis(axis_test_config):
    csv_data = (
        "Axis Ref 1,Axis Ref 2,Date of Transaction,Debit/Credit Amount,Transaction Currency,D/C Type,"
        "Beneficiary,Beneficiary Account,Axis UTR,Narration String,Settlement Booking Date\n"
        "AXIS,999123,2026-07-01,25000.00,INR,Credit,Sharma Corp,888822,UTR777,Payment,2026-07-01\n"
    ).encode('utf-8')
    
    file_hash, valid, errors = CSVParser.parse(csv_data, "axis_test.csv", axis_test_config)
    assert len(valid) == 1
    assert valid[0]["txn_id"] == "AXIS999123"

# --- MT940 parser tests ---
def test_mt940_parser_valid(hdfc_test_config):
    mt940_data = (
        ":20:HDFCREF\n"
        ":25:1234567890\n"
        ":28C:001\n"
        ":60F:C260701INR100000,00\n"
        ":61:2607010701CR1500,50S123REF999\n"
        ":86:/NAME/John Doe/ACCT/987654321/DESC/Transfer\n"
        ":62F:C260701INR101500,50\n"
    ).encode('utf-8')
    
    file_hash, valid, errors = MT940Parser.parse(mt940_data, "statement.txt", hdfc_test_config)
    assert len(valid) == 1
    assert len(errors) == 0
    assert valid[0]["txn_id"] == "REF999"
    assert valid[0]["amount"] == "1500.50"
    assert valid[0]["currency"] == "INR"
    assert valid[0]["direction"] == "CR"
    assert valid[0]["counterparty_name"] == "John Doe"
    assert valid[0]["counterparty_account"] == "987654321"

def test_mt940_parser_deviations(hdfc_test_config):
    # Deviation 1: Period decimal separator (instead of comma)
    # Deviation 2: Missing funds code (after CR/DR indicator)
    mt940_data = (
        ":20:HDFCREF\n"
        ":25:1234567890\n"
        ":28C:001\n"
        ":60F:C260701INR100000.00\n"
        ":61:2607010701DR500.75S123REF888\n"
        ":86:/NAME/Jane Doe/ACCT/111222333/DESC/Direct Debit\n"
        ":62F:C260701INR99499.25\n"
    ).encode('utf-8')
    
    file_hash, valid, errors = MT940Parser.parse(mt940_data, "statement.txt", hdfc_test_config)
    assert len(valid) == 1
    assert len(errors) == 0
    assert valid[0]["txn_id"] == "REF888"
    assert valid[0]["amount"] == "500.75"
    assert valid[0]["direction"] == "DR"
    assert valid[0]["counterparty_name"] == "Jane Doe"

# --- CAMT.053 parser tests ---
def test_camt053_parser_valid(icici_test_config):
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <Stmt>
      <Acct>
        <Id>
          <IBAN>IN1234567890</IBAN>
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
        <Amt Ccy="INR">3500.25</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt>
          <DtTm>2026-07-01T12:00:00Z</DtTm>
        </BookgDt>
        <NtryDtls>
          <TxDtls>
            <Refs>
              <EndToEndId>TXNCAMT999</EndToEndId>
            </Refs>
            <RltdPties>
              <Dbtr>
                <Nm>Alice Enterprises</Nm>
              </Dbtr>
              <DbtrAcct>
                <Id>
                  <IBAN>IN9876543210</IBAN>
                </Id>
              </DbtrAcct>
            </RltdPties>
          </TxDtls>
        </NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
""".encode('utf-8')
    
    file_hash, valid, errors = CAMT053Parser.parse(xml_data, "camt.xml", icici_test_config)
    assert len(valid) == 1
    assert len(errors) == 0
    assert valid[0]["txn_id"] == "TXNCAMT999"
    assert valid[0]["amount"] == "3500.25"
    assert valid[0]["direction"] == "CR"
    assert valid[0]["counterparty_name"] == "Alice Enterprises"
    assert valid[0]["counterparty_account"] == "IN9876543210"
