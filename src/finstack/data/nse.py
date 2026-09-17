"""
FinStack NSE Data Fetcher

Fetches Indian stock market data from multiple free sources:

1. Direct NSE JSON endpoints (primary for live NSE data)
2. yfinance (fallback / historical / supplementary)
3. Fallback mechanisms for reliability

All methods return clean dicts ready for MCP tool responses.
"""

import logging
import math
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from finstack.utils.cache import cached, quotes_cache, historical_cache
from finstack.utils.helpers import (
    validate_symbol,
    to_nse_symbol,
    to_bse_symbol,
    validate_period,
    validate_interval,
    clean_nan,
    safe_get,
    format_market_cap,
    format_percentage,
)

logger = logging.getLogger("finstack.data.nse")


# ============================================================================
# CONSTANTS
# ============================================================================

NSE_BASE = "https://www.nseindia.com"
NSE_API_BASE = f"{NSE_BASE}/api"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{NSE_BASE}/",
    "Connection": "keep-alive",
}

IST = ZoneInfo("Asia/Kolkata")

NSE_REQUEST_TIMEOUT = 8

# Keep the cache short for live market data.
QUOTE_CACHE_TTL = 60

# Historical data can change during the current trading day, so the previous
# 24-hour cache was too long for a market-data service.
HISTORICAL_CACHE_TTL = 900

# NSE trading session.
PRE_OPEN_START_MINUTES = 9 * 60
REGULAR_OPEN_MINUTES = 9 * 60 + 15
REGULAR_CLOSE_MINUTES = 15 * 60 + 30
POST_CLOSE_END_MINUTES = 16 * 60


# Index symbol mappings.
INDEX_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "NIFTYIT": "^CNXIT",
    "NIFTY IT": "^CNXIT",
    "NIFTYPHARMA": "^CNXPHARMA",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTYFMCG": "^CNXFMCG",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTYAUTO": "^CNXAUTO",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTYMETAL": "^CNXMETAL",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTYREALTY": "^CNXREALTY",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTYENERGY": "^CNXENERGY",
    "NIFTY ENERGY": "^CNXENERGY",
}


# Official/current NSE all-indices names that correspond to our public names.
NSE_INDEX_NAME_MAP = {
    "NIFTY50": "NIFTY 50",
    "NIFTY": "NIFTY 50",
    "NIFTY 50": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "BANK NIFTY": "NIFTY BANK",
    "NIFTYIT": "NIFTY IT",
    "NIFTY IT": "NIFTY IT",
    "NIFTYPHARMA": "NIFTY PHARMA",
    "NIFTY PHARMA": "NIFTY PHARMA",
    "NIFTYFMCG": "NIFTY FMCG",
    "NIFTY FMCG": "NIFTY FMCG",
    "NIFTYAUTO": "NIFTY AUTO",
    "NIFTY AUTO": "NIFTY AUTO",
    "NIFTYMETAL": "NIFTY METAL",
    "NIFTY METAL": "NIFTY METAL",
    "NIFTYREALTY": "NIFTY REALTY",
    "NIFTY REALTY": "NIFTY REALTY",
    "NIFTYENERGY": "NIFTY ENERGY",
    "NIFTY ENERGY": "NIFTY ENERGY",
}


# ============================================================================
# SMALL INTERNAL HELPERS
# ============================================================================

def _now_ist() -> datetime:
    """Return an aware current timestamp in Indian Standard Time."""
    return datetime.now(IST)


def _iso_now() -> str:
    """Return current IST timestamp in ISO-8601 format."""
    return _now_ist().isoformat()


def _to_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Safely convert a value to a finite float."""
    if value is None:
        return default

    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(result):
        return default

    return result


def _to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """Safely convert a value to a finite integer."""
    number = _to_float(value)

    if number is None:
        return default

    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return default


def _round_float(
    value: Any,
    digits: int = 2,
    default: Optional[float] = None,
) -> Optional[float]:
    """Safely convert and round a numeric value."""
    number = _to_float(value, default=default)

    if number is None:
        return default

    return round(number, digits)


def _clean_symbol(value: str) -> str:
    """Normalize an NSE/BSE symbol for display."""
    return (
        value.upper()
        .strip()
        .replace(".NS", "")
        .replace(".BO", "")
    )


def _ensure_nse_yf_symbol(symbol: str) -> str:
    """Return a normalized Yahoo Finance NSE ticker."""
    normalized = symbol.upper().strip()

    if normalized.endswith(".NS"):
        return normalized

    if normalized.endswith(".BO"):
        normalized = normalized[:-3]

    try:
        resolved = to_nse_symbol(normalized)
        if resolved and str(resolved).upper().endswith(".NS"):
            return str(resolved).upper()
    except Exception:
        pass

    return f"{normalized}.NS"


def _ensure_bse_yf_symbol(symbol: str) -> str:
    """Return a normalized Yahoo Finance BSE ticker."""
    normalized = symbol.upper().strip()

    if normalized.endswith(".BO"):
        return normalized

    if normalized.endswith(".NS"):
        normalized = normalized[:-3]

    try:
        resolved = to_bse_symbol(normalized)
        if resolved and str(resolved).upper().endswith(".BO"):
            return str(resolved).upper()
    except Exception:
        pass

    return f"{normalized}.BO"


def _resolve_index(name: str) -> str:
    """Resolve an index name to its Yahoo Finance symbol."""
    normalized = name.upper().strip()
    return INDEX_MAP.get(normalized, name)


def _nse_api_request(
    endpoint: str,
    params: Optional[dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Make a best-effort request to an NSE JSON endpoint.

    NSE commonly requires an initial request to the website in order to
    establish cookies/session state before API calls succeed.
    """
    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    try:
        # Establish NSE session/cookies.
        homepage = session.get(
            NSE_BASE,
            timeout=NSE_REQUEST_TIMEOUT,
        )

        if homepage.status_code not in (200, 301, 302, 403):
            logger.debug(
                "NSE homepage returned HTTP %s",
                homepage.status_code,
            )

        url = (
            endpoint
            if endpoint.startswith("http")
            else f"{NSE_API_BASE}/{endpoint.lstrip('/')}"
        )

        response = session.get(
            url,
            params=params,
            timeout=NSE_REQUEST_TIMEOUT,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Referer": f"{NSE_BASE}/",
            },
        )

        response.raise_for_status()

        return response.json()

    except requests.RequestException as exc:
        logger.warning(
            "NSE API request failed for %s: %s",
            endpoint,
            exc,
        )
    except ValueError as exc:
        logger.warning(
            "NSE API returned invalid JSON for %s: %s",
            endpoint,
            exc,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected NSE API error for %s: %s",
            endpoint,
            exc,
        )
    finally:
        session.close()

    return None


def _extract_fast_info_value(
    fast_info: Any,
    *keys: str,
) -> Any:
    """
    Read a yfinance FastInfo field across dict-like and attribute-style
    implementations.
    """
    if fast_info is None:
        return None

    for key in keys:
        try:
            if hasattr(fast_info, "get"):
                value = fast_info.get(key)
                if value is not None:
                    return value
        except Exception:
            pass

        try:
            value = getattr(fast_info, key)
            if value is not None:
                return value
        except Exception:
            pass

    return None


def _get_optional_yahoo_info(ticker: yf.Ticker) -> dict:
    """
    Fetch Yahoo metadata without making it a hard dependency for quotes.

    Ticker.info can fail independently of price history/FastInfo, so metadata
    failures must never cause an otherwise valid quote to fail.
    """
    try:
        info = ticker.info

        if isinstance(info, dict):
            return info

    except Exception as exc:
        logger.debug(
            "Yahoo ticker.info unavailable for %s: %s",
            getattr(ticker, "ticker", "unknown"),
            exc,
        )

    return {}


def _fetch_yahoo_latest_history(
    ticker: yf.Ticker,
) -> Optional[Any]:
    """
    Fetch a small daily history window for quote fallback.

    A 5-day daily request is deliberately used instead of ticker.info because
    it is generally much more reliable for price data.
    """
    try:
        history = ticker.history(
            period="5d",
            interval="1d",
            auto_adjust=True,
            actions=False,
        )

        if history is not None and not history.empty:
            return history

    except Exception as exc:
        logger.debug(
            "Yahoo fallback history failed for %s: %s",
            getattr(ticker, "ticker", "unknown"),
            exc,
        )

    return None


def _extract_history_last_values(
    history: Any,
) -> dict[str, Optional[float]]:
    """Extract latest/previous daily values safely from a DataFrame."""
    if history is None or getattr(history, "empty", True):
        return {
            "price": None,
            "previous_close": None,
            "open": None,
            "high": None,
            "low": None,
            "volume": None,
        }

    try:
        close_series = history["Close"].dropna()
    except Exception:
        close_series = None

    if close_series is None or len(close_series) == 0:
        return {
            "price": None,
            "previous_close": None,
            "open": None,
            "high": None,
            "low": None,
            "volume": None,
        }

    last_idx = close_series.index[-1]
    last_row = history.loc[last_idx]

    previous_close = None

    if len(close_series) >= 2:
        previous_close = _to_float(close_series.iloc[-2])

    return {
        "price": _to_float(last_row.get("Close")),
        "previous_close": previous_close,
        "open": _to_float(last_row.get("Open")),
        "high": _to_float(last_row.get("High")),
        "low": _to_float(last_row.get("Low")),
        "volume": _to_int(last_row.get("Volume")),
    }


def _normalize_nse_quote_payload(
    payload: dict[str, Any],
    symbol: str,
) -> Optional[dict[str, Any]]:
    """
    Normalize the NSE quote-equity response into FinStack's public schema.
    """
    metadata = payload.get("info") or payload.get("metadata") or {}
    price_info = payload.get("priceInfo") or {}
    security_info = payload.get("securityInfo") or {}
    industry_info = payload.get("industryInfo") or {}
    security_wise = payload.get("securityWiseDP") or {}

    if not isinstance(metadata, dict):
        metadata = {}

    if not isinstance(price_info, dict):
        price_info = {}

    if not isinstance(security_info, dict):
        security_info = {}

    if not isinstance(industry_info, dict):
        industry_info = {}

    if not isinstance(security_wise, dict):
        security_wise = {}

    price = _to_float(price_info.get("lastPrice"))

    if price is None:
        return None

    previous_close = _to_float(price_info.get("previousClose"))

    change = _to_float(price_info.get("change"))

    change_pct = _to_float(price_info.get("pChange"))

    if change is None and previous_close is not None:
        change = price - previous_close

    if change_pct is None and previous_close not in (None, 0):
        change_pct = (price / previous_close - 1) * 100

    intra_day = price_info.get("intraDayHighLow") or {}
    week_high_low = price_info.get("weekHighLow") or {}

    day_low = (
        _to_float(intra_day.get("min"))
        if isinstance(intra_day, dict)
        else None
    )

    day_high = (
        _to_float(intra_day.get("max"))
        if isinstance(intra_day, dict)
        else None
    )

    week_low = (
        _to_float(week_high_low.get("min"))
        if isinstance(week_high_low, dict)
        else None
    )

    week_high = (
        _to_float(week_high_low.get("max"))
        if isinstance(week_high_low, dict)
        else None
    )

    quantity_traded = _to_int(
        security_wise.get("quantityTraded")
        or security_wise.get("totalTradedVolume")
    )

    issued_size = _to_float(
        security_info.get("issuedSize")
        or security_info.get("issuedSize")
    )

    market_cap = None

    if issued_size is not None and price is not None:
        market_cap = price * issued_size

    name = (
        metadata.get("companyName")
        or metadata.get("companyname")
        or metadata.get("symbol")
        or symbol
    )

    sector = (
        industry_info.get("macro")
        or industry_info.get("sector")
        or metadata.get("industry")
    )

    industry = (
        metadata.get("industry")
        or industry_info.get("industry")
        or None
    )

    pe_ratio = _to_float(metadata.get("pdSymbolPe"))

    result = {
        "symbol": _clean_symbol(symbol),
        "exchange": "NSE",
        "series": metadata.get("series") or "EQ",
        "isin": metadata.get("isin"),
        "name": name,
        "price": _round_float(price),
        "change": _round_float(change),
        "change_pct": _round_float(change_pct),
        "currency": "INR",
        "open": _round_float(price_info.get("open")),
        "high": _round_float(day_high),
        "low": _round_float(day_low),
        "prev_close": _round_float(previous_close),
        "volume": quantity_traded,
        "avg_volume": None,
        "market_cap": _round_float(market_cap),
        "market_cap_formatted": format_market_cap(market_cap),
        "fifty_two_week_high": _round_float(week_high),
        "fifty_two_week_low": _round_float(week_low),
        "pe_ratio": _round_float(pe_ratio),
        "forward_pe": None,
        "pb_ratio": None,
        "dividend_yield": None,
        "dividend_yield_pct": None,
        "sector": sector,
        "industry": industry,
        "data_source": "NSE India",
        "data_quality": {
            "price_source": "official NSE quote-equity endpoint",
            "metadata_source": "official NSE quote-equity endpoint",
        },
        "timestamp": _iso_now(),
    }

    return clean_nan(result)


def _fetch_direct_nse_quote(symbol: str) -> Optional[dict[str, Any]]:
    """Try the official NSE quote-equity API."""
    payload = _nse_api_request(
        "/quote-equity",
        params={"symbol": _clean_symbol(symbol)},
    )

    if not isinstance(payload, dict):
        return None

    return _normalize_nse_quote_payload(payload, symbol)


def _fetch_yahoo_quote(
    symbol: str,
    exchange: str = "NSE",
) -> Optional[dict[str, Any]]:
    """
    Fetch a quote from yfinance.

    FastInfo/history provide the price path. ticker.info is optional metadata
    only and can fail without invalidating the quote.
    """
    if exchange == "NSE":
        yf_symbol = _ensure_nse_yf_symbol(symbol)
    else:
        yf_symbol = _ensure_bse_yf_symbol(symbol)

    try:
        ticker = yf.Ticker(yf_symbol)

        fast_info = None

        try:
            fast_info = ticker.fast_info
        except Exception as exc:
            logger.debug(
                "FastInfo unavailable for %s: %s",
                yf_symbol,
                exc,
            )

        fallback_history = None

        fast_price = _to_float(
            _extract_fast_info_value(
                fast_info,
                "last_price",
                "lastPrice",
                "regularMarketPrice",
            )
        )

        fast_previous_close = _to_float(
            _extract_fast_info_value(
                fast_info,
                "previous_close",
                "previousClose",
                "regularMarketPreviousClose",
            )
        )

        fast_open = _to_float(
            _extract_fast_info_value(
                fast_info,
                "open",
                "regularMarketOpen",
            )
        )

        fast_high = _to_float(
            _extract_fast_info_value(
                fast_info,
                "day_high",
                "dayHigh",
                "regularMarketDayHigh",
            )
        )

        fast_low = _to_float(
            _extract_fast_info_value(
                fast_info,
                "day_low",
                "dayLow",
                "regularMarketDayLow",
            )
        )

        fast_volume = _to_int(
            _extract_fast_info_value(
                fast_info,
                "last_volume",
                "lastVolume",
                "regularMarketVolume",
            )
        )

        fast_market_cap = _to_float(
            _extract_fast_info_value(
                fast_info,
                "market_cap",
                "marketCap",
            )
        )

        if fast_price is None:
            fallback_history = _fetch_yahoo_latest_history(ticker)
            history_values = _extract_history_last_values(fallback_history)

            fast_price = history_values["price"]
            fast_previous_close = (
                fast_previous_close
                if fast_previous_close is not None
                else history_values["previous_close"]
            )
            fast_open = (
                fast_open
                if fast_open is not None
                else history_values["open"]
            )
            fast_high = (
                fast_high
                if fast_high is not None
                else history_values["high"]
            )
            fast_low = (
                fast_low
                if fast_low is not None
                else history_values["low"]
            )
            fast_volume = (
                fast_volume
                if fast_volume is not None
                else history_values["volume"]
            )

        if fast_price is None:
            return None

        info = _get_optional_yahoo_info(ticker)

        # Derive price change independently rather than trusting optional
        # ticker.info fields.
        change = None
        change_pct = None

        if fast_previous_close not in (None, 0):
            change = fast_price - fast_previous_close
            change_pct = (change / fast_previous_close) * 100

        market_cap = (
            fast_market_cap
            if fast_market_cap is not None
            else _to_float(info.get("marketCap"))
        )

        dividend_yield = _to_float(info.get("dividendYield"))

        result = {
            "symbol": _clean_symbol(symbol),
            "exchange": exchange,
            "series": "EQ" if exchange == "NSE" else None,
            "name": (
                info.get("longName")
                or info.get("shortName")
                or symbol
            ),
            "price": _round_float(fast_price),
            "change": _round_float(
                change if change is not None
                else info.get("regularMarketChange")
            ),
            "change_pct": _round_float(
                change_pct if change_pct is not None
                else info.get("regularMarketChangePercent")
            ),
            "currency": info.get("currency") or "INR",
            "open": _round_float(
                fast_open
                if fast_open is not None
                else info.get("regularMarketOpen")
            ),
            "high": _round_float(
                fast_high
                if fast_high is not None
                else info.get("regularMarketDayHigh")
            ),
            "low": _round_float(
                fast_low
                if fast_low is not None
                else info.get("regularMarketDayLow")
            ),
            "prev_close": _round_float(
                fast_previous_close
                if fast_previous_close is not None
                else info.get("regularMarketPreviousClose")
            ),
            "volume": (
                fast_volume
                if fast_volume is not None
                else _to_int(info.get("regularMarketVolume"))
            ),
            "avg_volume": _to_int(
                info.get("averageDailyVolume10Day")
                or info.get("averageVolume10days")
                or info.get("averageVolume")
            ),
            "market_cap": _round_float(market_cap),
            "market_cap_formatted": format_market_cap(market_cap),
            "fifty_two_week_high": _round_float(
                _extract_fast_info_value(
                    fast_info,
                    "year_high",
                    "yearHigh",
                )
                or info.get("fiftyTwoWeekHigh")
            ),
            "fifty_two_week_low": _round_float(
                _extract_fast_info_value(
                    fast_info,
                    "year_low",
                    "yearLow",
                )
                or info.get("fiftyTwoWeekLow")
            ),
            "pe_ratio": _round_float(info.get("trailingPE")),
            "forward_pe": _round_float(info.get("forwardPE")),
            "pb_ratio": _round_float(info.get("priceToBook")),
            "dividend_yield": dividend_yield,
            "dividend_yield_pct": (
                format_percentage(dividend_yield * 100)
                if dividend_yield is not None
                else None
            ),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "data_source": "Yahoo Finance via yfinance",
            "data_quality": {
                "price_source": (
                    "yfinance.fast_info"
                    if fast_price is not None
                    else "yfinance.history"
                ),
                "metadata_source": (
                    "yfinance.info"
                    if info
                    else None
                ),
                "delay_warning": (
                    "Yahoo Finance market data may be delayed; "
                    "use official NSE/broker feed for execution-grade latency."
                ),
            },
            "timestamp": _iso_now(),
        }

        return clean_nan(result)

    except Exception as exc:
        logger.warning(
            "Yahoo quote failed for %s: %s",
            symbol,
            exc,
        )
        return None


# ============================================================================
# QUOTE DATA
# ============================================================================

@cached(quotes_cache, ttl=QUOTE_CACHE_TTL)
def get_nse_quote(symbol: str) -> dict:
    """
    Get the latest available NSE quote for a stock.

    The official NSE quote endpoint is preferred. yfinance is used as a
    fallback when the NSE endpoint is unavailable or rate-limited.

    Returns:
        dict with keys including symbol, name, price, change, change_pct,
        open, high, low, prev_close, volume, market_cap and fundamentals.
    """
    symbol = validate_symbol(symbol)

    # Primary: official NSE.
    direct_quote = _fetch_direct_nse_quote(symbol)

    if direct_quote and not direct_quote.get("error"):
        return direct_quote

    # Fallback: yfinance.
    yahoo_quote = _fetch_yahoo_quote(symbol, exchange="NSE")

    if yahoo_quote:
        return yahoo_quote

    return {
        "error": True,
        "message": f"No NSE data found for '{symbol}'.",
        "suggestion": (
            "Use NSE symbols like RELIANCE, TCS, INFY, HDFCBANK "
            "and verify the security is currently listed."
        ),
        "data_source_attempts": [
            "NSE India quote-equity API",
            "Yahoo Finance via yfinance",
        ],
        "timestamp": _iso_now(),
    }


@cached(quotes_cache, ttl=QUOTE_CACHE_TTL)
def get_bse_quote(symbol: str) -> dict:
    """Get the latest available BSE quote for a stock using yfinance."""
    symbol = validate_symbol(symbol)

    yahoo_quote = _fetch_yahoo_quote(symbol, exchange="BSE")

    if yahoo_quote:
        return yahoo_quote

    return {
        "error": True,
        "message": f"No BSE data found for '{symbol}'.",
        "suggestion": (
            "BSE symbols can use the company symbol (RELIANCE) "
            "or a BSE security code where supported."
        ),
        "timestamp": _iso_now(),
    }


# ============================================================================
# INDEX DATA
# ============================================================================

def _fetch_direct_nse_index(index_name: str) -> Optional[dict[str, Any]]:
    """Fetch an index from NSE's allIndices endpoint."""
    requested = index_name.upper().strip()
    nse_name = NSE_INDEX_NAME_MAP.get(requested)

    if not nse_name:
        return None

    payload = _nse_api_request("/allIndices")

    if not isinstance(payload, list):
        return None

    for row in payload:
        if not isinstance(row, dict):
            continue

        returned_name = str(row.get("index", "")).strip().upper()

        if returned_name != nse_name.upper():
            continue

        value = _to_float(
            row.get("last")
            or row.get("lastPrice")
        )

        if value is None:
            continue

        change = _to_float(row.get("variation"))
        change_pct = _to_float(row.get("percentChange"))

        result = {
            "index": requested,
            "yf_symbol": _resolve_index(requested),
            "value": _round_float(value),
            "change": _round_float(change),
            "change_pct": _round_float(change_pct),
            "open": _round_float(row.get("open")),
            "high": _round_float(row.get("high")),
            "low": _round_float(row.get("low")),
            "prev_close": _round_float(row.get("previousClose")),
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
            "data_source": "NSE India allIndices",
            "timestamp": _iso_now(),
        }

        return clean_nan(result)

    return None


def _fetch_yahoo_index(index_name: str) -> Optional[dict[str, Any]]:
    """Fetch an index using yfinance as fallback."""
    yf_symbol = _resolve_index(index_name)

    try:
        ticker = yf.Ticker(yf_symbol)

        fast_info = None

        try:
            fast_info = ticker.fast_info
        except Exception:
            pass

        price = _to_float(
            _extract_fast_info_value(
                fast_info,
                "last_price",
                "lastPrice",
                "regularMarketPrice",
            )
        )

        previous_close = _to_float(
            _extract_fast_info_value(
                fast_info,
                "previous_close",
                "previousClose",
                "regularMarketPreviousClose",
            )
        )

        open_price = _to_float(
            _extract_fast_info_value(
                fast_info,
                "open",
                "regularMarketOpen",
            )
        )

        high_price = _to_float(
            _extract_fast_info_value(
                fast_info,
                "day_high",
                "dayHigh",
                "regularMarketDayHigh",
            )
        )

        low_price = _to_float(
            _extract_fast_info_value(
                fast_info,
                "day_low",
                "dayLow",
                "regularMarketDayLow",
            )
        )

        if price is None:
            history = _fetch_yahoo_latest_history(ticker)
            values = _extract_history_last_values(history)

            price = values["price"]
            previous_close = (
                previous_close
                if previous_close is not None
                else values["previous_close"]
            )
            open_price = (
                open_price
                if open_price is not None
                else values["open"]
            )
            high_price = (
                high_price
                if high_price is not None
                else values["high"]
            )
            low_price = (
                low_price
                if low_price is not None
                else values["low"]
            )

        if price is None:
            return None

        change = None
        change_pct = None

        if previous_close not in (None, 0):
            change = price - previous_close
            change_pct = (change / previous_close) * 100

        result = {
            "index": index_name.upper(),
            "yf_symbol": yf_symbol,
            "value": _round_float(price),
            "change": _round_float(change),
            "change_pct": _round_float(change_pct),
            "open": _round_float(open_price),
            "high": _round_float(high_price),
            "low": _round_float(low_price),
            "prev_close": _round_float(previous_close),
            "fifty_two_week_high": _round_float(
                _extract_fast_info_value(
                    fast_info,
                    "year_high",
                    "yearHigh",
                )
            ),
            "fifty_two_week_low": _round_float(
                _extract_fast_info_value(
                    fast_info,
                    "year_low",
                    "yearLow",
                )
            ),
            "data_source": "Yahoo Finance via yfinance",
            "data_quality": {
                "delay_warning": (
                    "Yahoo Finance market data may be delayed."
                ),
            },
            "timestamp": _iso_now(),
        }

        return clean_nan(result)

    except Exception as exc:
        logger.warning(
            "Yahoo index fetch failed for %s: %s",
            index_name,
            exc,
        )
        return None


@cached(quotes_cache, ttl=QUOTE_CACHE_TTL)
def get_index_data(index_name: str = "NIFTY50") -> dict:
    """
    Get current values for major Indian indices.

    Args:
        index_name:
            NIFTY50, SENSEX, BANKNIFTY, NIFTYIT, NIFTYPHARMA, etc.
            Or "ALL" for supported major indices.
    """
    normalized = index_name.upper().strip()

    if normalized == "ALL":
        indices = [
            "NIFTY50",
            "SENSEX",
            "BANKNIFTY",
            "NIFTYIT",
        ]

        results = {}

        for idx in indices:
            results[idx] = get_index_data(idx)

        return {
            "indices": results,
            "timestamp": _iso_now(),
        }

    # NSE is authoritative for NSE indices.
    direct_result = _fetch_direct_nse_index(normalized)

    if direct_result:
        return direct_result

    # yfinance fallback also covers SENSEX.
    yahoo_result = _fetch_yahoo_index(normalized)

    if yahoo_result:
        return yahoo_result

    return {
        "error": True,
        "message": f"No data found for index '{index_name}'.",
        "suggestion": (
            f"Valid indices include: "
            f"{', '.join(INDEX_MAP.keys())}"
        ),
        "timestamp": _iso_now(),
    }


# ============================================================================
# NSE HOLIDAY / TRADING-DAY HELPERS
# ============================================================================

def _fetch_nse_holiday_dates(year: int) -> set[str]:
    """
    Fetch official NSE trading holidays for a calendar year.

    Returns an empty set if NSE is temporarily unavailable.
    """
    payload = _nse_api_request(
        "/holiday-master",
        params={"type": "trading"},
    )

    holidays: set[str] = set()

    if not isinstance(payload, dict):
        return holidays

    # NSE may group holidays by market segment.
    candidate_groups = []

    for key, value in payload.items():
        if isinstance(value, list):
            candidate_groups.append(value)

    for group in candidate_groups:
        for row in group:
            if not isinstance(row, dict):
                continue

            raw_date = (
                row.get("tradingDate")
                or row.get("date")
                or row.get("Date")
            )

            if not raw_date:
                continue

            raw_date = str(raw_date).strip()

            parsed_date = None

            for fmt in (
                "%d-%b-%Y",
                "%d-%m-%Y",
                "%Y-%m-%d",
                "%d/%m/%Y",
            ):
                try:
                    parsed_date = datetime.strptime(
                        raw_date,
                        fmt,
                    ).date()
                    break
                except ValueError:
                    continue

            if parsed_date is not None and parsed_date.year == year:
                holidays.add(parsed_date.isoformat())

    return holidays


def _remove_known_non_trading_days(
    hist: Any,
    interval: str,
) -> tuple[Any, dict[str, Any]]:
    """
    Remove known NSE holidays from daily/weekly/monthly historical data.

    We do NOT blindly remove every zero-volume day because legitimate illiquid
    securities can have zero-volume sessions. Only dates identified by the
    official NSE holiday calendar are automatically removed.
    """
    quality = {
        "known_holiday_rows_removed": 0,
        "holiday_calendar_available": False,
        "suspect_zero_volume_rows": 0,
        "suspect_flat_zero_volume_rows": 0,
    }

    if hist is None or getattr(hist, "empty", True):
        return hist, quality

    daily_like = {
        "1d",
        "5d",
        "1wk",
        "1mo",
        "3mo",
    }

    if interval not in daily_like:
        return hist, quality

    try:
        years = {
            int(ts.year)
            for ts in hist.index
            if hasattr(ts, "year")
        }

        holiday_dates: set[str] = set()

        for year in years:
            year_holidays = _fetch_nse_holiday_dates(year)
            holiday_dates.update(year_holidays)

        if holiday_dates:
            quality["holiday_calendar_available"] = True

            keep_mask = []

            for ts in hist.index:
                date_value = ts.date().isoformat()

                keep_mask.append(date_value not in holiday_dates)

            before = len(hist)

            hist = hist.loc[keep_mask]

            quality["known_holiday_rows_removed"] = (
                before - len(hist)
            )

    except Exception as exc:
        logger.warning(
            "Could not apply NSE holiday filter: %s",
            exc,
        )

    # Flag suspicious rows without silently deleting them.
    try:
        previous_close = None

        for ts, row in hist.iterrows():
            volume = _to_float(row.get("Volume"))
            open_price = _to_float(row.get("Open"))
            high_price = _to_float(row.get("High"))
            low_price = _to_float(row.get("Low"))
            close_price = _to_float(row.get("Close"))

            if volume is not None and volume <= 0:
                quality["suspect_zero_volume_rows"] += 1

                if (
                    previous_close is not None
                    and open_price is not None
                    and high_price is not None
                    and low_price is not None
                    and close_price is not None
                    and open_price == high_price == low_price == close_price
                    and close_price == previous_close
                ):
                    quality["suspect_flat_zero_volume_rows"] += 1

            if close_price is not None:
                previous_close = close_price

    except Exception as exc:
        logger.debug(
            "Historical data quality scan failed: %s",
            exc,
        )

    return hist, quality


# ============================================================================
# HISTORICAL DATA
# ============================================================================

@cached(historical_cache, ttl=HISTORICAL_CACHE_TTL)
def get_historical_data(
    symbol: str,
    period: str = "1mo",
    interval: str = "1d",
) -> dict:
    """
    Get historical OHLCV data for an NSE stock.

    Args:
        symbol:
            NSE stock symbol, e.g. RELIANCE or TCS.
        period:
            1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max.
        interval:
            1m, 5m, 15m, 30m, 1h, 1d, 5d, 1wk, 1mo.

    Returns:
        Dict with OHLCV data and data-quality metadata.

    Notes:
        yfinance's current history behavior defaults to adjusted prices.
        This function makes that behavior explicit so the service is not
        dependent on an implicit library default.
    """
    symbol = validate_symbol(symbol)
    period = validate_period(period)
    interval = validate_interval(interval)

    yf_symbol = _ensure_nse_yf_symbol(symbol)

    try:
        ticker = yf.Ticker(yf_symbol)

        hist = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=True,
            actions=False,
        )

        if hist is None or hist.empty:
            return {
                "error": True,
                "message": (
                    f"No historical data for '{symbol}' "
                    f"with period={period}, interval={interval}"
                ),
                "suggestion": (
                    "Try a different period/interval or verify the NSE symbol."
                ),
                "timestamp": _iso_now(),
            }

        # Remove known NSE exchange holidays from daily-style series.
        hist, quality = _remove_known_non_trading_days(
            hist,
            interval,
        )

        if hist.empty:
            return {
                "error": True,
                "message": (
                    f"No valid trading-session data remains for '{symbol}' "
                    f"after NSE calendar validation."
                ),
                "timestamp": _iso_now(),
            }

        records = []

        daily_like = {
            "1d",
            "5d",
            "1wk",
            "1mo",
            "3mo",
        }

        for idx, row in hist.iterrows():
            open_price = _to_float(row.get("Open"))
            high_price = _to_float(row.get("High"))
            low_price = _to_float(row.get("Low"))
            close_price = _to_float(row.get("Close"))
            volume = _to_int(row.get("Volume"), default=0)

            # A price record without a close is not usable for analytics.
            if close_price is None:
                continue

            if idx.tzinfo is not None:
                display_idx = idx.astimezone(IST)
            else:
                display_idx = idx

            if interval in daily_like:
                date_value = display_idx.strftime("%Y-%m-%d")
            else:
                date_value = display_idx.strftime(
                    "%Y-%m-%d %H:%M"
                )

            flat_zero_volume = (
                volume == 0
                and open_price is not None
                and high_price is not None
                and low_price is not None
                and open_price == high_price == low_price == close_price
            )

            records.append(
                {
                    "date": date_value,
                    "open": (
                        round(open_price, 2)
                        if open_price is not None
                        else None
                    ),
                    "high": (
                        round(high_price, 2)
                        if high_price is not None
                        else None
                    ),
                    "low": (
                        round(low_price, 2)
                        if low_price is not None
                        else None
                    ),
                    "close": round(close_price, 2),
                    "volume": volume,
                    "data_quality_flags": (
                        ["zero_volume_flat_bar"]
                        if flat_zero_volume
                        else []
                    ),
                }
            )

        if not records:
            return {
                "error": True,
                "message": (
                    f"No valid OHLC records were produced for '{symbol}'."
                ),
                "timestamp": _iso_now(),
            }

        closes = [
            record["close"]
            for record in records
            if record["close"] is not None
        ]

        highs = [
            record["high"]
            for record in records
            if record["high"] is not None
        ]

        lows = [
            record["low"]
            for record in records
            if record["low"] is not None
        ]

        volumes = [
            record["volume"]
            for record in records
            if record["volume"] is not None
        ]

        latest_close = closes[-1] if closes else None
        first_close = closes[0] if closes else None

        period_return_pct = None

        if (
            first_close is not None
            and latest_close is not None
            and len(closes) > 1
            and first_close != 0
        ):
            period_return_pct = (
                (latest_close / first_close - 1) * 100
            )

        result = {
            "symbol": _clean_symbol(symbol),
            "exchange": "NSE",
            "yf_symbol": yf_symbol,
            "period": period,
            "interval": interval,
            "price_basis": "adjusted",
            "data_points": len(records),
            "start_date": records[0]["date"],
            "end_date": records[-1]["date"],
            "summary": {
                "latest_close": (
                    round(latest_close, 2)
                    if latest_close is not None
                    else None
                ),
                "period_high": (
                    round(max(highs), 2)
                    if highs
                    else None
                ),
                "period_low": (
                    round(min(lows), 2)
                    if lows
                    else None
                ),
                "period_return_pct": (
                    round(period_return_pct, 2)
                    if period_return_pct is not None
                    else None
                ),
                "avg_volume": (
                    int(sum(volumes) / len(volumes))
                    if volumes
                    else None
                ),
                "nonzero_volume_avg": (
                    int(
                        sum(v for v in volumes if v > 0)
                        / max(1, sum(1 for v in volumes if v > 0))
                    )
                    if any(v > 0 for v in volumes)
                    else None
                ),
            },
            "data_quality": {
                "source": "Yahoo Finance via yfinance",
                "calendar_source": (
                    "NSE India trading holiday calendar"
                    if quality["holiday_calendar_available"]
                    else None
                ),
                "known_holiday_rows_removed": quality[
                    "known_holiday_rows_removed"
                ],
                "suspect_zero_volume_rows": quality[
                    "suspect_zero_volume_rows"
                ],
                "suspect_flat_zero_volume_rows": quality[
                    "suspect_flat_zero_volume_rows"
                ],
                "warning": (
                    "A zero-volume row is not automatically deleted because "
                    "illiquid securities can legitimately have zero trading "
                    "volume. Known NSE holidays are removed."
                ),
            },
            "data": records,
            "timestamp": _iso_now(),
        }

        return clean_nan(result)

    except Exception as exc:
        logger.error(
            "Error fetching historical data for %s: %s",
            symbol,
            exc,
            exc_info=True,
        )

        return {
            "error": True,
            "message": (
                f"Failed to fetch history for '{symbol}': {str(exc)}"
            ),
            "timestamp": _iso_now(),
        }


# ============================================================================
# MARKET MOVERS
# ============================================================================

def _fetch_direct_nifty50_movers() -> Optional[list[dict[str, Any]]]:
    """
    Fetch the current Nifty 50 constituent snapshot directly from NSE.

    This avoids the original problem where only 30 hardcoded stocks were
    described as the full Nifty 50.
    """
    payload = _nse_api_request(
        "/equity-stockIndices",
        params={"index": "NIFTY 50"},
    )

    if not isinstance(payload, dict):
        return None

    rows = payload.get("data")

    if not isinstance(rows, list):
        return None

    results = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = row.get("symbol")

        if not symbol:
            continue

        symbol_text = str(symbol).strip().upper()

        # Exclude index summary rows accidentally included by the endpoint.
        if symbol_text in {
            "NIFTY 50",
            "NIFTY50",
        }:
            continue

        price = _to_float(row.get("lastPrice"))
        previous_close = _to_float(row.get("previousClose"))

        if price is None:
            continue

        change = _to_float(row.get("change"))
        change_pct = _to_float(row.get("pChange"))

        if change is None and previous_close is not None:
            change = price - previous_close

        if (
            change_pct is None
            and previous_close not in (None, 0)
        ):
            change_pct = (
                (price / previous_close - 1) * 100
            )

        volume = _to_int(
            row.get("totalTradedVolume")
            or row.get("totalTradedQuantity")
        )

        results.append(
            {
                "symbol": symbol_text,
                "price": _round_float(price),
                "change": _round_float(change),
                "change_pct": _round_float(change_pct),
                "volume": volume or 0,
            }
        )

    return results if results else None


def _fetch_yahoo_movers_fallback() -> list[dict[str, Any]]:
    """
    yfinance fallback for market movers.

    This is intentionally a fallback only. The primary implementation uses
    the live NSE Nifty 50 constituent endpoint.
    """
    fallback_symbols = [
        "RELIANCE.NS",
        "TCS.NS",
        "HDFCBANK.NS",
        "INFY.NS",
        "ICICIBANK.NS",
        "HINDUNILVR.NS",
        "SBIN.NS",
        "BHARTIARTL.NS",
        "ITC.NS",
        "KOTAKBANK.NS",
        "LT.NS",
        "AXISBANK.NS",
        "BAJFINANCE.NS",
        "ASIANPAINT.NS",
        "MARUTI.NS",
        "TITAN.NS",
        "SUNPHARMA.NS",
        "M&M.NS",
        "WIPRO.NS",
        "HCLTECH.NS",
        "ONGC.NS",
        "NTPC.NS",
        "POWERGRID.NS",
        "TATASTEEL.NS",
        "ADANIENT.NS",
        "ADANIPORTS.NS",
        "BAJAJFINSV.NS",
        "TECHM.NS",
        "NESTLEIND.NS",
        "ULTRACEMCO.NS",
    ]

    try:
        data = yf.download(
            fallback_symbols,
            period="5d",
            interval="1d",
            group_by="ticker",
            progress=False,
            threads=True,
            auto_adjust=True,
            actions=False,
        )

        if data is None or data.empty:
            return []

        results = []

        for sym in fallback_symbols:
            try:
                if not hasattr(data, "columns"):
                    continue

                if not hasattr(data.columns, "get_level_values"):
                    continue

                level_zero = data.columns.get_level_values(0)

                if sym not in level_zero:
                    continue

                ticker_data = data[sym].dropna(
                    how="all"
                )

                if ticker_data.empty or len(ticker_data) < 2:
                    continue

                close_series = ticker_data["Close"].dropna()

                if len(close_series) < 2:
                    continue

                previous_close = _to_float(
                    close_series.iloc[-2]
                )

                current_close = _to_float(
                    close_series.iloc[-1]
                )

                if (
                    previous_close is None
                    or current_close is None
                    or previous_close == 0
                ):
                    continue

                volume = _to_int(
                    ticker_data["Volume"].iloc[-1],
                    default=0,
                )

                change = (
                    current_close
                    - previous_close
                )

                change_pct = (
                    change
                    / previous_close
                    * 100
                )

                results.append(
                    {
                        "symbol": sym.replace(".NS", ""),
                        "price": round(current_close, 2),
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                        "volume": volume or 0,
                    }
                )

            except Exception:
                continue

        return results

    except Exception as exc:
        logger.warning(
            "yfinance market mover fallback failed: %s",
            exc,
        )
        return []


@cached(quotes_cache, ttl=QUOTE_CACHE_TTL)
def get_market_movers(
    mover_type: str = "gainers",
) -> dict:
    """
    Get top gainers, losers, or most active stocks in the Nifty 50.

    Args:
        mover_type:
            'gainers', 'losers', or 'active'.
    """
    mover_type = str(mover_type).strip().lower()

    if mover_type not in {
        "gainers",
        "losers",
        "active",
    }:
        return {
            "error": True,
            "message": (
                f"Invalid mover_type '{mover_type}'."
            ),
            "suggestion": (
                "Use 'gainers', 'losers', or 'active'."
            ),
        }

    try:
        results = _fetch_direct_nifty50_movers()
        source = "NSE India"

        if not results:
            results = _fetch_yahoo_movers_fallback()
            source = "Yahoo Finance via yfinance"

        if not results:
            return {
                "error": True,
                "message": (
                    "Could not fetch market mover data."
                ),
                "suggestion": (
                    "The exchange/provider may be temporarily "
                    "unavailable. Try again shortly."
                ),
                "timestamp": _iso_now(),
            }

        if mover_type == "gainers":
            filtered = [
                item
                for item in results
                if item["change_pct"] is not None
                and item["change_pct"] > 0
            ]

            filtered.sort(
                key=lambda item: item["change_pct"],
                reverse=True,
            )

        elif mover_type == "losers":
            filtered = [
                item
                for item in results
                if item["change_pct"] is not None
                and item["change_pct"] < 0
            ]

            filtered.sort(
                key=lambda item: item["change_pct"]
            )

        else:
            filtered = list(results)

            filtered.sort(
                key=lambda item: item.get("volume", 0),
                reverse=True,
            )

        filtered = filtered[:10]

        return clean_nan(
            {
                "type": mover_type,
                "count": len(filtered),
                "exchange": "NSE",
                "universe": "Nifty 50",
                "source": source,
                "stocks": filtered,
                "timestamp": _iso_now(),
            }
        )

    except Exception as exc:
        logger.error(
            "Error fetching market movers: %s",
            exc,
            exc_info=True,
        )

        return {
            "error": True,
            "message": (
                f"Failed to fetch {mover_type}: {str(exc)}"
            ),
            "timestamp": _iso_now(),
        }


# ============================================================================
# MARKET STATUS
# ============================================================================

def get_market_status() -> dict:
    """
    Check whether NSE/BSE equity markets are currently open.

    Uses IST rather than the Render server's timezone.

    Holiday detection is sourced from NSE's official trading-holiday
    endpoint when available. If that endpoint is unavailable, the service
    falls back to weekday/session-time logic and explicitly reports that
    the holiday calendar could not be verified.
    """
    now = _now_ist()

    weekday = now.weekday()
    current_minutes = (
        now.hour * 60
        + now.minute
    )

    current_date = now.date().isoformat()

    holiday_dates = _fetch_nse_holiday_dates(
        now.year
    )

    holiday_calendar_verified = bool(
        holiday_dates
    )

    is_holiday = (
        current_date in holiday_dates
        if holiday_calendar_verified
        else False
    )

    is_weekday = weekday < 5

    if is_holiday:
        status = "CLOSED"
        reason = "Exchange-declared trading holiday"

    elif not is_weekday:
        status = "CLOSED"
        reason = "Weekend"

    elif current_minutes < PRE_OPEN_START_MINUTES:
        status = "CLOSED"
        reason = "Before pre-open session"

    elif current_minutes < REGULAR_OPEN_MINUTES:
        status = "PRE_OPEN"
        reason = (
            "Pre-open session "
            "(9:00 AM - 9:15 AM IST)"
        )

    elif current_minutes < REGULAR_CLOSE_MINUTES:
        status = "OPEN"
        reason = "Regular trading session"

    elif current_minutes < POST_CLOSE_END_MINUTES:
        status = "POST_CLOSE"
        reason = (
            "Post-market session "
            "(after regular trading)"
        )

    else:
        status = "CLOSED"
        reason = "After market hours"

    return clean_nan(
        {
            "nse_status": status,
            "bse_status": status,
            "reason": reason,
            "trading_hours": {
                "pre_open": "9:00 AM - 9:15 AM IST",
                "regular": "9:15 AM - 3:30 PM IST",
                "post_close": "3:30 PM - 4:00 PM IST",
            },
            "current_time": now.isoformat(),
            "timezone": "Asia/Kolkata",
            "holiday": is_holiday,
            "holiday_calendar_verified": holiday_calendar_verified,
            "holiday_calendar_source": (
                "NSE India"
                if holiday_calendar_verified
                else None
            ),
            "note": (
                "Holiday-aware when the official NSE calendar "
                "endpoint is reachable. Otherwise the result "
                "falls back to weekday and session-time logic."
            ),
        }
    )
