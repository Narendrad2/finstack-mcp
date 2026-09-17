"""
FinStack Helpers

Common utilities: symbol validation, response formatting, error handling.
"""

import logging
import math
import re
from typing import Any

logger = logging.getLogger("finstack.helpers")


# ============================================================================
# SYMBOL VALIDATION
# ============================================================================

POPULAR_NSE_SYMBOLS = {
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA",
    "TATAMOTORS", "ULTRACEMCO", "WIPRO", "NESTLEIND", "HCLTECH",
    "ONGC", "NTPC", "POWERGRID", "M&M", "TATASTEEL", "JSWSTEEL",
    "ADANIENT", "ADANIPORTS", "BAJAJFINSV", "TECHM", "HDFCLIFE",
    "DIVISLAB", "DRREDDY", "CIPLA", "EICHERMOT", "GRASIM",
    "BRITANNIA", "APOLLOHOSP", "INDUSINDBK", "COALINDIA",
    "BPCL", "TATACONSUM", "SBILIFE", "HEROMOTOCO", "HINDALCO",
    "UPL", "SHREECEM",
}


def validate_symbol(symbol: str) -> str:
    """
    Validate and normalize a stock symbol.

    Handles:
        RELIANCE
        RELIANCE.NS
        RELIANCE.BO
        AAPL
        AAPL.US

    Returns:
        Cleaned uppercase symbol.

    Raises:
        ValueError: If the supplied symbol is invalid.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Symbol cannot be empty")

    symbol = symbol.strip().upper()

    # Remove common exchange suffixes for validation.
    base = re.sub(
        r"\.(NS|BO|US|L|TO|AX|HK|SS|SZ)$",
        "",
        symbol,
    )

    # Basic symbol format:
    # 1–20 characters, uppercase letters/digits plus & and -.
    if not re.fullmatch(r"[A-Z0-9&\-]{1,20}", base):
        raise ValueError(
            f"Invalid symbol format: '{symbol}'. "
            "Use formats like RELIANCE, TCS, AAPL, RELIANCE.NS"
        )

    return symbol


def to_nse_symbol(symbol: str) -> str:
    """
    Convert a symbol to Yahoo Finance NSE format.

    Examples:
        RELIANCE    -> RELIANCE.NS
        RELIANCE.NS -> RELIANCE.NS
        RELIANCE.BO -> RELIANCE.BO

    The function is deliberately NSE-specific. It should not be used for
    global Yahoo symbols unless the caller explicitly wants an NSE ticker.
    """
    symbol = symbol.strip().upper()

    if symbol.endswith(".NS"):
        return symbol

    # Preserve explicitly supplied BSE symbols.
    if symbol.endswith(".BO"):
        return symbol

    # Remove another exchange suffix if supplied, then target NSE.
    base = re.sub(r"\.\w+$", "", symbol)

    return f"{base}.NS"


def to_bse_symbol(symbol: str) -> str:
    """
    Convert a symbol to Yahoo Finance BSE format.

    Examples:
        RELIANCE    -> RELIANCE.BO
        RELIANCE.BO -> RELIANCE.BO
        RELIANCE.NS -> RELIANCE.BO
    """
    symbol = symbol.strip().upper()

    if symbol.endswith(".BO"):
        return symbol

    base = re.sub(r"\.\w+$", "", symbol)

    return f"{base}.BO"


# ============================================================================
# PERIOD / INTERVAL VALIDATION
# ============================================================================

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
    """Validate a yfinance-compatible period string."""
    if not isinstance(period, str) or not period.strip():
        raise ValueError(
            "Period cannot be empty"
        )

    period = period.strip().lower()

    if period not in VALID_PERIODS:
        raise ValueError(
            f"Invalid period '{period}'. "
            f"Valid: {', '.join(sorted(VALID_PERIODS))}"
        )

    return period


def validate_interval(interval: str) -> str:
    """Validate a yfinance-compatible interval string."""
    if not isinstance(interval, str) or not interval.strip():
        raise ValueError(
            "Interval cannot be empty"
        )

    interval = interval.strip().lower()

    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval '{interval}'. "
            f"Valid: {', '.join(sorted(VALID_INTERVALS))}"
        )

    return interval


# ============================================================================
# RESPONSE FORMATTING
# ============================================================================

def _finite_float(value: Any) -> float | None:
    """Return a finite float or None."""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(number):
        return None

    return number


def format_number(
    value: Any,
    decimals: int = 2,
) -> str | None:
    """
    Format an INR number using Indian units.

    Examples:
        1234       -> ₹1,234.00
        150000     -> ₹1.50 L
        25000000   -> ₹2.50 Cr
    """
    num = _finite_float(value)

    if num is None:
        return None

    if abs(num) >= 1_00_00_000:
        return f"₹{num / 1_00_00_000:,.{decimals}f} Cr"

    if abs(num) >= 1_00_000:
        return f"₹{num / 1_00_000:,.{decimals}f} L"

    if abs(num) >= 1000:
        return f"₹{num:,.{decimals}f}"

    return f"{num:.{decimals}f}"


def format_market_cap(
    value: Any,
    currency: str = "USD",
) -> str | None:
    """
    Format market capitalization using the correct currency.

    Backward compatibility:
        format_market_cap(value)
            continues to produce USD formatting.

    INR:
        Large market caps are expressed in lakh crore / crore, which is
        more natural for the Indian equity market.

    Examples:
        format_market_cap(16_833_043_041_059, "INR")
            -> ₹16.83 Lakh Cr

        format_market_cap(16_833_043_041_059, "USD")
            -> $16.83T
    """
    num = _finite_float(value)

    if num is None:
        return None

    currency = (
        str(currency).strip().upper()
        if currency is not None
        else "USD"
    )

    sign = "-" if num < 0 else ""
    magnitude = abs(num)

    # ------------------------------------------------------------------
    # Indian Rupees
    # ------------------------------------------------------------------
    if currency in {"INR", "₹", "RUPEE", "RUPEES"}:
        if magnitude >= 1e12:
            # 1 lakh crore = 1e12 INR.
            return f"₹{sign}{magnitude / 1e12:.2f} Lakh Cr"

        if magnitude >= 1e9:
            # 1 crore = 1e7 INR.
            return f"₹{sign}{magnitude / 1e7:,.2f} Cr"

        if magnitude >= 1e7:
            return f"₹{sign}{magnitude / 1e7:,.2f} Cr"

        if magnitude >= 1e5:
            return f"₹{sign}{magnitude / 1e5:,.2f} L"

        if magnitude >= 1e3:
            return f"₹{sign}{magnitude / 1e3:,.2f} K"

        return f"₹{sign}{magnitude:.2f}"

    # ------------------------------------------------------------------
    # US Dollars
    # ------------------------------------------------------------------
    if currency in {"USD", "$", "US DOLLAR", "US DOLLARS"}:
        if magnitude >= 1e12:
            return f"${sign}{magnitude / 1e12:.2f}T"

        if magnitude >= 1e9:
            return f"${sign}{magnitude / 1e9:.2f}B"

        if magnitude >= 1e6:
            return f"${sign}{magnitude / 1e6:.2f}M"

        if magnitude >= 1e3:
            return f"${sign}{magnitude / 1e3:.2f}K"

        return f"${sign}{magnitude:.2f}"

    # ------------------------------------------------------------------
    # Generic fallback
    # ------------------------------------------------------------------
    return f"{currency} {sign}{magnitude:,.2f}"


def format_percentage(
    value: Any,
    decimals: int = 2,
) -> str | None:
    """Format a percentage value safely."""
    num = _finite_float(value)

    if num is None:
        return None

    return f"{num:.{decimals}f}%"


# ============================================================================
# NaN / INF CLEANING
# ============================================================================

def clean_nan(data: dict | list | tuple) -> dict | list | tuple:
    """
    Recursively replace NaN and +/-inf with None.

    None is preserved.

    This function intentionally leaves normal integers, strings, booleans,
    and finite floats untouched.
    """
    if isinstance(data, dict):
        return {
            key: clean_nan(value)
            for key, value in data.items()
        }

    if isinstance(data, list):
        return [
            clean_nan(item)
            for item in data
        ]

    if isinstance(data, tuple):
        return tuple(
            clean_nan(item)
            for item in data
        )

    if isinstance(data, float):
        if not math.isfinite(data):
            return None

        return data

    return data


# ============================================================================
# SAFE OBJECT ACCESS
# ============================================================================

def safe_get(
    obj: Any,
    *keys: str,
    default: Any = None,
) -> Any:
    """
    Safely traverse nested dictionaries or objects.

    Examples:
        safe_get(info, "marketCap")
        safe_get(payload, "quoteSummary", "result", default=None)
    """
    current = obj

    for key in keys:
        if current is None:
            return default

        try:
            if isinstance(current, dict):
                if key not in current:
                    return default

                current = current[key]

            else:
                if not hasattr(current, key):
                    return default

                current = getattr(current, key)

        except (
            AttributeError,
            TypeError,
            KeyError,
        ):
            return default

    return current


# ============================================================================
# ERROR FORMATTING
# ============================================================================

def tool_error(
    message: str,
    suggestion: str = "",
) -> dict:
    """Create a standardized error response for MCP tools."""
    response = {
        "error": True,
        "message": message,
    }

    if suggestion:
        response["suggestion"] = suggestion

    return response


def tier_locked_error(tool_name: str) -> dict:
    """Error response when a free user tries a Pro-only tool."""
    return tool_error(
        f"'{tool_name}' requires a Pro subscription ($19/month).",
        suggestion=(
            "Upgrade at https://finstack.dev/pricing to unlock:\n"
            "• Options chain analysis\n"
            "• Portfolio analytics\n"
            "• Backtesting\n"
            "• Advanced stock screening\n"
            "• Support & resistance levels"
        ),
    )
