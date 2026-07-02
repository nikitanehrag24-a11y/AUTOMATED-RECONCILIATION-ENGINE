import hashlib
import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import uuid
from sqlalchemy.orm import Session
from database.models import AuditLog

class AuditLogger:
    @staticmethod
    def calculate_hash(
        prev_hash: str,
        timestamp_str: str,
        actor: str,
        action_type: str,
        affected_records: str,
        before_state: str,
        after_state: str,
        rationale: str
    ) -> str:
        """Computes the SHA-256 hash of an audit entry incorporating the previous entry's hash."""
        block_data = f"{prev_hash}|{timestamp_str}|{actor}|{action_type}|{affected_records}|{before_state}|{after_state}|{rationale}"
        return hashlib.sha256(block_data.encode('utf-8')).hexdigest()

    @classmethod
    def log_event(
        cls,
        db: Session,
        actor: str,
        action_type: str,
        affected_records_list: List[str],
        before_state_dict: Optional[Dict[str, Any]],
        after_state_dict: Optional[Dict[str, Any]],
        rationale: str
    ) -> AuditLog:
        """
        Appends a new event to the tamper-evident audit log in the database.
        Chains the hash to the previous audit log entry.
        """
        # Fetch the latest audit log entry by timestamp/id to get its hash
        prev_log = db.query(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).first()
        prev_hash = prev_log.sha256_hash if prev_log else "0" * 64
        
        timestamp = datetime.utcnow()
        timestamp_str = timestamp.isoformat()
        
        # Serialize payloads
        affected_records_str = json.dumps(affected_records_list, sort_keys=True)
        before_state_str = json.dumps(before_state_dict, sort_keys=True) if before_state_dict else "{}"
        after_state_str = json.dumps(after_state_dict, sort_keys=True) if after_state_dict else "{}"
        
        # Compute hash
        sha256_hash = cls.calculate_hash(
            prev_hash,
            timestamp_str,
            actor,
            action_type,
            affected_records_str,
            before_state_str,
            after_state_str,
            rationale
        )
        
        # Save to DB
        new_log = AuditLog(
            id=str(uuid.uuid4()),
            timestamp=timestamp,
            actor=actor,
            action_type=action_type,
            affected_records=affected_records_list,
            before_state=before_state_dict or {},
            after_state=after_state_dict or {},
            rationale=rationale,
            sha256_hash=sha256_hash
        )
        db.add(new_log)
        db.commit()
        db.refresh(new_log)
        return new_log

    @classmethod
    def verify_log_integrity(cls, db: Session) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Traverses the audit log database and re-computes all hashes in sequence.
        Returns:
            is_valid (bool): True if the chain is unbroken, False if tampered.
            violations (list of dicts): Details of broken records.
        """
        # Fetch all audit log records ordered by timestamp
        logs = db.query(AuditLog).order_by(AuditLog.timestamp.asc(), AuditLog.id.asc()).all()
        
        is_valid = True
        violations = []
        
        prev_hash = "0" * 64
        
        for idx, log in enumerate(logs):
            # Recompute hash
            timestamp_str = log.timestamp.isoformat()
            affected_records_str = json.dumps(log.affected_records, sort_keys=True)
            before_state_str = json.dumps(log.before_state, sort_keys=True)
            after_state_str = json.dumps(log.after_state, sort_keys=True)
            
            calculated_hash = cls.calculate_hash(
                prev_hash,
                timestamp_str,
                log.actor,
                log.action_type,
                affected_records_str,
                before_state_str,
                after_state_str,
                log.rationale
            )
            
            if calculated_hash != log.sha256_hash:
                is_valid = False
                violations.append({
                    "log_id": log.id,
                    "index": idx,
                    "stored_hash": log.sha256_hash,
                    "calculated_hash": calculated_hash,
                    "reason": "Hash mismatch (record content or chain order has been tampered with)"
                })
            
            # Chain link
            prev_hash = log.sha256_hash
            
        return is_valid, violations
