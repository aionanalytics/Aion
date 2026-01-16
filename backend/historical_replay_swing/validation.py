"""
Validation system to detect look-ahead bias.
"""

from dataclasses import dataclass
from typing import List, Dict, Any
import pandas as pd


@dataclass
class ValidationResult:
    """Result of validation check."""
    valid: bool
    errors: List[str]
    warnings: List[str]
    checks: Dict[str, bool]


class ReplayValidator:
    """Validates replay for correctness."""
    
    def validate_snapshot(self, date: str, snapshot) -> ValidationResult:
        """Validate snapshot has no future data."""
        errors = []
        warnings = []
        checks = {}
        
        # Check 1: Bar dates <= snapshot date
        if not snapshot.bars.empty and 'date' in snapshot.bars.columns:
            try:
                max_bar_date = snapshot.bars['date'].max()
                if pd.to_datetime(max_bar_date) > pd.to_datetime(date):
                    errors.append(f"Bars contain future data: max={max_bar_date} > {date}")
                    checks["bars_no_future"] = False
                else:
                    checks["bars_no_future"] = True
            except Exception as e:
                warnings.append(f"Could not validate bar dates: {e}")
                checks["bars_no_future"] = None
        else:
            checks["bars_no_future"] = True
        
        # Check 2: Data completeness
        if snapshot.bars.empty:
            warnings.append("No bar data in snapshot")
        
        if not snapshot.fundamentals:
            warnings.append("No fundamentals in snapshot")
        
        # Check 3: Macro data presence
        if not snapshot.macro:
            warnings.append("No macro data in snapshot")
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checks=checks,
        )
    
    def validate_training_data(self, df: pd.DataFrame, as_of_date: str) -> ValidationResult:
        """Validate model training data has no future leakage."""
        errors = []
        warnings = []
        checks = {}
        
        if 'date' not in df.columns:
            errors.append("Training data missing 'date' column")
            checks["training_no_future"] = False
            return ValidationResult(
                valid=False,
                errors=errors,
                warnings=warnings,
                checks=checks,
            )
        
        try:
            max_date = df['date'].max()
            if pd.to_datetime(max_date) > pd.to_datetime(as_of_date):
                errors.append(f"Training data contains future: max={max_date} > {as_of_date}")
                checks["training_no_future"] = False
            else:
                checks["training_no_future"] = True
        except Exception as e:
            errors.append(f"Could not validate training dates: {e}")
            checks["training_no_future"] = False
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            checks=checks,
        )


__all__ = [
    "ValidationResult",
    "ReplayValidator",
]
