import pytest
from datetime import datetime, date, timedelta
from decimal import Decimal
from engine.matching import MatchingEngine
from config.loader import BankConfig

@pytest.fixture
def mock_bank_config():
    return BankConfig(
        bank_code="HDFC",
        bank_name="HDFC Test Bank",
        supported_formats=["CSV"],
        timezone="Asia/Kolkata",
        settlement_cycle="T+1",
        reconciliation_window_days=5
    )

def test_string_similarity():
    # Jaro-Winkler test
    assert MatchingEngine.calculate_string_similarity("TXN123456", "TXN123456") == 1.0
    assert MatchingEngine.calculate_string_similarity("TXN123456", "TXN123457") >= 0.90
    assert MatchingEngine.calculate_string_similarity("ABC", "XYZ") == 0.0

def test_token_set_ratio():
    assert MatchingEngine.calculate_token_set_ratio("RELIANCE PRIVATE LIMITED", "RELIANCE RETAIL PRIVATE LIMITED") >= 0.80

def test_confidence_score_calculation(mock_bank_config):
    # Perfect match
    internal = {
        "txn_id": "TXN1001",
        "txn_date": datetime(2026, 7, 1, 10, 0, 0),
        "amount": Decimal("5000.0000"),
        "currency": "INR",
        "direction": "CR",
        "counterparty_name": "JOHN DOE",
        "counterparty_account": "123456"
    }
    external = {
        "id": "ext-1",
        "txn_id": "TXN1001",
        "txn_date": datetime(2026, 7, 1, 10, 30, 0), # within same day
        "amount": Decimal("5000.0000"),
        "currency": "INR",
        "direction": "DR", # complementary direction
        "counterparty_name": "JOHN DOE",
        "counterparty_account": "123456"
    }
    score = MatchingEngine.calculate_confidence_score(internal, external, mock_bank_config)
    assert score >= 0.95

    # Direction blocker check: same direction (CR and CR) should yield 0.0 score
    bad_dir_ext = dict(external, direction="CR")
    score_bad_dir = MatchingEngine.calculate_confidence_score(internal, bad_dir_ext, mock_bank_config)
    assert score_bad_dir == 0.0

def test_exact_matching():
    internal_pool = [
        {"id": "int-1", "txn_id": "TXN001", "amount": Decimal("1000.00"), "currency": "INR", "direction": "DR", "txn_date": datetime(2026, 7, 1)},
        {"id": "int-2", "txn_id": "TXN002", "amount": Decimal("2000.00"), "currency": "INR", "direction": "DR", "txn_date": datetime(2026, 7, 1)},
    ]
    external_pool = [
        {"id": "ext-1", "txn_id": "TXN001", "amount": Decimal("1000.00"), "currency": "INR", "direction": "CR", "txn_date": datetime(2026, 7, 1)}, # complementary CR matches internal DR
        {"id": "ext-2", "txn_id": "TXN003", "amount": Decimal("3000.00"), "currency": "INR", "direction": "CR", "txn_date": datetime(2026, 7, 1)},
    ]
    
    matches, unmatched_int, unmatched_ext = MatchingEngine.exact_match(internal_pool, external_pool)
    assert len(matches) == 1
    assert matches[0][0]["id"] == "int-1"
    assert matches[0][1]["id"] == "ext-1"
    assert len(unmatched_int) == 1
    assert len(unmatched_ext) == 1

def test_fuzzy_matching(mock_bank_config):
    internal_pool = [
        # Reference differs slightly (Jaro-Winkler should be high)
        {"id": "int-1", "txn_id": "TXN9999", "amount": Decimal("1500.00"), "currency": "INR", "direction": "DR", "txn_date": datetime(2026, 7, 1), "counterparty_name": "JOHN DOE"},
    ]
    external_pool = [
        {"id": "ext-1", "txn_id": "TXN9998", "amount": Decimal("1500.00"), "currency": "INR", "direction": "CR", "txn_date": datetime(2026, 7, 1), "counterparty_name": "JOHN DOE"},
    ]
    
    auto_matches, review_matches, unmatched_int, unmatched_ext = MatchingEngine.fuzzy_match(
        internal_pool, external_pool, mock_bank_config, auto_match_threshold=0.85, review_threshold=0.60
    )
    
    # Highly similar reference and exact amount/direction/name should exceed 0.85
    assert len(auto_matches) == 1
    assert auto_matches[0][0]["id"] == "int-1"
    assert auto_matches[0][1]["id"] == "ext-1"
    assert len(unmatched_int) == 0

def test_rule_based_matching_date_offset(mock_bank_config):
    # Date offset within 5 days window
    internal = {"id": "int-1", "txn_id": "TXN100", "amount": Decimal("100.00"), "currency": "INR", "direction": "DR", "txn_date": datetime(2026, 7, 1)}
    external = {"id": "ext-1", "txn_id": "TXN100", "amount": Decimal("100.00"), "currency": "INR", "direction": "CR", "txn_date": datetime(2026, 7, 4)} # 3 days later
    
    simple, splits, netted, unmatched_int, unmatched_ext = MatchingEngine.rule_based_match(
        [internal], [external], mock_bank_config
    )
    assert len(simple) == 1
    assert simple[0][2] == "DATE_OFFSET_RULE"

def test_rule_based_matching_split(mock_bank_config):
    # One external matched to sum of two internal
    # Ext: 5000.00
    # Ints: 2000.00 + 3000.00
    external = {"id": "ext-1", "txn_id": "TXNSPLIT", "amount": Decimal("5000.00"), "currency": "INR", "direction": "CR", "txn_date": datetime(2026, 7, 1)}
    internal_1 = {"id": "int-1", "txn_id": "TXN_PART1", "amount": Decimal("2000.00"), "currency": "INR", "direction": "DR", "txn_date": datetime(2026, 7, 1)}
    internal_2 = {"id": "int-2", "txn_id": "TXN_PART2", "amount": Decimal("3000.00"), "currency": "INR", "direction": "DR", "txn_date": datetime(2026, 7, 1)}
    
    simple, splits, netted, unmatched_int, unmatched_ext = MatchingEngine.rule_based_match(
        [internal_1, internal_2], [external], mock_bank_config
    )
    
    assert len(splits) == 1
    assert splits[0][1]["id"] == "ext-1"
    assert len(splits[0][0]) == 2
    assert {x["id"] for x in splits[0][0]} == {"int-1", "int-2"}
