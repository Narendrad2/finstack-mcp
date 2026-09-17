"""
FinStack Helpers

Common utilities: symbol validation, response formatting, error handling.
"""

import logging
import math
import re
from numbers import Real
from typing import Any

logger = logging.getLogger("finstack.helpers")


POPULAR_NSE_SYMBOLS = {
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "INFY",
    "ICICIBANK",
    "HINDUNILVR",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "KOTAKBANK",
    "LT",
    "AXISBANK",
    "BAJFINANCE",
    "ASIANPAINT",
    "MARUTI",
    "TITAN",
    "SUNPHARMA",
    "TATAMOTORS",
    "ULTRACEMCO",
    "WIPRO",
    "NESTLEIND",
    "HCLTECH",
    "ONGC",
    "NTPC",
    "POWERGRID",
    "M&M",
    "TATASTEEL",
    "JSWSTEEL",
    "ADANIENT",
    "ADANIPORTS",
    "BAJAJFINSV",
    "TECHM",
}


def validate_symbol(symbol: str) -> str:
    """
    Validate and normalize a ticker symbol.
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol cannot be empty")

    symbol = symbol.strip().upper()

    base = re.sub(
        r"\.(NS|BO|US|L|TO|AX|HK|SS|SZ)$",
        "",
        symbol,
        flags=re.IGNORECASE,
    )

    if not re.match(r"^[A-Z0-9&\-]{1,20}$", base):
        raise ValueError(
            f"Invalid symbol '{symbol}'. "
            "Symbol must contain only letters, numbers, '&' or '-'."
        )

    return symbol


def to_nse_symbol(symbol: str) -> str:
    """
    Convert an Indian symbol to Yahoo Finance NSE format.

    Examples:
        RELIANCE -> RELIANCE.NS
        RELIANCE.NS -> RELIANCE.NS
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol cannot be empty")

    symbol = symbol.strip().upper()

    if symbol.endswith(".NS"):
        return symbol

    if symbol.endswith(".BO"):
        return symbol

    base = re.sub(r"\.\w+$", "", symbol)

    return f"{base}.NS"


def to_bse_symbol(symbol: str) -> str:
    """
    Convert an Indian symbol to Yahoo Finance BSE format.

    Examples:
        RELIANCE -> RELIANCE.BO
        RELIANCE.BO -> RELIANCE.BO
    """
    if not symbol or not isinstance(symbol, str):
        raise ValueError("Symbol cannot be empty")

    symbol = symbol.strip().upper()

    if symbol.endswith(".BO"):
        return symbol

    base = re.sub(r"\.\w+$", "", symbol)

    return f"{base}.BO"


VALID_PERIODS = {
    "1d",
    "5d",
    "1mo",
    "3mo",
    "6mo",
    "1y",
    "2y",
    "5y",
    "10y",
    "ytd",
    "max",
}


VALID_INTERVALS = {
    "1m",
    "2m",
    "5m",
    "15m",
    "30m",
    "60m",
    "90m",
    "1h",
    "1d",
    "5d",
    "1wk",
    "1mo",
    "3mo",
}


def validate_period(period: str) -> str:
    """
    Validate a historical-data period.
    """
    if not period or not isinstance(period, str):
        raise ValueError("Period cannot be empty")

    period = period.strip().lower()

    if period not in VALID_PERIODS:
        raise ValueError(
            f"Invalid period '{period}'. "
            f"Valid periods: {', '.join(sorted(VALID_PERIODS))}"
        )

    return period


def validate_interval(interval: str) -> str:
    """
    Validate a historical-data interval.
    """
    if not interval or not isinstance(interval, str):
        raise ValueError("Interval cannot be empty")

    interval = interval.strip().lower()

    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval '{interval}'. "
            f"Valid intervals: {', '.join(sorted(VALID_INTERVALS))}"
        )

    return interval


def format_number(value: Any) -> str:
    """
    Format numbers using Indian-style lakh/crore grouping.

    Examples:
        1234 -> 1,234
        1234567 -> 12,34,567
        123456789 -> 12,34,56,789
    """
    if value is None:
        return "N/A"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)

    if not math.isfinite(numeric):
        return "N/A"

    if numeric.is_integer():
        number = int(numeric)
    else:
        return f"{numeric:,.2f}"

    sign = "-" if number < 0 else ""
    number = abs(number)

    if number < 1000:
        return f"{sign}{number:,}"

    number_str = str(number)

    last_three = number_str[-3:]
    remaining = number_str[:-3]

    if not remaining:
        return f"{sign}{last_three}"

    groups = []

    while len(remaining) > 2:
        groups.insert(0, remaining[-2:])
        remaining = remaining[:-2]

    if remaining:
        groups.insert(0, remaining)

    return f"{sign}{','.join(groups)},{last_three}"


def format_market_cap(value: Any, currency: str = "INR") -> str:
    """
    Format market capitalization using the supplied currency.

    The previous implementation always used '$', which was incorrect
    for Indian NSE/BSE values that are denominated in INR.
    """
    if value is None:
        return "N/A"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if not math.isfinite(numeric):
        return "N/A"

    currency = (currency or "INR").strip().upper()

    currency_symbols = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
        "CAD": "C$",
        "AUD": "A$",
        "SGD": "S$",
        "HKD": "HK$",
    }

    symbol = currency_symbols.get(currency, currency)

    absolute = abs(numeric)
    sign = "-" if numeric < 0 else ""

    if absolute >= 1_000_000_000_000:
        return f"{sign}{symbol}{absolute / 1_000_000_000_000:.2f}T"

    if absolute >= 1_000_000_000:
        return f"{sign}{symbol}{absolute / 1_000_000_000:.2f}B"

    if absolute >= 1_000_000:
        return f"{sign}{symbol}{absolute / 1_000_000:.2f}M"

    if absolute >= 1_000:
        return f"{sign}{symbol}{absolute / 1_000:.2f}K"

    return f"{sign}{symbol}{absolute:.2f}"


def format_percentage(value: Any, decimals: int = 2) -> str:
    """
    Format a numeric value as a percentage.
    """
    if value is None:
        return "N/A"

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if not math.isfinite(numeric):
        return "N/A"

    return f"{numeric:.{decimals}f}%"


def clean_nan(data: Any) -> Any:
    """
    Recursively replace NaN and infinite numeric values with None.

    Handles:
        - Python floats
        - NumPy scalar values
        - dictionaries
        - lists
        - tuples
    """
    if data is None:
        return None

    if isinstance(data, float):
        return data if math.isfinite(data) else None

    if isinstance(data, Real) and not isinstance(data, bool):
        try:
            numeric = float(data)
            return data if math.isfinite(numeric) else None
        except (TypeError, ValueError, OverflowError):
            return data

    if hasattr(data, "item") and not isinstance(data, (str, bytes)):
        try:
            converted = data.item()

            if converted is not data:
                return clean_nan(converted)

        except (TypeError, ValueError):
            pass

    if isinstance(data, dict):
        return {
            key: clean_nan(value)
            for key, value in data.items()
        }

    if isinstance(data, list):
        return [
            clean_nan(value)
            for value in data
        ]

    if isinstance(data, tuple):
        return tuple(
            clean_nan(value)
            for value in data
        )

    return data


def safe_get(
    data: Any,
    key: str,
    default: Any = None,
) -> Any:
    """
    Safely retrieve a key from a dictionary.
    """
    if data is None:
        return default

    if not isinstance(data, dict):
        return default

    value = data.get(key, default)

    return default if value is None else value


def tool_error(
    message: str,
    error_type: str = "tool_error",
    details: Any = None,
) -> dict[str, Any]:
    """
    Create a consistent tool-error response.
    """
    response: dict[str, Any] = {
        "error": True,
        "error_type": error_type,
        "message": str(message),
    }

    if details is not None:
        response["details"] = clean_nan(details)

    return response


def tier_locked_error(
    tool_name: str,
    required_tier: str = "pro",
) -> dict[str, Any]:
    """
    Create a standardized response when a tool requires a higher tier.
    """
    return {
        "error": True,
        "error_type": "tier_locked",
        "message": (
            f"Tool '{tool_name}' requires the '{required_tier}' tier."
        ),
        "tool": tool_name,
        "required_tier": required_tier,
    }
