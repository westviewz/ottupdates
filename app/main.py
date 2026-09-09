"""
main.py — Application orchestrator.

This is the entry point for the Railway Cron Job.

Run with:
    python -m app.main

Execution flow:
    1.  Load environment variables + validate config
    2.  Set up logging
    3.  Connect to MongoDB + ensure indexes
    4.  Determine today's date in Asia/Kolkata (IST)
    5.  Build the date window (lookback/lookahead)
    6.  Fetch releases from TMDB
    7.  Filter out already-posted items (MongoDB check)
    8.  Generate the Telegram message
    9.  Send the message to the Telegram channel
    10. Record successfully posted items in MongoDB
    11. Log summary
    12. Close MongoDB connection
    13. Exit 0 (success) or 1 (fatal error)
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta, timezone
from typing import List, Tuple

import pytz

from app.config import Config
from app.formatter import format_daily_post, format_empty_post
from app.mongodb import MongoDBClient
from app.releases import DateConfidence, DateWindow, Release, TMDBSource
from app.telegram_bot import TelegramBot

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

IST = pytz.timezone("Asia/Kolkata")


def setup_logging() -> None:
    """Configure the root logger with a timestamp-rich format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quieten noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def get_today_ist() -> date:
    """Return the current date in Asia/Kolkata timezone."""
    return datetime.now(tz=IST).date()


def build_date_window(today: date, lookback: int, lookahead: int) -> DateWindow:
    """Compute the inclusive date window around today."""
    return DateWindow(
        start=today - timedelta(days=lookback),
        end=today + timedelta(days=lookahead),
    )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def run() -> int:
    """
    Run the full OTT update workflow.

    Returns:
        0 on clean exit (including "no new releases").
        1 on fatal error.
    """
    setup_logging()

    now_utc = datetime.now(tz=timezone.utc)
    logger.info("=== OTT Update Bot starting — %s UTC ===", now_utc.isoformat())

    # Step 1: Load config
    try:
        config = Config.load()
    except SystemExit:
        # Config.load() already logged the error and called sys.exit(1)
        return 1

    today_ist = get_today_ist()
    logger.info("Today (IST): %s", today_ist.isoformat())

    # Step 2: Connect to MongoDB
    db = MongoDBClient(config)
    try:
        db.connect()
        db.ensure_indexes()
    except Exception as exc:
        logger.critical("MongoDB initialization failed: %s", exc)
        return 1

    try:
        return _workflow(config, db, today_ist, now_utc)
    except Exception as exc:
        logger.critical("Unexpected fatal error: %s", exc, exc_info=True)
        return 1
    finally:
        db.close()


def _workflow(
    config: Config,
    db: MongoDBClient,
    today_ist: date,
    now_utc: datetime,
) -> int:
    """Inner workflow — separated so the finally/close in run() always fires."""

    # Step 3: Build date window
    window = build_date_window(
        today_ist, config.lookback_days, config.lookahead_days
    )
    logger.info("Date window: %s", window)

    # Step 4: Fetch releases from TMDB
    source = TMDBSource(config)
    try:
        all_releases = source.fetch_releases(window)
    except Exception as exc:
        logger.error("Failed to fetch releases from TMDB: %s", exc, exc_info=True)
        all_releases = []

    logger.info("Total releases fetched from TMDB: %d", len(all_releases))

    # Step 5a: Apply confidence threshold
    # Parse the configured threshold string into a DateConfidence enum value.
    try:
        threshold = DateConfidence.from_string(config.min_release_confidence)
    except ValueError:
        logger.warning(
            "Could not parse MIN_RELEASE_CONFIDENCE=%r; defaulting to APPROXIMATE_DATE.",
            config.min_release_confidence,
        )
        threshold = DateConfidence.APPROXIMATE_DATE

    logger.info(
        "Confidence threshold: %s (releases below this level will be skipped)",
        threshold.name,
    )

    confident_releases: List[Release] = []
    skipped_low_confidence = 0
    for release in all_releases:
        if release.confidence.meets_threshold(threshold):
            confident_releases.append(release)
        else:
            skipped_low_confidence += 1
            logger.debug(
                "Skipping [%s] %s — confidence=%s | %s",
                release.media_type,
                release.title,
                release.confidence.name,
                release.date_note,
            )

    logger.info(
        "After confidence filter: %d releases kept, %d skipped",
        len(confident_releases),
        skipped_low_confidence,
    )

    # Step 5b: Expand releases by provider
    # Each (release, provider) pair is one potential post item.
    candidates: List[Tuple[Release, str]] = []
    for release in confident_releases:
        for provider in release.providers:
            candidates.append((release, provider))

    logger.info("Total (release, provider) candidates: %d", len(candidates))


    # Step 6: Filter already-posted items
    new_items: List[Tuple[Release, str]] = []
    skipped = 0
    for release, provider in candidates:
        if db.is_posted(release.tmdb_id, release.media_type, provider):
            logger.debug(
                "Skipping already-posted: [%s] %s — %s",
                release.media_type,
                release.title,
                provider,
            )
            skipped += 1
        else:
            new_items.append((release, provider))

    logger.info(
        "New items: %d | Already posted (skipped): %d",
        len(new_items),
        skipped,
    )

    # Step 7: Handle empty result
    if not new_items:
        logger.info("No new OTT releases to post today.")
        if config.post_empty_update:
            bot = TelegramBot(config.telegram_bot_token, config.telegram_channel_id)
            bot.send_message(format_empty_post())
            logger.info("Empty-update message sent.")
        return 0

    # Step 8: Format the Telegram message
    message = format_daily_post(new_items, today_ist)
    logger.debug("Formatted message (%d chars):\n%s", len(message), message)

    # Step 9: Send to Telegram
    bot = TelegramBot(config.telegram_bot_token, config.telegram_channel_id)
    message_id = bot.send_message(message)

    if message_id is None:
        logger.error(
            "Telegram send failed. NOT recording items in MongoDB "
            "(will retry on next run)."
        )
        # Return 1 so Railway can log the failed run, but it is not catastrophic
        return 1

    logger.info("Telegram message sent successfully (message_id=%d).", message_id)

    # Step 10: Record posted items in MongoDB
    recorded = 0
    for release, provider in new_items:
        if db.record_post(release, provider, posted_at=now_utc):
            recorded += 1

    # Step 11: Summary
    logger.info(
        "=== Run complete — posted %d new release(s) across %d provider(s) "
        "| skipped %d duplicate(s) | skipped %d below confidence threshold ===",
        len(new_items),
        len({p for _, p in new_items}),
        skipped,
        skipped_low_confidence,
    )
    logger.info("MongoDB records inserted: %d", recorded)

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for `python -m app.main`."""
    exit_code = run()
    logger.info("Exiting with code %d.", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
