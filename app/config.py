"""
config.py — Environment variable loading and validation.

All configuration is sourced from environment variables.
Use a .env file locally (loaded via python-dotenv) or set
variables directly in your Railway project settings.
"""

from __future__ import annotations

import os
import sys
import logging
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Holds all runtime configuration loaded from environment variables."""

    # --- Required ---
    tmdb_api_key: str
    telegram_bot_token: str
    telegram_channel_id: str
    mongodb_uri: str

    # --- MongoDB ---
    mongodb_database: str = "ott_update_bot"
    mongodb_collection: str = "posted_items"

    # --- TMDB ---
    tmdb_region: str = "IN"

    # --- OTT providers (canonical names) ---
    ott_providers: List[str] = field(default_factory=lambda: [
        "Netflix",
        "Amazon Prime Video",
        "JioHotstar",
        "ZEE5",
        "Sony Pictures Networks India",
    ])

    # --- Date window ---
    lookback_days: int = 1
    lookahead_days: int = 1

    # How many days back to search TMDB for candidate titles.
    # This is WIDER than lookback_days — it covers titles released in the
    # past N days that are currently on the configured platforms.
    # The narrow confidence window (lookback_days/lookahead_days) then
    # decides which of those candidates are actually "new" enough to post.
    discover_lookback_days: int = 90

    # --- Behaviour ---
    post_empty_update: bool = False

    # --- Release confidence threshold ---
    # Minimum DateConfidence level required to include a title in the post.
    # Valid values: CONFIRMED_DATE, APPROXIMATE_DATE, UNKNOWN_DATE
    # Default APPROXIMATE_DATE: post confirmed + approximate, skip unknown.
    min_release_confidence: str = "APPROXIMATE_DATE"

    @classmethod
    def load(cls) -> "Config":
        """
        Load configuration from environment variables.

        Raises SystemExit if any required variable is missing or invalid.
        """
        load_dotenv()  # No-op if .env is absent (production)

        missing: list[str] = []

        def require(key: str) -> str:
            val = os.environ.get(key, "").strip()
            if not val:
                missing.append(key)
            return val

        def optional(key: str, default: str = "") -> str:
            return os.environ.get(key, default).strip()

        tmdb_api_key = require("TMDB_API_KEY")
        telegram_bot_token = require("TELEGRAM_BOT_TOKEN")
        telegram_channel_id = require("TELEGRAM_CHANNEL_ID")
        mongodb_uri = require("MONGODB_URI")

        if missing:
            logger.critical(
                "Missing required environment variables: %s", ", ".join(missing)
            )
            sys.exit(1)

        # Optional with defaults
        mongodb_database = optional("MONGODB_DATABASE", "ott_update_bot")
        mongodb_collection = optional("MONGODB_COLLECTION", "posted_items")
        tmdb_region = optional("TMDB_REGION", "IN").upper()

        # OTT providers list
        raw_providers = optional(
            "OTT_PROVIDERS",
            "Netflix,Amazon Prime Video,JioHotstar,ZEE5,Sony Pictures Networks India",
        )
        ott_providers = [p.strip() for p in raw_providers.split(",") if p.strip()]

        # Date windows
        try:
            lookback_days = int(optional("LOOKBACK_DAYS", "1"))
            lookahead_days = int(optional("LOOKAHEAD_DAYS", "1"))
            discover_lookback_days = int(optional("DISCOVER_LOOKBACK_DAYS", "90"))
        except ValueError:
            logger.warning(
                "Invalid LOOKBACK_DAYS, LOOKAHEAD_DAYS, or DISCOVER_LOOKBACK_DAYS; "
                "defaulting to 1/1/90."
            )
            lookback_days = 1
            lookahead_days = 1
            discover_lookback_days = 90

        # Empty-update flag
        post_empty_update = optional("POST_EMPTY_UPDATE", "false").lower() == "true"

        # Release confidence threshold — validate against known values
        _valid_confidence = {"CONFIRMED_DATE", "APPROXIMATE_DATE", "UNKNOWN_DATE"}
        min_release_confidence = optional(
            "MIN_RELEASE_CONFIDENCE", "APPROXIMATE_DATE"
        ).upper()
        if min_release_confidence not in _valid_confidence:
            logger.warning(
                "Invalid MIN_RELEASE_CONFIDENCE=%r; defaulting to APPROXIMATE_DATE. "
                "Valid values: %s",
                min_release_confidence,
                ", ".join(sorted(_valid_confidence)),
            )
            min_release_confidence = "APPROXIMATE_DATE"

        config = cls(
            tmdb_api_key=tmdb_api_key,
            telegram_bot_token=telegram_bot_token,
            telegram_channel_id=telegram_channel_id,
            mongodb_uri=mongodb_uri,
            mongodb_database=mongodb_database,
            mongodb_collection=mongodb_collection,
            tmdb_region=tmdb_region,
            ott_providers=ott_providers,
            lookback_days=lookback_days,
            lookahead_days=lookahead_days,
            discover_lookback_days=discover_lookback_days,
            post_empty_update=post_empty_update,
            min_release_confidence=min_release_confidence,
        )

        logger.debug(
            "Config loaded — region=%s providers=%s window=-%d/+%d",
            config.tmdb_region,
            config.ott_providers,
            config.lookback_days,
            config.lookahead_days,
        )
        return config
