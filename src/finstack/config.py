"""Configuration helpers for FinStack."""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("finstack")


class UserTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    API = "api"
    ENTERPRISE = "enterprise"


# Daily request caps by plan.
TIER_RATE_LIMITS = {
    UserTier.FREE: 100,
    UserTier.PRO: 5000,
    UserTier.API: 50000,
    UserTier.ENTERPRISE: 500000,
}


# Free users can call most tools, but these stay paid-only.
FREE_TIER_LOCKED_TOOLS = {
    "nse_options_chain",
    "backtest_strategy",
    "portfolio_analysis",
    "stock_screener",
    "support_resistance",
}


PRO_TIER_LOCKED_TOOLS = set()


ENTERPRISE_ONLY_TOOLS = {
    "custom_screener",
    "bulk_export",
    "webhook_alerts",
}


@dataclass
class FinStackConfig:
    """Central configuration for the server and hosted add-ons."""

    # Render requires the web server to listen on all interfaces.
    host: str = field(
        default_factory=lambda: os.getenv(
            "FINSTACK_HOST",
            "0.0.0.0",
        )
    )

    # Render supplies PORT at runtime.
    #
    # Priority:
    # 1. FINSTACK_PORT, if explicitly configured
    # 2. Render's PORT
    # 3. 10000 fallback
    port: int = field(
        default_factory=lambda: int(
            os.getenv(
                "FINSTACK_PORT",
                os.getenv(
                    "PORT",
                    "10000",
                ),
            )
        )
    )

    log_level: str = field(
        default_factory=lambda: os.getenv(
            "FINSTACK_LOG_LEVEL",
            "INFO",
        )
    )

    mode: UserTier = field(
        default_factory=lambda: UserTier(
            os.getenv(
                "FINSTACK_MODE",
                "free",
            )
        )
    )

    cache_ttl_quotes: int = field(
        default_factory=lambda: int(
            os.getenv(
                "FINSTACK_CACHE_TTL_QUOTES",
                "300",
            )
        )
    )

    cache_ttl_fundamentals: int = field(
        default_factory=lambda: int(
            os.getenv(
                "FINSTACK_CACHE_TTL_FUNDAMENTALS",
                "3600",
            )
        )
    )

    cache_ttl_historical: int = field(
        default_factory=lambda: int(
            os.getenv(
                "FINSTACK_CACHE_TTL_HISTORICAL",
                "86400",
            )
        )
    )

    # Optional integrations.
    alpha_vantage_key: str = field(
        default_factory=lambda: os.getenv(
            "ALPHA_VANTAGE_API_KEY",
            "",
        )
    )

    coingecko_key: str = field(
        default_factory=lambda: os.getenv(
            "COINGECKO_API_KEY",
            "",
        )
    )

    sec_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "SEC_EDGAR_USER_AGENT",
            "FinStack/0.3.2 arunodayya32@gmail.com",
        )
    )

    stripe_secret_key: str = field(
        default_factory=lambda: os.getenv(
            "STRIPE_SECRET_KEY",
            "",
        )
    )

    stripe_webhook_secret: str = field(
        default_factory=lambda: os.getenv(
            "STRIPE_WEBHOOK_SECRET",
            "",
        )
    )

    razorpay_key_id: str = field(
        default_factory=lambda: os.getenv(
            "RAZORPAY_KEY_ID",
            "",
        )
    )

    razorpay_key_secret: str = field(
        default_factory=lambda: os.getenv(
            "RAZORPAY_KEY_SECRET",
            "",
        )
    )

    def is_tool_allowed(
        self,
        tool_name: str,
        user_tier: UserTier | None = None,
    ) -> bool:
        """Return whether a given tool is available for the selected tier."""

        tier = user_tier or self.mode

        if tier == UserTier.ENTERPRISE:
            return True

        if tier == UserTier.FREE:
            return (
                tool_name not in FREE_TIER_LOCKED_TOOLS
                and tool_name not in ENTERPRISE_ONLY_TOOLS
            )

        if tier in (UserTier.PRO, UserTier.API):
            return tool_name not in ENTERPRISE_ONLY_TOOLS

        return True

    def get_rate_limit(
        self,
        user_tier: UserTier | None = None,
    ) -> int:
        """Return the daily request cap for the selected tier."""

        tier = user_tier or self.mode

        return TIER_RATE_LIMITS.get(
            tier,
            100,
        )

    def setup_logging(self) -> None:
        """Set up a basic structured log format."""

        # Convert the textual log level into the corresponding
        # logging module integer safely.
        level_name = self.log_level.upper()

        level = getattr(
            logging,
            level_name,
            logging.INFO,
        )

        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


config = FinStackConfig()
