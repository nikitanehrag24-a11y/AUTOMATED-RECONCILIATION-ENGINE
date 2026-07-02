import os
import yaml
from typing import Dict, List, Union, Optional
from pydantic import BaseModel, Field, ValidationError

class CSVColumnMapping(BaseModel):
    txn_id: Union[str, List[str]]
    txn_date: str
    amount: str
    currency: str
    direction: str
    counterparty_name: Optional[str] = None
    counterparty_account: Optional[str] = None
    bank_ref: Optional[str] = None
    narration: Optional[str] = None
    settlement_date: Optional[str] = None

class MT940Deviations(BaseModel):
    decimal_separator: str = ","
    date_format: str = "YYMMDD"
    omit_funds_code: bool = True
    narration_line_range: str = "4-6"
    account_subfield_pos: str = "line 2"

class CAMT053Deviations(BaseModel):
    namespace: str = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"
    balance_type: str = "CLBD"

class FormatDeviations(BaseModel):
    mt940: Optional[MT940Deviations] = None
    camt053: Optional[CAMT053Deviations] = None

class BankConfig(BaseModel):
    bank_code: str
    bank_name: str
    supported_formats: List[str]
    timezone: str = "UTC"
    settlement_cycle: str = "T+1"
    reconciliation_window_days: int = 5
    column_mappings: Optional[CSVColumnMapping] = None
    format_deviations: Optional[FormatDeviations] = None

def load_bank_config(bank_code: str, config_dir: Optional[str] = None) -> BankConfig:
    """Loads and validates configuration for a specific bank."""
    if not config_dir:
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "bank_configurations")
    
    file_path = os.path.join(config_dir, f"{bank_code.lower()}_config.yaml")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Configuration file for bank '{bank_code}' not found at {file_path}")
        
    with open(file_path, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f)
        
    try:
        return BankConfig(**config_data)
    except ValidationError as e:
        raise ValueError(f"Invalid configuration for bank '{bank_code}': {e}")

def load_all_bank_configs(config_dir: Optional[str] = None) -> Dict[str, BankConfig]:
    """Loads all configuration files from the bank_configurations directory."""
    if not config_dir:
        config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "bank_configurations")
        
    configs = {}
    if not os.path.exists(config_dir):
        return configs
        
    for filename in os.listdir(config_dir):
        if filename.endswith("_config.yaml"):
            bank_code = filename.replace("_config.yaml", "").upper()
            try:
                configs[bank_code] = load_bank_config(bank_code, config_dir)
            except Exception as e:
                # Log or reraise
                print(f"Error loading {filename}: {e}")
                
    return configs
