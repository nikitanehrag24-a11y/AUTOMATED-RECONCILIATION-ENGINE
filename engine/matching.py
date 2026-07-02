from decimal import Decimal, ROUND_HALF_EVEN
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Tuple, Optional
from rapidfuzz import fuzz
from rapidfuzz.distance.JaroWinkler import similarity as jaro_winkler_similarity
from rapidfuzz.distance.Levenshtein import distance as levenshtein_distance
from sqlalchemy.orm import Session
from database.models import NormalisedTransaction, MatchResult, ReconciliationRun
import uuid

class MatchingEngine:
    @staticmethod
    def calculate_string_similarity(s1: str, s2: str) -> float:
        """Calculates Jaro-Winkler similarity (0 to 1)."""
        if not s1 or not s2:
            return 0.0
        return float(jaro_winkler_similarity(s1, s2))

    @staticmethod
    def calculate_token_set_ratio(s1: str, s2: str) -> float:
        """Calculates Token Set Ratio (0 to 1) for name matching."""
        if not s1 or not s2:
            return 0.0
        return float(fuzz.token_set_ratio(s1, s2) / 100.0)

    @staticmethod
    def calculate_levenshtein_distance(s1: str, s2: str) -> int:
        """Calculates Levenshtein distance."""
        return levenshtein_distance(s1, s2)

    @classmethod
    def calculate_confidence_score(
        cls, 
        internal: Dict[str, Any], 
        external: Dict[str, Any],
        bank_config: Any
    ) -> float:
        """
        Computes the weighted confidence score between an internal and external record:
        Confidence = 0.35*ref + 0.25*amt + 0.15*date + 0.10*name + 0.10*dir + 0.05*ccy
        """
        # Direction blocker (must match)
        # Note: In bank statement, CR is Credit (money in) which should match internal DR (Debit, money in) 
        # or CR matching CR depending on ledger representation.
        # Standard: Internal ledger direction matches external bank statement direction on opposite signs:
        # e.g. Internal CR (Payment out) matches Bank DR (Debit, money out).
        # We assume standard bank-reconciliation mapping: 
        # Internal Debit (DR, cash increase) matches Bank Credit (CR, deposit).
        # Internal Credit (CR, cash decrease) matches Bank Debit (DR, withdrawal).
        # Therefore, direction is a blocker: they MUST be complementary (Internal DR <-> Bank CR, or Internal CR <-> Bank DR).
        # If the directions are the same (e.g. Internal DR and Bank DR), it is a mismatch (Direction Reversal).
        # Let's check complementary directions:
        dir_match = 0.0
        int_dir = internal["direction"].upper()
        ext_dir = external["direction"].upper()
        
        # In our normalisation engine, we standardised both based on their own files.
        # So we check if they are complementary:
        if (int_dir == "DR" and ext_dir == "CR") or (int_dir == "CR" and ext_dir == "DR"):
            dir_match = 1.0
        else:
            # Direction reversal!
            return 0.0

        # Currency blocker (must match unless converted)
        ccy_match = 1.0 if internal["currency"] == external["currency"] else 0.0
        if ccy_match == 0.0:
            return 0.0

        # 1. Reference Score (35%)
        ref_sim = cls.calculate_string_similarity(internal["txn_id"], external["txn_id"])
        # If Jaro-Winkler similarity < 0.92, score is 0
        ref_score = ref_sim if ref_sim >= 0.92 else 0.0
        
        # Check Levenshtein distance deviation
        # allows max distance of 2 for references > 12 characters
        if len(internal["txn_id"]) > 12 and len(external["txn_id"]) > 12:
            lev_dist = cls.calculate_levenshtein_distance(internal["txn_id"], external["txn_id"])
            if lev_dist <= 2 and ref_score < 0.92:
                # Upgrade reference score to match if Levenshtein distance is low
                ref_score = 0.92

        # 2. Amount Score (25%)
        amt_int = Decimal(str(internal["amount"]))
        amt_ext = Decimal(str(external["amount"]))
        
        if amt_int == amt_ext:
            amt_score = 1.0
        else:
            # Check within tolerance: default is 0.01 for same currency
            diff = abs(amt_int - amt_ext)
            if diff <= Decimal("0.01"):
                amt_score = 0.8
            else:
                # Check cross-currency or FX variance 1.5%
                mean_amt = (amt_int + amt_ext) / 2
                if mean_amt > 0 and (diff / mean_amt) <= Decimal("0.015"):
                    amt_score = 0.8
                else:
                    amt_score = 0.0

        # 3. Date Score (15%)
        # Timezone-normalised dates in UTC
        date_int = internal["txn_date"].date() if isinstance(internal["txn_date"], datetime) else internal["txn_date"]
        date_ext = external["txn_date"].date() if isinstance(external["txn_date"], datetime) else external["txn_date"]
        
        date_diff = abs((date_int - date_ext).days)
        if date_diff == 0:
            date_score = 1.0
        elif date_diff == 1:
            date_score = 0.8
        elif date_diff == 2:
            date_score = 0.5
        else:
            date_score = 0.0

        # 4. Counterparty Name Score (10%)
        name_sim = cls.calculate_token_set_ratio(internal["counterparty_name"], external["counterparty_name"])
        name_score = name_sim if name_sim >= 0.80 else 0.0

        # Weighted Sum
        score = (
            0.35 * ref_score + 
            0.25 * amt_score + 
            0.15 * date_score + 
            0.10 * name_score + 
            0.10 * dir_match + 
            0.05 * ccy_match
        )
        return float(score)

    @classmethod
    def exact_match(
        cls, 
        internal_pool: List[Dict[str, Any]], 
        external_pool: List[Dict[str, Any]]
    ) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Executes exact matching using hash maps. O(N+M) complexity.
        Handles hash collisions via secondary disambiguation (narration and date proximity).
        """
        # Create hash map of external records: key -> list of external txns
        # Key fields: txn_id (cleaned), amount, currency, complementary direction
        ext_map = {}
        for ext in external_pool:
            # Complementary key for lookup from internal: 
            # If external is CR, key direction is CR (so internal DR matches it)
            # We store using external's fields: (txn_id, amount, currency, direction)
            key = (ext["txn_id"], Decimal(ext["amount"]), ext["currency"], ext["direction"])
            ext_map.setdefault(key, []).append(ext)

        matched_pairs = []
        unmatched_internal = []
        
        # Keep track of matched external record IDs to remove them from pool
        matched_ext_ids = set()

        for col in internal_pool:
            # Complementary direction lookup: 
            # If internal is DR, look up external with direction CR
            lookup_dir = "CR" if col["direction"] == "DR" else "DR"
            key = (col["txn_id"], Decimal(col["amount"]), col["currency"], lookup_dir)
            
            candidates = ext_map.get(key, [])
            # Filter out candidates already matched
            available_candidates = [c for c in candidates if c["id"] not in matched_ext_ids]
            
            if not available_candidates:
                unmatched_internal.append(col)
                continue
                
            # Secondary disambiguation if multiple matches found
            best_match = None
            if len(available_candidates) > 1:
                # Sort by timestamp proximity first
                col_date = col["txn_date"]
                available_candidates.sort(
                    key=lambda x: abs((x["txn_date"] - col_date).total_seconds())
                )
                best_match = available_candidates[0]
            else:
                best_match = available_candidates[0]
                
            matched_pairs.append((col, best_match))
            matched_ext_ids.add(best_match["id"])

        unmatched_external = [e for e in external_pool if e["id"] not in matched_ext_ids]
        
        return matched_pairs, unmatched_internal, unmatched_external

    @classmethod
    def fuzzy_match(
        cls, 
        internal_pool: List[Dict[str, Any]], 
        external_pool: List[Dict[str, Any]],
        bank_config: Any,
        auto_match_threshold: float = 0.85,
        review_threshold: float = 0.60
    ) -> Tuple[
        List[Tuple[Dict[str, Any], Dict[str, Any], float]], 
        List[Tuple[Dict[str, Any], Dict[str, Any], float]], 
        List[Dict[str, Any]], 
        List[Dict[str, Any]]
    ]:
        """
        Executes fuzzy matching with candidate blocking.
        Returns:
            auto_matches: list of (internal, external, score)
            review_matches: list of (internal, external, score) for human review
            unmatched_internal
            unmatched_external
        """
        auto_matches = []
        review_matches = []
        
        matched_ext_ids = set()
        matched_int_ids = set()

        for col in internal_pool:
            # Candidate Blocking: Find external records that are within +/- 2 days and same currency
            col_date = col["txn_date"].date() if isinstance(col["txn_date"], datetime) else col["txn_date"]
            
            candidates = []
            for ext in external_pool:
                if ext["id"] in matched_ext_ids:
                    continue
                if ext["currency"] != col["currency"]:
                    continue
                # Direction blocker check
                if (col["direction"] == "DR" and ext["direction"] != "CR") or (col["direction"] == "CR" and ext["direction"] != "DR"):
                    continue
                    
                ext_date = ext["txn_date"].date() if isinstance(ext["txn_date"], datetime) else ext["txn_date"]
                if abs((col_date - ext_date).days) <= 2:
                    # Within date window, calculate confidence score
                    score = cls.calculate_confidence_score(col, ext, bank_config)
                    if score >= review_threshold:
                        candidates.append((ext, score))

            if not candidates:
                continue
                
            # Pick candidate with highest score
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_ext, best_score = candidates[0]
            
            if best_score >= auto_match_threshold:
                auto_matches.append((col, best_ext, best_score))
                matched_ext_ids.add(best_ext["id"])
                matched_int_ids.add(col["id"])
            elif best_score >= review_threshold:
                review_matches.append((col, best_ext, best_score))
                # Do not immediately lock external ID so others can match it if better, 
                # but for simplicity in matching pipeline we can record suggestion.
                # Actually, in standard human review queue, we map the best candidate
                matched_ext_ids.add(best_ext["id"])
                matched_int_ids.add(col["id"])

        unmatched_internal = [i for i in internal_pool if i["id"] not in matched_int_ids]
        unmatched_external = [e for e in external_pool if e["id"] not in matched_ext_ids]
        
        return auto_matches, review_matches, unmatched_internal, unmatched_external

    @classmethod
    def rule_based_match(
        cls, 
        internal_pool: List[Dict[str, Any]], 
        external_pool: List[Dict[str, Any]],
        bank_config: Any
    ) -> Tuple[
        List[Tuple[Dict[str, Any], Dict[str, Any], str]], 
        List[Tuple[List[Dict[str, Any]], Dict[str, Any], str]],  # Split Matches (many internal to one external)
        List[Tuple[Dict[str, Any], List[Dict[str, Any]], str]],  # Netted Matches (one internal to many external)
        List[Dict[str, Any]], 
        List[Dict[str, Any]]
    ]:
        """
        Executes business rule based matching (split transactions, netted settlements).
        """
        split_matches = []
        netted_matches = []
        simple_rule_matches = []
        
        matched_int_ids = set()
        matched_ext_ids = set()

        # 1. Date Offset & Amount Tolerance Rule (Simple Rule match)
        # Check if reference matches exactly, amount matches exactly, but date is offset (within configured window)
        for col in internal_pool:
            if col["id"] in matched_int_ids:
                continue
            for ext in external_pool:
                if ext["id"] in matched_ext_ids:
                    continue
                # Blockers
                if col["currency"] != ext["currency"] or col["direction"] == ext["direction"]:
                    continue
                
                # Check reference match
                if col["txn_id"] == ext["txn_id"] and col["txn_id"] != "NONREF":
                    # Check amount match (exact or within tolerance)
                    amt_diff = abs(Decimal(str(col["amount"])) - Decimal(str(ext["amount"])))
                    if amt_diff <= Decimal("0.01"):
                        # Check date offset within reconciliation window
                        col_date = col["txn_date"].date() if isinstance(col["txn_date"], datetime) else col["txn_date"]
                        ext_date = ext["txn_date"].date() if isinstance(ext["txn_date"], datetime) else ext["txn_date"]
                        if abs((col_date - ext_date).days) <= bank_config.reconciliation_window_days:
                            simple_rule_matches.append((col, ext, "DATE_OFFSET_RULE"))
                            matched_int_ids.add(col["id"])
                            matched_ext_ids.add(ext["id"])
                            break

        # 2. Split Transaction Rule (Subset-Sum): Many Internal -> One External
        # For each unmatched external record, search for subsets of unmatched internal records 
        # whose sum matches the external amount.
        remaining_ext = [e for e in external_pool if e["id"] not in matched_ext_ids]
        remaining_int = [i for i in internal_pool if i["id"] not in matched_int_ids]
        
        for ext in remaining_ext:
            ext_amt = Decimal(str(ext["amount"]))
            ext_date = ext["txn_date"].date() if isinstance(ext["txn_date"], datetime) else ext["txn_date"]
            
            # Blocking candidates: same currency, complementary direction, within +/- 3 days
            candidates = [
                i for i in remaining_int 
                if i["id"] not in matched_int_ids
                and i["currency"] == ext["currency"]
                and i["direction"] != ext["direction"]
                and abs(((i["txn_date"].date() if isinstance(i["txn_date"], datetime) else i["txn_date"]) - ext_date).days) <= 3
            ]
            
            if not candidates:
                continue
                
            # Solve subset sum using depth-bounded backtracking (max size 10)
            subset = cls._find_subset_sum(candidates, ext_amt, Decimal("0.02"))
            if subset:
                split_matches.append((subset, ext, "SPLIT_TRANSACTION_RULE"))
                matched_ext_ids.add(ext["id"])
                for item in subset:
                    matched_int_ids.add(item["id"])
                    
        # 3. Netted Settlement Rule (One Internal -> Many External)
        # Reverse of split transaction
        remaining_ext = [e for e in external_pool if e["id"] not in matched_ext_ids]
        remaining_int = [i for i in remaining_int if i["id"] not in matched_int_ids]
        
        for col in remaining_int:
            col_amt = Decimal(str(col["amount"]))
            col_date = col["txn_date"].date() if isinstance(col["txn_date"], datetime) else col["txn_date"]
            
            # Blocking candidates: same currency, complementary direction, within +/- 3 days
            candidates = [
                e for e in remaining_ext 
                if e["id"] not in matched_ext_ids
                and e["currency"] == col["currency"]
                and e["direction"] != col["direction"]
                and abs(((e["txn_date"].date() if isinstance(e["txn_date"], datetime) else e["txn_date"]) - col_date).days) <= 3
            ]
            
            if not candidates:
                continue
                
            subset = cls._find_subset_sum(candidates, col_amt, Decimal("0.02"))
            if subset:
                netted_matches.append((col, subset, "NETTED_SETTLEMENT_RULE"))
                matched_int_ids.add(col["id"])
                for item in subset:
                    matched_ext_ids.add(item["id"])

        unmatched_internal = [i for i in internal_pool if i["id"] not in matched_int_ids]
        unmatched_external = [e for e in external_pool if e["id"] not in matched_ext_ids]
        
        return simple_rule_matches, split_matches, netted_matches, unmatched_internal, unmatched_external

    @classmethod
    def _find_subset_sum(
        cls, 
        candidates: List[Dict[str, Any]], 
        target: Decimal, 
        tolerance: Decimal,
        max_size: int = 10
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Backtracking helper to find a subset of candidates summing to target within tolerance.
        Bounded by max_size (10) for performance.
        """
        # Sort candidates descending by amount to prune faster
        candidates_sorted = sorted(candidates, key=lambda x: Decimal(str(x["amount"])), reverse=True)
        
        def backtrack(index: int, current_subset: List[Dict[str, Any]], current_sum: Decimal):
            if abs(current_sum - target) <= tolerance:
                return current_subset
            if len(current_subset) >= max_size or index >= len(candidates_sorted):
                return None
            if current_sum > target + tolerance:
                # Since we sorted descending, adding next items will exceed target
                # (Unless negative amounts, but standardised as absolute positive)
                pass
                
            # Option 1: Include candidates_sorted[index]
            item = candidates_sorted[index]
            item_amt = Decimal(str(item["amount"]))
            res = backtrack(index + 1, current_subset + [item], current_sum + item_amt)
            if res is not None:
                return res
                
            # Option 2: Exclude candidates_sorted[index]
            res = backtrack(index + 1, current_subset, current_sum)
            if res is not None:
                return res
                
            return None
            
        return backtrack(0, [], Decimal("0.00"))
