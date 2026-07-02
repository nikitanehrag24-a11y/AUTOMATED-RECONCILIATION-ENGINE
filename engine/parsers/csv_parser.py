import csv
import hashlib
import io
from typing import List, Dict, Any, Tuple, Union
from decimal import Decimal
from datetime import datetime

class CSVParser:
    @staticmethod
    def compute_sha256(file_content: bytes) -> str:
        """Computes the SHA-256 hash of a file's content for integrity verification."""
        sha256 = hashlib.sha256()
        sha256.update(file_content)
        return sha256.hexdigest()

    @classmethod
    def parse(
        cls, 
        file_content: bytes, 
        filename: str, 
        bank_config: Any
    ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parses a bank-specific CSV file based on its configuration mapping.
        Returns:
            file_hash (str)
            valid_records (list of dicts, ready to be stored in raw_transactions)
            validation_errors (list of error records for exception handling)
        """
        file_hash = cls.compute_sha256(file_content)
        
        # Read content using text stream
        text_stream = io.StringIO(file_content.decode('utf-8', errors='replace'))
        
        # Detect delimiter (default to comma)
        reader = csv.reader(text_stream)
        
        # Read header
        try:
            headers = next(reader)
        except StopIteration:
            return file_hash, [], [{"row": 0, "error": "Empty file", "raw_line": ""}]
            
        headers = [h.strip() for h in headers]
        
        # Build index mapping for easy retrieval
        header_map = {h: idx for idx, h in enumerate(headers)}
        
        mappings = bank_config.column_mappings
        if not mappings:
            raise ValueError(f"No CSV column mappings defined for bank config: {bank_config.bank_code}")

        valid_records = []
        validation_errors = []
        
        row_num = 1
        for row in reader:
            row_num += 1
            if not row or all(cell.strip() == "" for cell in row):
                continue  # Skip empty rows
                
            # If row is truncated (fewer columns than header)
            if len(row) < len(headers):
                validation_errors.append({
                    "row_number": row_num,
                    "error_type": "FORMAT_ERROR",
                    "error_message": f"Truncated row: expected {len(headers)} columns, got {len(row)}",
                    "raw_line": ",".join(row)
                })
                continue
                
            try:
                # 1. Parse Transaction ID (handling Axis split references)
                if isinstance(mappings.txn_id, list):
                    # Check if all split parts exist in headers
                    txn_id_parts = []
                    for key in mappings.txn_id:
                        if key not in header_map:
                            raise ValueError(f"Mapping key '{key}' not found in CSV headers")
                        txn_id_parts.append(row[header_map[key]].strip())
                    txn_id = "".join(txn_id_parts)
                else:
                    if mappings.txn_id not in header_map:
                        raise ValueError(f"Mapping key '{mappings.txn_id}' not found in CSV headers")
                    txn_id = row[header_map[mappings.txn_id]].strip()
                
                # Check if txn_id is empty
                if not txn_id:
                    validation_errors.append({
                        "row_number": row_num,
                        "error_type": "FORMAT_ERROR",
                        "error_message": "Transaction ID (txn_id) is empty",
                        "raw_line": ",".join(row)
                    })
                    continue

                # 2. Parse Date
                if mappings.txn_date not in header_map:
                    raise ValueError(f"Mapping key '{mappings.txn_date}' not found in CSV headers")
                raw_date = row[header_map[mappings.txn_date]].strip()
                if not raw_date:
                    raise ValueError("Transaction date is empty")
                
                # 3. Parse Amount
                if mappings.amount not in header_map:
                    raise ValueError(f"Mapping key '{mappings.amount}' not found in CSV headers")
                raw_amount = row[header_map[mappings.amount]].strip()
                if not raw_amount:
                    raise ValueError("Amount is empty")
                    
                # Clean amount formatting (commas, currency symbols, spaces)
                clean_amount_str = raw_amount.replace(",", "").replace("$", "").replace("₹", "").strip()
                try:
                    amount = Decimal(clean_amount_str)
                except Exception:
                    raise ValueError(f"Invalid amount value: '{raw_amount}'")
                
                # 4. Parse Currency
                if mappings.currency not in header_map:
                    raise ValueError(f"Mapping key '{mappings.currency}' not found in CSV headers")
                currency = row[header_map[mappings.currency]].strip().upper()
                if not currency:
                    raise ValueError("Currency is empty")
                
                # 5. Parse Direction
                if mappings.direction not in header_map:
                    raise ValueError(f"Mapping key '{mappings.direction}' not found in CSV headers")
                direction = row[header_map[mappings.direction]].strip().upper()
                if not direction:
                    raise ValueError("Direction is empty")
                
                # Normalize direction
                if direction in ("DEBIT", "DR", "D", "OUT", "WD", "WITHDRAWAL"):
                    direction_norm = "DR"
                elif direction in ("CREDIT", "CR", "C", "IN", "DEP", "DEPOSIT"):
                    direction_norm = "CR"
                else:
                    raise ValueError(f"Unknown transaction direction: '{direction}'")
                
                # Optional fields
                counterparty_name = row[header_map[mappings.counterparty_name]].strip() if mappings.counterparty_name and mappings.counterparty_name in header_map else None
                counterparty_account = row[header_map[mappings.counterparty_account]].strip() if mappings.counterparty_account and mappings.counterparty_account in header_map else None
                bank_ref = row[header_map[mappings.bank_ref]].strip() if mappings.bank_ref and mappings.bank_ref in header_map else None
                narration = row[header_map[mappings.narration]].strip() if mappings.narration and mappings.narration in header_map else None
                
                raw_settlement_date = row[header_map[mappings.settlement_date]].strip() if mappings.settlement_date and mappings.settlement_date in header_map else None
                
                # Construct raw row dictionary
                raw_record = {
                    "txn_id": txn_id,
                    "raw_date": raw_date,
                    "amount": str(amount),
                    "currency": currency,
                    "direction": direction_norm,
                    "counterparty_name": counterparty_name,
                    "counterparty_account": counterparty_account,
                    "bank_ref": bank_ref,
                    "narration": narration,
                    "settlement_date": raw_settlement_date,
                    "metadata": {
                        "row_num": row_num,
                        "filename": filename,
                        "ingestion_timestamp": datetime.utcnow().isoformat()
                    }
                }
                
                valid_records.append(raw_record)
                
            except Exception as e:
                validation_errors.append({
                    "row_number": row_num,
                    "error_type": "FORMAT_ERROR",
                    "error_message": str(e),
                    "raw_line": ",".join(row)
                })

        return file_hash, valid_records, validation_errors
