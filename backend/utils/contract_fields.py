"""Turn model output into fields the rest of the product can trust.

Expiry alerts and the dashboard compare ``expiryDate`` as a ``YYYY-MM-DD``
string. A prose date, a number, or a nested object used to be stored verbatim,
which either crashed the upload or silently dropped the contract out of
"expiring soon". Anything that is not a real calendar date becomes ``None``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

_RISK_LEVELS = {"high", "medium", "low"}


def coerce_iso_date(value: Any) -> Optional[str]:
    """Return ``YYYY-MM-DD`` or ``None``. Rejects partial and impossible dates."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def coerce_contract_value(value: Any) -> Optional[str]:
    """A short display string. Numbers are accepted; objects are not."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = " ".join(value.split())
    else:
        return None
    text = text.strip()
    if not text:
        return None
    return text[:120]


def coerce_risk_level(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    level = value.strip().lower()
    return level if level in _RISK_LEVELS else None


def fields_from_analysis(analysis: Any) -> dict:
    """Pull the contract columns we are willing to persist from an analysis."""
    if not isinstance(analysis, dict):
        return {
            "riskLevel": None,
            "effectiveDate": None,
            "expiryDate": None,
            "value": None,
        }
    dates = analysis.get("dates")
    dates = dates if isinstance(dates, dict) else {}
    return {
        "riskLevel": coerce_risk_level(analysis.get("riskLevel")),
        "effectiveDate": coerce_iso_date(dates.get("effectiveDate")),
        "expiryDate": coerce_iso_date(dates.get("expiryDate")),
        "value": coerce_contract_value(analysis.get("value")),
    }
