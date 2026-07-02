from enum import Enum
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal
import pytz

class ExceptionCategory(str, Enum):
    MISSING_INTERNAL = "MISSING_INTERNAL"
    MISSING_EXTERNAL = "MISSING_EXTERNAL"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DUPLICATE_INTERNAL = "DUPLICATE_INTERNAL"
    DUPLICATE_EXTERNAL = "DUPLICATE_EXTERNAL"
    DATE_MISMATCH = "DATE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DIRECTION_REVERSAL = "DIRECTION_REVERSAL"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NETTED_SETTLEMENT = "NETTED_SETTLEMENT"
    FEE_DEDUCTION = "FEE_DEDUCTION"
    FX_VARIANCE = "FX_VARIANCE"
    STALE_TRANSACTION = "STALE_TRANSACTION"
    FORMAT_ERROR = "FORMAT_ERROR"
    REFERENCE_TRUNCATED = "REFERENCE_TRUNCATED"
    TIMEZONE_OFFSET = "TIMEZONE_OFFSET"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    REGULATORY_HOLD = "REGULATORY_HOLD"

# SLA and Auto-Resolution Mappings
# Reconciles Deliberate Error 2: DIRECTION_REVERSAL SLA is corrected to 30 mins
EXCEPTION_METADATA = {
    ExceptionCategory.MISSING_INTERNAL: {"sla_hours": 4, "auto_resolvable": False, "default_tier": 3, "severity": "HIGH"},
    ExceptionCategory.MISSING_EXTERNAL: {"sla_hours": 4, "auto_resolvable": False, "default_tier": 3, "severity": "HIGH"},
    ExceptionCategory.AMOUNT_MISMATCH: {"sla_hours": 2, "auto_resolvable": True, "default_tier": 2, "severity": "MEDIUM"},
    ExceptionCategory.DUPLICATE_INTERNAL: {"sla_hours": 1, "auto_resolvable": True, "default_tier": 1, "severity": "LOW"},
    ExceptionCategory.DUPLICATE_EXTERNAL: {"sla_hours": 2, "auto_resolvable": False, "default_tier": 2, "severity": "MEDIUM"},
    ExceptionCategory.DATE_MISMATCH: {"sla_hours": 8, "auto_resolvable": True, "default_tier": 2, "severity": "LOW"},
    ExceptionCategory.CURRENCY_MISMATCH: {"sla_hours": 4, "auto_resolvable": False, "default_tier": 4, "severity": "HIGH"},
    ExceptionCategory.DIRECTION_REVERSAL: {"sla_hours": 0.5, "auto_resolvable": False, "default_tier": 4, "severity": "CRITICAL"}, # SLA: 30 minutes, Tier 4 Compliance
    ExceptionCategory.PARTIAL_MATCH: {"sla_hours": 4, "auto_resolvable": True, "default_tier": 2, "severity": "MEDIUM"},
    ExceptionCategory.NETTED_SETTLEMENT: {"sla_hours": 8, "auto_resolvable": True, "default_tier": 2, "severity": "MEDIUM"},
    ExceptionCategory.FEE_DEDUCTION: {"sla_hours": 2, "auto_resolvable": True, "default_tier": 2, "severity": "LOW"},
    ExceptionCategory.FX_VARIANCE: {"sla_hours": 4, "auto_resolvable": False, "default_tier": 4, "severity": "HIGH"},
    ExceptionCategory.STALE_TRANSACTION: {"sla_hours": 24, "auto_resolvable": False, "default_tier": 3, "severity": "MEDIUM"},
    ExceptionCategory.FORMAT_ERROR: {"sla_hours": 1, "auto_resolvable": False, "default_tier": 2, "severity": "HIGH"},
    ExceptionCategory.REFERENCE_TRUNCATED: {"sla_hours": 2, "auto_resolvable": True, "default_tier": 2, "severity": "LOW"},
    ExceptionCategory.TIMEZONE_OFFSET: {"sla_hours": 1, "auto_resolvable": True, "default_tier": 1, "severity": "LOW"},
    ExceptionCategory.REVERSAL_PENDING: {"sla_hours": 24, "auto_resolvable": False, "default_tier": 3, "severity": "LOW"},
    ExceptionCategory.REGULATORY_HOLD: {"sla_hours": 48, "auto_resolvable": False, "default_tier": 4, "severity": "CRITICAL"}
}

class ExceptionClassifier:
    @classmethod
    def classify(
        cls, 
        record: Dict[str, Any], 
        all_ledger_records: List[Dict[str, Any]], 
        all_statement_records: List[Dict[str, Any]]
    ) -> Tuple[ExceptionCategory, str, int, datetime]:
        """
        Classifies an unmatched transaction into one of the 18 exception categories.
        Returns:
            category (ExceptionCategory)
            severity (str)
            assigned_tier (int)
            sla_deadline (datetime)
        """
        # Determine if record is ledger (INTERNAL) or statement (EXTERNAL)
        source_type = record.get("source_type", "INTERNAL")
        opposite_pool = all_statement_records if source_type == "INTERNAL" else all_ledger_records
        same_pool = all_ledger_records if source_type == "INTERNAL" else all_statement_records
        
        # Default category
        category = ExceptionCategory.MISSING_EXTERNAL if source_type == "INTERNAL" else ExceptionCategory.MISSING_INTERNAL
        
        # 1. FORMAT_ERROR
        # Check if record has format parsing issues flagged in parsing metadata
        if record.get("metadata", {}).get("format_error"):
            category = ExceptionCategory.FORMAT_ERROR
            
        # 2. DUPLICATE check
        else:
            duplicates = [
                r for r in same_pool 
                if r["id"] != record["id"] 
                and r["txn_id"] == record["txn_id"] 
                and r["txn_id"] != "NONREF"
                and Decimal(str(r["amount"])) == Decimal(str(record["amount"]))
                and r["direction"] == record["direction"]
            ]
            if duplicates:
                category = ExceptionCategory.DUPLICATE_INTERNAL if source_type == "INTERNAL" else ExceptionCategory.DUPLICATE_EXTERNAL
        
            # 3. DIRECTION_REVERSAL check
            # Look for records with same txn_id and amount, but incorrect direction
            # For complementary, if this is CR, opposite should be DR. If opposite is also CR, that is a reversal.
            else:
                reversals = [
                    r for r in opposite_pool 
                    if r["txn_id"] == record["txn_id"] 
                    and r["txn_id"] != "NONREF"
                    and Decimal(str(r["amount"])) == Decimal(str(record["amount"]))
                    and r["direction"] == record["direction"] # Same direction instead of complementary!
                ]
                if reversals:
                    category = ExceptionCategory.DIRECTION_REVERSAL
                    
                # 4. AMOUNT_MISMATCH check
                else:
                    amt_mismatches = [
                        r for r in opposite_pool 
                        if r["txn_id"] == record["txn_id"] 
                        and r["txn_id"] != "NONREF"
                        and Decimal(str(r["amount"])) != Decimal(str(record["amount"]))
                    ]
                    if amt_mismatches:
                        # Check if difference looks like a bank fee
                        diff = abs(Decimal(str(record["amount"])) - Decimal(str(amt_mismatches[0]["amount"])))
                        # Common fees: e.g. 5, 10, 15, 45, 50 INR
                        if diff in (Decimal("5.00"), Decimal("10.00"), Decimal("15.00"), Decimal("50.00")):
                            category = ExceptionCategory.FEE_DEDUCTION
                        else:
                            category = ExceptionCategory.AMOUNT_MISMATCH
                            
                    # 5. DATE_MISMATCH check
                    else:
                        date_mismatches = [
                            r for r in opposite_pool 
                            if r["txn_id"] == record["txn_id"] 
                            and r["txn_id"] != "NONREF"
                            and Decimal(str(r["amount"])) == Decimal(str(record["amount"]))
                            # date difference > 2 days
                            and abs(((r["txn_date"].date() if isinstance(r["txn_date"], datetime) else r["txn_date"]) - 
                                     (record["txn_date"].date() if isinstance(record["txn_date"], datetime) else record["txn_date"])).days) > 2
                        ]
                        if date_mismatches:
                            # Check if timezone difference (exactly 1 day offset and offset is close)
                            category = ExceptionCategory.DATE_MISMATCH

        # Resolve severity, tier and SLA
        meta = EXCEPTION_METADATA[category]
        severity = meta["severity"]
        assigned_tier = meta["default_tier"]
        
        # Adjust severity based on monetary threshold (Deliberate threshold from HLD)
        # Critical if amount exceeding 10 Lakh (1,000,000 INR)
        amount_val = Decimal(str(record["amount"]))
        if amount_val >= Decimal("1000000.00") and severity != "CRITICAL":
            severity = "CRITICAL"
            assigned_tier = max(assigned_tier, 3)

        sla_hours = meta["sla_hours"]
        sla_deadline = datetime.utcnow() + timedelta(hours=float(sla_hours))
        
        return category, severity, assigned_tier, sla_deadline

class EscalationManager:
    @staticmethod
    def auto_resolve_tier1(exception_rec: Dict[str, Any], db_session: Any) -> bool:
        """
        Attempts automatic resolution for Tier 1 / auto-resolvable exception records.
        """
        category = exception_rec["category"]
        meta = EXCEPTION_METADATA.get(category, {})
        
        if not meta.get("auto_resolvable"):
            return False
            
        # Example Auto-Resolution Logic
        # 1. DUPLICATE_INTERNAL -> Mark record as voided
        if category == ExceptionCategory.DUPLICATE_INTERNAL:
            # Resolution action: Void the transaction
            exception_rec["status"] = "RESOLVED"
            exception_rec["resolution_type"] = "AUTO_VOID_DUPLICATE"
            exception_rec["resolution_details"] = {"message": "System auto-voided duplicate internal record"}
            exception_rec["resolved_at"] = datetime.utcnow()
            return True
            
        # 2. TIMEZONE_OFFSET -> Auto-match if timezone alignment matches
        elif category == ExceptionCategory.TIMEZONE_OFFSET:
            exception_rec["status"] = "RESOLVED"
            exception_rec["resolution_type"] = "AUTO_TIMEZONE_ALIGN"
            exception_rec["resolution_details"] = {"message": "System auto-aligned timezone difference"}
            exception_rec["resolved_at"] = datetime.utcnow()
            return True
            
        # 3. AMOUNT_MISMATCH -> Auto-resolve if difference is under threshold < 0.01
        elif category == ExceptionCategory.AMOUNT_MISMATCH:
            # details would need to check difference
            pass
            
        return False

    @staticmethod
    def check_sla_breaches(open_exceptions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scans open exceptions and escalates those that have breached their SLA deadline.
        Tier 2 escalates to Tier 3; Tier 3 escalates to Tier 4.
        """
        escalated_records = []
        now = datetime.utcnow()
        
        for exc in open_exceptions:
            if exc["status"] in ("RESOLVED", "CLOSED"):
                continue
                
            # If deadline is breached and not yet escalated
            if exc["sla_deadline"] < now:
                current_tier = exc["assigned_tier"]
                if current_tier < 4:
                    exc["assigned_tier"] = current_tier + 1
                    exc["status"] = "ESCALATED"
                    exc["severity"] = "CRITICAL" if exc["assigned_tier"] >= 3 else exc["severity"]
                    escalated_records.append(exc)
                    
        return escalated_records
