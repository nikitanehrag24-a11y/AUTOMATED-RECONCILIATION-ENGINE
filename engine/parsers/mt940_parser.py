import re
from typing import List, Dict, Any, Tuple
from decimal import Decimal
from datetime import datetime

class MT940Parser:
    @staticmethod
    def parse(
        file_content: bytes, 
        filename: str, 
        bank_config: Any
    ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parses a SWIFT MT940 statement file using a state-machine parser.
        Returns:
            file_hash (str)
            valid_records (list of dicts, ready to be stored in raw_transactions)
            validation_errors (list of error records for exception handling)
        """
        import hashlib
        sha256 = hashlib.sha256()
        sha256.update(file_content)
        file_hash = sha256.hexdigest()

        # Decodes file
        try:
            text = file_content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text = file_content.decode('latin-1')
            except Exception as e:
                return file_hash, [], [{"row": 0, "error": f"Failed to decode file: {e}", "raw_line": ""}]

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        
        valid_records = []
        validation_errors = []
        
        # State machine variables
        bank_ref = ""
        account_id = ""
        statement_num = ""
        currency = "INR"  # default fallback
        
        current_txn = None
        narration_buffer = []
        
        # Regex patterns
        # Tag format e.g. :61:
        tag_pattern = re.compile(r"^:([0-9]{2}[A-Z]?):(.*)$")
        
        # Tag 61 pattern:
        # Value Date: 6 digits (YYMMDD)
        # Entry Date: optional 4 digits (MMDD)
        # Debit/Credit: R? (reversal) + D or C
        # Funds Code: optional 1 letter
        # Amount: digits with comma or period decimal separator
        # Transaction Type: 1 letter (S/N/F) + 3 chars identification code
        # Reference: Up to 16 chars (or longer for extended reference deviation)
        # Supplementary details: optional // followed by chars
        txn_line_pattern = re.compile(
            r"^(\d{6})(\d{4})?(R?[DC])([A-Z])?(\d+[\.,]\d{0,4})([A-Z]{4}|[SINF].{3})?([^/]*)(//.*)?$"
        )
        
        row_num = 0
        
        def save_current_txn():
            nonlocal current_txn, narration_buffer
            if current_txn:
                # Flush narration buffer
                if narration_buffer:
                    narration_str = " ".join(narration_buffer)
                    current_txn["narration"] = narration_str
                    
                    # Try to extract counterparty name and account from narration based on bank deviations
                    # e.g., /NAME/John/ACCT/1234 or unstructured
                    # Standard SWIFT :86: structured fields format: /tag/value
                    subfields = parse_86_subfields(narration_str, bank_config)
                    if subfields.get("counterparty_name"):
                        current_txn["counterparty_name"] = subfields["counterparty_name"]
                    if subfields.get("counterparty_account"):
                        current_txn["counterparty_account"] = subfields["counterparty_account"]
                
                valid_records.append(current_txn)
                current_txn = None
                narration_buffer = []

        for line in lines:
            row_num += 1
            tag_match = tag_pattern.match(line)
            
            if tag_match:
                tag = tag_match.group(1)
                value = tag_match.group(2).strip()
                
                # Save previous transaction before moving to a new tag that marks transaction boundaries
                # Note: :61: starts a new transaction. Other tag boundaries also save.
                if tag in ('20', '25', '28C', '60F', '61', '62F'):
                    save_current_txn()
                
                if tag == '20':
                    bank_ref = value
                elif tag == '25':
                    account_id = value
                elif tag == '28C':
                    statement_num = value
                elif tag == '60F':
                    # Opening balance, e.g., C260701INR100000,00
                    # Extract currency (starts at index 7, 3 characters)
                    if len(value) >= 10:
                        currency = value[7:10].upper()
                elif tag == '61':
                    # Parse transaction line
                    match = txn_line_pattern.match(value)
                    if not match:
                        validation_errors.append({
                            "row_number": row_num,
                            "error_type": "FORMAT_ERROR",
                            "error_message": f"Malformed :61: transaction line: '{value}'",
                            "raw_line": line
                        })
                        continue
                        
                    raw_val_date = match.group(1)
                    raw_entry_date = match.group(2)
                    dc_indicator = match.group(3)
                    funds_code = match.group(4)
                    raw_amount = match.group(5)
                    txn_type = match.group(6)
                    reference = match.group(7).strip()
                    supp_details = match.group(8)
                    
                    # Process indicators
                    direction = "DR" if "D" in dc_indicator else "CR"
                    
                    # Convert amount (handle decimal comma or period separators)
                    # This addresses Deliberate Error 1 / Deviation 2: Period decimal separator
                    amount_str = raw_amount.replace(",", ".")
                    try:
                        amount = Decimal(amount_str)
                    except Exception:
                        validation_errors.append({
                            "row_number": row_num,
                            "error_type": "FORMAT_ERROR",
                            "error_message": f"Invalid amount in :61: line: '{raw_amount}'",
                            "raw_line": line
                        })
                        continue
                        
                    # Standardise reference (remove non-alphanumeric, default to UTR or NOREF)
                    ref_cleaned = reference.replace("NONREF", "").replace("NOREF", "").strip()
                    if not ref_cleaned and bank_ref:
                        ref_cleaned = bank_ref
                    
                    current_txn = {
                        "txn_id": ref_cleaned if ref_cleaned else f"MT940-{row_num}-{raw_val_date}",
                        "raw_date": raw_val_date,
                        "amount": str(amount),
                        "currency": currency,
                        "direction": direction,
                        "counterparty_name": None,
                        "counterparty_account": None,
                        "bank_ref": bank_ref if bank_ref else ref_cleaned,
                        "narration": "",
                        "settlement_date": None,
                        "metadata": {
                            "row_num": row_num,
                            "filename": filename,
                            "ingestion_timestamp": datetime.utcnow().isoformat(),
                            "account_id": account_id,
                            "statement_num": statement_num
                        }
                    }
                    
                elif tag == '86':
                    # Transaction narration/details
                    if current_txn:
                        narration_buffer.append(value)
                    else:
                        # File level info
                        pass
                elif tag == '62F':
                    # Closing balance - marks statement end
                    pass
            else:
                # Continuation line of previous tag (especially :86: fields)
                if current_txn and narration_buffer:
                    # Append continuation text
                    narration_buffer.append(line)
        
        # Save last transaction in buffer
        save_current_txn()
        
        return file_hash, valid_records, validation_errors

def parse_86_subfields(narration: str, bank_config: Any) -> Dict[str, str]:
    """
    Parses unstructured MT940 :86: narration into subfields based on bank configurations.
    Addresses Deliberate Error 1: unstructured :86: extraction variations.
    """
    results = {}
    
    # Try common subfield structures, e.g. /NAME/John Doe/ACCT/1234
    # or /CNP/John Doe/IBAN/1234
    # Or using standard SWIFT /F86/ code patterns
    subfield_pattern = re.compile(r"/([A-Z]{3,4})/([^/]*)")
    matches = subfield_pattern.findall(narration)
    if matches:
        subfield_dict = {tag.upper(): val.strip() for tag, val in matches}
        # HDFC / ICICI subfield tags
        name_keys = ["NAME", "CNP", "BENE", "CRED", "DEBT"]
        acct_keys = ["ACCT", "IBAN", "CNPA", "BENEA"]
        
        for k in name_keys:
            if k in subfield_dict:
                results["counterparty_name"] = subfield_dict[k]
                break
        for k in acct_keys:
            if k in subfield_dict:
                results["counterparty_account"] = subfield_dict[k]
                break
                
    # Fallback to simple regex if not slash-structured
    if not results.get("counterparty_name"):
        # Match common patterns: "Transfer from NAME" or "BENE:NAME"
        name_match = re.search(r"(?:BENE|NAME|FROM|TO):\s*([^,;/\n]*)", narration, re.IGNORECASE)
        if name_match:
            results["counterparty_name"] = name_match.group(1).strip()
            
    if not results.get("counterparty_account"):
        acct_match = re.search(r"(?:ACCT|A/C|ACC|IBAN):\s*([A-Z0-9]*)", narration, re.IGNORECASE)
        if acct_match:
            results["counterparty_account"] = acct_match.group(1).strip()
            
    return results
