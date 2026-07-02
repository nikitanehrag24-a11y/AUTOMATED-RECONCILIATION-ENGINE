import re
from datetime import datetime, date, time
from decimal import Decimal, ROUND_HALF_EVEN
import pytz
from typing import Dict, Any, Optional, Union
from engine.schemas import CanonicalTransaction
from config.loader import BankConfig

class NormalisationEngine:
    @staticmethod
    def timezone_normalise(dt_val: Union[datetime, str], source_tz_name: str) -> datetime:
        """
        Converts the source transaction timestamp to UTC based on the bank's local timezone.
        Preserves original timestamp information.
        """
        # If string, parse it
        if isinstance(dt_val, str):
            dt_val = dt_val.strip()
            # Try common datetime formats
            for fmt in (
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y",
                "%y%m%d"  # MT940 format
            ):
                try:
                    # Special check for YYMMDD
                    if fmt == "%y%m%d":
                        dt_parsed = datetime.strptime(dt_val, fmt)
                    else:
                        dt_parsed = datetime.strptime(dt_val, fmt)
                    dt_val = dt_parsed
                    break
                except ValueError:
                    continue
            
            if isinstance(dt_val, str):
                # If parsing failed, default to now
                dt_val = datetime.utcnow()

        # If it is date, convert to datetime at midnight
        if isinstance(dt_val, date) and not isinstance(dt_val, datetime):
            dt_val = datetime.combine(dt_val, time.min)

        # Localise
        try:
            local_tz = pytz.timezone(source_tz_name)
        except Exception:
            local_tz = pytz.utc

        if dt_val.tzinfo is None:
            # Naive datetime: assume it is in the bank's timezone
            dt_localised = local_tz.localize(dt_val)
        else:
            # Already aware
            dt_localised = dt_val.astimezone(local_tz)

        # Convert to UTC
        dt_utc = dt_localised.astimezone(pytz.utc)
        return dt_utc

    @staticmethod
    def currency_normalise(currency: str) -> str:
        """Validates ISO 4217 currency code."""
        curr_clean = currency.strip().upper()
        if len(curr_clean) != 3 or not curr_clean.isalpha():
            raise ValueError(f"Invalid currency code: {currency}")
        return curr_clean

    @staticmethod
    def reference_clean(reference: Optional[str]) -> str:
        """
        Strips whitespace, removes formatting characters, 
        uppercases, and standardises the reference format.
        """
        if not reference:
            return "NONREF"
        # Uppercase and remove special characters like hyphens, slashes, spaces
        ref_upper = reference.strip().upper()
        ref_cleaned = re.sub(r'[^A-Z0-9]', '', ref_upper)
        return ref_cleaned if ref_cleaned else "NONREF"

    @staticmethod
    def amount_standardise(amount: Any, direction: str) -> Decimal:
        """
        Converts amount to Decimal, applies banker's rounding 
        (ROUND_HALF_EVEN), and checks sign consistency.
        """
        if isinstance(amount, (str, float, int)):
            amount_dec = Decimal(str(amount))
        else:
            amount_dec = amount

        # Quantise to 4 decimal places using Banker's rounding
        amount_rounded = amount_dec.quantize(Decimal('0.0001'), rounding=ROUND_HALF_EVEN)
        
        # In reconciliation systems, amount is usually stored as absolute positive values, 
        # with DR/CR indicating the sign, or sign represents the direction.
        # We standardise amounts as positive numbers and check direction.
        if amount_rounded < 0:
            amount_rounded = abs(amount_rounded)
            
        return amount_rounded

    @staticmethod
    def counterparty_clean(name: Optional[str]) -> str:
        """Normalises case, expands abbreviations, removes special characters."""
        if not name:
            return "UNKNOWN"
            
        # Strip and uppercase
        name_clean = name.strip().upper()
        # Expand common abbreviations
        abbreviations = {
            r"\bPVT\b\.?": "PRIVATE",
            r"\bLTD\b\.?": "LIMITED",
            r"\bCO\b\.?": "COMPANY",
            r"\bCORP\b\.?": "CORPORATION",
            r"\bINC\b\.?": "INCORPORATED",
            r"\bINTL\b\.?": "INTERNATIONAL",
            r"\bMCH\b\.?": "MERCHANT"
        }
        for pattern, replacement in abbreviations.items():
            name_clean = re.sub(pattern, replacement, name_clean)
            
        # Remove non-alphanumeric noise characters except spaces
        name_clean = re.sub(r'[^A-Z0-9\s]', '', name_clean)
        # Collapse multiple spaces
        name_clean = re.sub(r'\s+', ' ', name_clean).strip()
        
        return name_clean if name_clean else "UNKNOWN"

    @classmethod
    def normalise(cls, raw_record: Dict[str, Any], bank_config: BankConfig, source_type: str) -> Dict[str, Any]:
        """
        Normalises a single raw transaction record into the canonical model format.
        """
        bank_code = bank_config.bank_code
        source_tz = bank_config.timezone
        
        # Timezone normalise
        txn_date_utc = cls.timezone_normalise(raw_record["raw_date"], source_tz)
        
        # Amount normalise
        amount = cls.amount_standardise(raw_record["amount"], raw_record["direction"])
        
        # Currency normalise
        currency = cls.currency_normalise(raw_record["currency"])
        
        # Reference clean
        txn_id = cls.reference_clean(raw_record["txn_id"])
        bank_ref = cls.reference_clean(raw_record.get("bank_ref") or raw_record["txn_id"])
        
        # Counterparty clean
        counterparty_name = cls.counterparty_clean(raw_record.get("counterparty_name"))
        counterparty_account = cls.reference_clean(raw_record.get("counterparty_account")) if raw_record.get("counterparty_account") else None
        
        # Narration
        narration = raw_record.get("narration", "")
        if narration:
            narration = narration.strip()
            
        # Settlement date
        settlement_date_val = None
        raw_settlement = raw_record.get("settlement_date")
        if raw_settlement:
            if isinstance(raw_settlement, str):
                try:
                    # parse date
                    settlement_date_val = datetime.strptime(raw_settlement.strip(), "%Y-%m-%d").date()
                except Exception:
                    # Try parsing datetime then converting to date
                    try:
                        settlement_date_val = cls.timezone_normalise(raw_settlement, source_tz).date()
                    except Exception:
                        settlement_date_val = txn_date_utc.date()
            elif isinstance(raw_settlement, (datetime, date)):
                settlement_date_val = raw_settlement if isinstance(raw_settlement, date) else raw_settlement.date()
        else:
            settlement_date_val = txn_date_utc.date()

        # Validate with Pydantic CanonicalTransaction to verify correctness
        canonical = CanonicalTransaction(
            txn_id=txn_id,
            txn_date=txn_date_utc,
            amount=amount,
            currency=currency,
            direction=raw_record["direction"],
            counterparty_name=counterparty_name,
            counterparty_account=counterparty_account,
            bank_ref=bank_ref,
            narration=narration,
            settlement_date=settlement_date_val
        )

        return {
            "source_type": source_type,
            "bank_code": bank_code,
            "txn_id": canonical.txn_id,
            "txn_date": canonical.txn_date,
            "amount": canonical.amount,
            "currency": canonical.currency,
            "direction": canonical.direction,
            "counterparty_name": canonical.counterparty_name,
            "counterparty_account": canonical.counterparty_account,
            "bank_ref": canonical.bank_ref,
            "narration": canonical.narration,
            "settlement_date": canonical.settlement_date,
            "is_reconciled": False
        }
