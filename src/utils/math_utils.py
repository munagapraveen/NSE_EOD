import math
from typing import Any, Optional

def truncate_decimal(val: Any, decimals: int = 2) -> Optional[float]:
    """
    Truncate a numeric value to a specified number of decimal places.
    Avoids floating-point precision issues by rounding to 9 decimal places
    before applying truncation.
    Guards against NaN and Infinity by returning None.
    """
    if val is None:
        return None
    try:
        float_val = float(val)
        if math.isnan(float_val) or math.isinf(float_val):
            return None
        factor = 10 ** decimals
        # Round to 9 decimal places first to eliminate tiny float approximation noise (e.g. 12.350000000000002)
        val_rounded = round(float_val, 9)
        return int(val_rounded * factor) / factor
    except (TypeError, ValueError, OverflowError):
        return None


def safe_float(val: Any, default: float = 0.0) -> float:
    """
    Convert value to float safely.
    Returns 0.0 if value is NaN or Inf, or default if ValueError/TypeError occurs.
    """
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    """
    Convert value to int safely by converting to float first.
    Returns default if value is NaN/Inf or if ValueError/TypeError occurs.
    """
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return int(f)
    except (TypeError, ValueError):
        return default
