from decimal import Decimal
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session
from database.models import NormalisedTransaction, ReconciliationRun, ExceptionRecord
import math

class AnomalyDetector:
    @staticmethod
    def detect_amount_deviation(
        db: Session, 
        bank_code: str, 
        counterparty_name: str, 
        direction: str, 
        amount: Decimal
    ) -> Tuple[bool, str]:
        """
        Flags transaction if amount is > 3 standard deviations 
        from the historical mean for this counterparty and direction.
        """
        if not counterparty_name or counterparty_name == "UNKNOWN":
            return False, ""

        # Fetch historical transactions for the same counterparty, bank, and direction
        history = db.query(NormalisedTransaction.amount).filter(
            NormalisedTransaction.bank_code == bank_code,
            NormalisedTransaction.counterparty_name == counterparty_name,
            NormalisedTransaction.direction == direction
        ).all()
        
        if len(history) < 10:
            # Not enough historical data to compute standard deviation reliably
            return False, ""
            
        amounts = [float(h.amount) for h in history]
        mean = sum(amounts) / len(amounts)
        
        # Calculate standard deviation
        variance = sum((x - mean) ** 2 for x in amounts) / len(amounts)
        std_dev = math.sqrt(variance)
        
        if std_dev == 0:
            return False, ""
            
        amount_float = float(amount)
        z_score = abs(amount_float - mean) / std_dev
        
        if z_score > 3.0:
            return True, f"Amount deviation detected: Z-Score of {z_score:.2f} exceeds threshold 3.0 (Mean: {mean:.2f}, Std Dev: {std_dev:.2f})"
            
        return False, ""

    @staticmethod
    def detect_velocity_alert(
        db: Session, 
        counterparty_name: str, 
        txn_date: datetime
    ) -> Tuple[bool, str]:
        """
        Flags when transaction count from a counterparty in the past 1 hour 
        exceeds 200% of their daily average.
        """
        if not counterparty_name or counterparty_name == "UNKNOWN":
            return False, ""

        # Get count in past 1 hour
        one_hour_ago = txn_date - timedelta(hours=1)
        hourly_count = db.query(func.count(NormalisedTransaction.id)).filter(
            NormalisedTransaction.counterparty_name == counterparty_name,
            NormalisedTransaction.txn_date >= one_hour_ago,
            NormalisedTransaction.txn_date <= txn_date
        ).scalar() or 0

        # Get daily average count (rolling last 7 days)
        seven_days_ago = txn_date - timedelta(days=7)
        total_count = db.query(func.count(NormalisedTransaction.id)).filter(
            NormalisedTransaction.counterparty_name == counterparty_name,
            NormalisedTransaction.txn_date >= seven_days_ago,
            NormalisedTransaction.txn_date <= txn_date
        ).scalar() or 0
        
        daily_average = total_count / 7.0
        
        if daily_average < 5:
            # Skip check for low-volume counterparties to avoid false positives
            return False, ""
            
        # Velocity ratio
        if daily_average > 0:
            # We scale the daily average count to a 1-hour window (divide by 24)
            expected_hourly_avg = daily_average / 24.0
            if expected_hourly_avg > 0:
                ratio = hourly_count / expected_hourly_avg
                if ratio > 2.0:
                    return True, f"Velocity alert: Transaction volume ({hourly_count} in past hour) is {ratio*100:.1f}% of rolling average ({expected_hourly_avg:.2f}/hr)"
                    
        return False, ""

    @staticmethod
    def check_absence_alert(db: Session, bank_code: str, expected_hour_utc: int) -> Tuple[bool, str]:
        """
        Flags when an expected daily settlement file has not arrived 
        within 2 hours past its scheduled time.
        """
        now = datetime.utcnow()
        expected_time = datetime.combine(now.date(), datetime.min.time().replace(hour=expected_hour_utc))
        
        # If expected time has not happened yet today, check yesterday's
        if now < expected_time:
            expected_time = expected_time - timedelta(days=1)
            
        # If we are > 2 hours past expected arrival time
        if now - expected_time > timedelta(hours=2):
            # Check if a file was ingested today for this bank
            ingested_today = db.query(ReconciliationRun).filter(
                ReconciliationRun.bank_code == bank_code,
                ReconciliationRun.run_date == expected_time.date()
            ).first()
            
            if not ingested_today:
                return True, f"Absence alert: Expected statement run for bank '{bank_code}' at {expected_time.strftime('%H:%M')} UTC has not arrived."
                
        return False, ""
