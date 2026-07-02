from lxml import etree
from typing import List, Dict, Any, Tuple
from decimal import Decimal
from datetime import datetime, date

class CAMT053Parser:
    @classmethod
    def parse(
        cls, 
        file_content: bytes, 
        filename: str, 
        bank_config: Any
    ) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parses an ISO 20022 CAMT.053 XML statement file using XPath.
        Returns:
            file_hash (str)
            valid_records (list of dicts, ready to be stored in raw_transactions)
            validation_errors (list of error records for exception handling)
        """
        import hashlib
        sha256 = hashlib.sha256()
        sha256.update(file_content)
        file_hash = sha256.hexdigest()

        valid_records = []
        validation_errors = []

        try:
            # Parse XML
            root = etree.fromstring(file_content)
        except Exception as e:
            return file_hash, [], [{"row": 0, "error_type": "FORMAT_ERROR", "error_message": f"XML parse error: {e}", "raw_line": ""}]

        # Detect namespace
        # ISO 20022 namespaces start with urn:iso:std:iso:20022:tech:xsd:camt.053.
        ns_url = root.nsmap.get(None)
        if not ns_url:
            # Fallback if no default namespace
            ns_url = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"
            
        ns = {"camt": ns_url}

        # Select all Statement elements
        statements = root.xpath("//camt:Stmt", namespaces=ns)
        
        row_num = 0
        for stmt in statements:
            # Extract statement currency and account number
            acct_number = ""
            acct_node = stmt.xpath("camt:Acct/camt:Id", namespaces=ns)
            if acct_node:
                iban = acct_node[0].xpath("camt:IBAN/text()", namespaces=ns)
                if iban:
                    acct_number = iban[0].strip()
                else:
                    othr_id = acct_node[0].xpath("camt:Othr/camt:Id/text()", namespaces=ns)
                    if othr_id:
                        acct_number = othr_id[0].strip()

            # Select statement balance date
            stmt_date_str = ""
            # Balance type code CLBD (Closing Booked) or CLAV (Closing Available)
            # Addresses Deliberate Error 1 (verifying balance type code)
            balance_type = "CLBD"
            if bank_config and bank_config.format_deviations and bank_config.format_deviations.camt053:
                balance_type = bank_config.format_deviations.camt053.balance_type
                
            bal_date_node = stmt.xpath(f"camt:Bal[camt:Tp/camt:CdOrPrtry/camt:Cd='{balance_type}']/camt:Dt/camt:Dt/text()", namespaces=ns)
            if bal_date_node:
                stmt_date_str = bal_date_node[0].strip()
            else:
                # Fallback to any balance date
                bal_date_node = stmt.xpath("camt:Bal/camt:Dt/camt:Dt/text()", namespaces=ns)
                if bal_date_node:
                    stmt_date_str = bal_date_node[0].strip()

            # Select all Entries (statement lines)
            entries = stmt.xpath("camt:Ntry", namespaces=ns)
            
            for entry in entries:
                row_num += 1
                try:
                    # Get Entry-level details
                    raw_entry_amount = entry.xpath("camt:Amt/text()", namespaces=ns)
                    entry_ccy = entry.xpath("camt:Amt/@Ccy", namespaces=ns)
                    cdt_dbt_ind = entry.xpath("camt:CdtDbtInd/text()", namespaces=ns)
                    
                    if not raw_entry_amount or not cdt_dbt_ind:
                        raise ValueError("Entry amount or CreditDebitIndicator is missing")
                        
                    amount_val = Decimal(raw_entry_amount[0].strip())
                    currency = entry_ccy[0].strip().upper() if entry_ccy else "INR"
                    direction = "CR" if cdt_dbt_ind[0].strip().upper() == "CRDT" else "DR"
                    
                    booking_dt = entry.xpath("camt:BookgDt/camt:Dt/text()", namespaces=ns)
                    booking_dt_tm = entry.xpath("camt:BookgDt/camt:DtTm/text()", namespaces=ns)
                    
                    raw_date = ""
                    if booking_dt_tm:
                        raw_date = booking_dt_tm[0].strip()
                    elif booking_dt:
                        raw_date = booking_dt[0].strip()
                        
                    val_dt = entry.xpath("camt:ValDt/camt:Dt/text()", namespaces=ns)
                    settlement_date_str = val_dt[0].strip() if val_dt else stmt_date_str
                    
                    # Entry narration
                    addtl_entry_inf = entry.xpath("camt:AddtlNtryInf/text()", namespaces=ns)
                    entry_narration = addtl_entry_inf[0].strip() if addtl_entry_inf else ""

                    # Recursive Extraction for nested <NtryDtls>/<TxDtls> (transaction details)
                    # A single Entry statement line may pack multiple transactions!
                    tx_details = entry.xpath("camt:NtryDtls/camt:TxDtls", namespaces=ns)
                    
                    if tx_details:
                        for tx in tx_details:
                            # Parse transaction-level override fields
                            tx_id_node = tx.xpath("camt:Refs/camt:EndToEndId/text()", namespaces=ns)
                            tx_instr_id = tx.xpath("camt:Refs/camt:InstrId/text()", namespaces=ns)
                            tx_id = ""
                            if tx_id_node and tx_id_node[0].strip().upper() != "NOTPROVIDED":
                                tx_id = tx_id_node[0].strip()
                            elif tx_instr_id and tx_instr_id[0].strip().upper() != "NOTPROVIDED":
                                tx_id = tx_instr_id[0].strip()
                            else:
                                tx_id = f"CAMT-{row_num}-{datetime.utcnow().timestamp()}"
                                
                            tx_amount_node = tx.xpath("camt:Amt/text()", namespaces=ns)
                            tx_amount = amount_val
                            if tx_amount_node:
                                tx_amount = Decimal(tx_amount_node[0].strip())
                                
                            # Counterparty details
                            # If CR (Credit), counterparty is Debtor (who paid us). If DR (Debit), counterparty is Creditor (who we paid).
                            cp_name = ""
                            cp_acct = ""
                            if direction == "CR":
                                cp_name_node = tx.xpath("camt:RltdPties/camt:Dbtr/camt:Nm/text()", namespaces=ns)
                                cp_acct_node = tx.xpath("camt:RltdPties/camt:DbtrAcct/camt:Id", namespaces=ns)
                            else:
                                cp_name_node = tx.xpath("camt:RltdPties/camt:Cdtr/camt:Nm/text()", namespaces=ns)
                                cp_acct_node = tx.xpath("camt:RltdPties/camt:CdtrAcct/camt:Id", namespaces=ns)
                                
                            if cp_name_node:
                                cp_name = cp_name_node[0].strip()
                            if cp_acct_node:
                                iban = cp_acct_node[0].xpath("camt:IBAN/text()", namespaces=ns)
                                if iban:
                                    cp_acct = iban[0].strip()
                                else:
                                    othr_id = cp_acct_node[0].xpath("camt:Othr/camt:Id/text()", namespaces=ns)
                                    if othr_id:
                                        cp_acct = othr_id[0].strip()

                            # Reference and narration override
                            tx_ref = tx.xpath("camt:Refs/camt:TxId/text()", namespaces=ns)
                            bank_ref = tx_ref[0].strip() if tx_ref else acct_number
                            
                            tx_inf = tx.xpath("camt:AddtlTxInf/text()", namespaces=ns)
                            narration = tx_inf[0].strip() if tx_inf else entry_narration
                            
                            record = {
                                "txn_id": tx_id,
                                "raw_date": raw_date,
                                "amount": str(tx_amount),
                                "currency": currency,
                                "direction": direction,
                                "counterparty_name": cp_name if cp_name else None,
                                "counterparty_account": cp_acct if cp_acct else None,
                                "bank_ref": bank_ref if bank_ref else None,
                                "narration": narration if narration else None,
                                "settlement_date": settlement_date_str if settlement_date_str else None,
                                "metadata": {
                                    "row_num": row_num,
                                    "filename": filename,
                                    "ingestion_timestamp": datetime.utcnow().isoformat(),
                                    "account_id": acct_number,
                                    "statement_level": False
                                }
                            }
                            valid_records.append(record)
                    else:
                        # Fallback: Create a single transaction directly from the Entry-level fields
                        tx_id_node = entry.xpath("camt:NtryRef/text()", namespaces=ns)
                        tx_id = tx_id_node[0].strip() if tx_id_node else f"CAMT-ENTRY-{row_num}"
                        
                        record = {
                            "txn_id": tx_id,
                            "raw_date": raw_date,
                            "amount": str(amount_val),
                            "currency": currency,
                            "direction": direction,
                            "counterparty_name": None,
                            "counterparty_account": None,
                            "bank_ref": acct_number,
                            "narration": entry_narration if entry_narration else None,
                            "settlement_date": settlement_date_str if settlement_date_str else None,
                            "metadata": {
                                "row_num": row_num,
                                "filename": filename,
                                "ingestion_timestamp": datetime.utcnow().isoformat(),
                                "account_id": acct_number,
                                "statement_level": True
                            }
                        }
                        valid_records.append(record)
                        
                except Exception as ex:
                    validation_errors.append({
                        "row_number": row_num,
                        "error_type": "FORMAT_ERROR",
                        "error_message": f"Entry level parse error: {ex}",
                        "raw_line": f"Ntry line index {row_num}"
                    })

        return file_hash, valid_records, validation_errors
