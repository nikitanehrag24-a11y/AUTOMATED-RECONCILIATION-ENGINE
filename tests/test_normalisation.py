import pytest
from datetime import datetime, date
from decimal import Decimal
import pytz
from engine.normalisation import NormalisationEngine
from config.loader import BankConfig

@pytest.fixture
def mock_bank_config():
    return BankConfig(
        bank_code="HDFC",
        bank_name="HDFC Test Bank",
        supported_formats=["CSV"],
        timezone="Asia/Kolkata",
        settlement_cycle="T+1"
    )

def test_timezone_normalise():
    # Test naive datetime string
    dt_str = "2026-07-01 10:30:00"
    dt_utc = NormalisationEngine.timezone_normalise(dt_str, "Asia/Kolkata")
    
    # 10:30 AM in India is 5:00 AM UTC (India is +5:30)
    assert dt_utc.year == 2026
    assert dt_utc.month == 7
    assert dt_utc.day == 1
    assert dt_utc.hour == 5
    assert dt_utc.minute == 0
    assert dt_utc.second == 0
    assert dt_utc.tzinfo == pytz.utc

def test_timezone_normalise_date_only():
    # Test date object
    d_val = date(2026, 7, 1)
    dt_utc = NormalisationEngine.timezone_normalise(d_val, "Asia/Kolkata")
    
    # Midnight on July 1st in India is 18:30 on June 30th UTC
    assert dt_utc.year == 2026
    assert dt_utc.month == 6
    assert dt_utc.day == 30
    assert dt_utc.hour == 18
    assert dt_utc.minute == 30

def test_currency_normalise():
    assert NormalisationEngine.currency_normalise("inr") == "INR"
    assert NormalisationEngine.currency_normalise(" USD ") == "USD"
    
    with pytest.raises(ValueError):
        NormalisationEngine.currency_normalise("US")
    with pytest.raises(ValueError):
        NormalisationEngine.currency_normalise("123")

def test_reference_clean():
    assert NormalisationEngine.reference_clean(" TXN-123/456 ") == "TXN123456"
    assert NormalisationEngine.reference_clean("") == "NONREF"
    assert NormalisationEngine.reference_clean(None) == "NONREF"

def test_amount_standardise_bankers_rounding():
    # Banker's rounding (Round half to even)
    # 2.50025 -> 2.5002 (since 2 is even)
    assert NormalisationEngine.amount_standardise("2.50025", "CR") == Decimal("2.5002")
    # 2.50035 -> 2.5004 (since 4 is even)
    assert NormalisationEngine.amount_standardise("2.50035", "CR") == Decimal("2.5004")
    
    # Negative amount should be standardised as absolute positive
    assert NormalisationEngine.amount_standardise("-100.50", "DR") == Decimal("100.50")

def test_counterparty_clean():
    assert NormalisationEngine.counterparty_clean("HDFC Bank Pvt. Ltd.") == "HDFC BANK PRIVATE LIMITED"
    assert NormalisationEngine.counterparty_clean("Reliance Industries Co.") == "RELIANCE INDUSTRIES COMPANY"
    assert NormalisationEngine.counterparty_clean("  John  Doe  ") == "JOHN DOE"
    assert NormalisationEngine.counterparty_clean(None) == "UNKNOWN"

def test_normalise_record_end_to_end(mock_bank_config):
    raw_record = {
        "txn_id": "TXN-101",
        "raw_date": "2026-07-01 10:30:00",
        "amount": "1234.56785", # Will round to 1234.5678 (8 is even)
        "currency": "inr",
        "direction": "CR",
        "counterparty_name": "Sharma Pvt. Ltd.",
        "counterparty_account": "123-456-789",
        "bank_ref": "UTR-111",
        "narration": " Test Payment ",
        "settlement_date": "2026-07-02"
    }
    
    normalised = NormalisationEngine.normalise(raw_record, mock_bank_config, "EXTERNAL")
    
    assert normalised["source_type"] == "EXTERNAL"
    assert normalised["bank_code"] == "HDFC"
    assert normalised["txn_id"] == "TXN101"
    assert normalised["amount"] == Decimal("1234.5678")
    assert normalised["currency"] == "INR"
    assert normalised["direction"] == "CR"
    assert normalised["counterparty_name"] == "SHARMA PRIVATE LIMITED"
    assert normalised["counterparty_account"] == "123456789"
    assert normalised["bank_ref"] == "UTR111"
    assert normalised["narration"] == "Test Payment"
    assert normalised["settlement_date"] == date(2026, 7, 2)
    assert normalised["is_reconciled"] is False
