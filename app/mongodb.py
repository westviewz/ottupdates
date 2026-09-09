"""
mongodb.py — MongoDB client for duplicate tracking.

Responsibilities:
  - Connect to MongoDB with a sensible timeout.
  - Create/ensure the unique compound index on startup.
  - Check whether a (tmdb_id, media_type, provider) tuple has been posted.
  - Record a successful post.
  - Close the connection cleanly.

The unique index prevents duplicate posts even if the bot is run multiple
times on the same day or if Railway fires the cron job more than once.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pymongo
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import (
    ConnectionFailure,
    DuplicateKeyError,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from app.config import Config
from app.releases import Release

logger = logging.getLogger(__name__)

# MongoDB connection timeouts in milliseconds
CONNECT_TIMEOUT_MS = 5_000
SERVER_SELECTION_TIMEOUT_MS = 5_000


class MongoDBClient:
    """
    Manages the MongoDB connection and all database operations for the bot.

    Usage:
        db = MongoDBClient(config)
        db.connect()
        db.ensure_indexes()
        # ... use db.is_posted() and db.record_post() ...
        db.close()
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client: Optional[MongoClient] = None
        self._collection: Optional[Collection] = None

    def connect(self) -> None:
        """
        Open a connection to MongoDB.

        Raises SystemExit on connection failure so the cron job exits cleanly
        rather than hanging or producing a misleading traceback.
        """
        try:
            self._client = MongoClient(
                self.config.mongodb_uri,
                connectTimeoutMS=CONNECT_TIMEOUT_MS,
                serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
            )
            # Trigger an actual connection attempt
            self._client.admin.command("ping")

            db = self._client[self.config.mongodb_database]
            self._collection = db[self.config.mongodb_collection]

            logger.info(
                "MongoDB connected — database=%s collection=%s",
                self.config.mongodb_database,
                self.config.mongodb_collection,
            )
        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            logger.critical("MongoDB connection failed: %s", exc)
            raise

    def ensure_indexes(self) -> None:
        """
        Create the unique compound index on (tmdb_id, media_type, provider)
        if it does not already exist.

        Using create_index with unique=True is idempotent — MongoDB will not
        recreate the index if it already exists with the same specification.
        """
        if self._collection is None:
            raise RuntimeError("Call connect() before ensure_indexes().")
        try:
            index_name = self._collection.create_index(
                [
                    ("tmdb_id", pymongo.ASCENDING),
                    ("media_type", pymongo.ASCENDING),
                    ("provider", pymongo.ASCENDING),
                ],
                unique=True,
                name="unique_tmdb_media_provider",
                background=True,
            )
            logger.info("MongoDB index ready: %s", index_name)
        except OperationFailure as exc:
            logger.error("Could not create MongoDB index: %s", exc)
            raise

    def is_posted(
        self, tmdb_id: int, media_type: str, provider: str
    ) -> bool:
        """
        Return True if (tmdb_id, media_type, provider) already exists in the
        collection, meaning this title has been posted previously.
        """
        if self._collection is None:
            raise RuntimeError("Call connect() before is_posted().")
        try:
            result = self._collection.find_one(
                {
                    "tmdb_id": tmdb_id,
                    "media_type": media_type,
                    "provider": provider,
                },
                projection={"_id": 1},
            )
            return result is not None
        except OperationFailure as exc:
            logger.error(
                "MongoDB is_posted query failed for tmdb_id=%d: %s", tmdb_id, exc
            )
            # Fail open — treat as not posted to avoid silently dropping titles
            return False

    def record_post(
        self,
        release: Release,
        provider: str,
        posted_at: Optional[datetime] = None,
    ) -> bool:
        """
        Insert a document recording that a title/provider was successfully posted.

        Args:
            release:   The Release object that was posted.
            provider:  The canonical provider name.
            posted_at: UTC datetime of the post (defaults to now).

        Returns:
            True if the record was inserted, False if it already existed
            (DuplicateKeyError — safe to ignore) or another error occurred.
        """
        if self._collection is None:
            raise RuntimeError("Call connect() before record_post().")

        if posted_at is None:
            posted_at = datetime.now(tz=timezone.utc)

        doc = {
            "tmdb_id": release.tmdb_id,
            "media_type": release.media_type,
            "provider": provider,
            "title": release.title,
            "release_date": release.release_date,
            "posted_at": posted_at.isoformat(),
        }

        try:
            self._collection.insert_one(doc)
            logger.debug(
                "MongoDB recorded: [%s] %s — %s",
                release.media_type,
                release.title,
                provider,
            )
            return True
        except DuplicateKeyError:
            # Race condition or re-run — already recorded, that's fine
            logger.debug(
                "MongoDB duplicate (already recorded): %s / %s / %s",
                release.tmdb_id,
                release.media_type,
                provider,
            )
            return False
        except OperationFailure as exc:
            logger.error(
                "MongoDB record_post failed for '%s': %s", release.title, exc
            )
            return False

    def close(self) -> None:
        """Close the MongoDB connection cleanly."""
        if self._client:
            try:
                self._client.close()
                logger.info("MongoDB connection closed.")
            except Exception as exc:
                logger.warning("Error closing MongoDB connection: %s", exc)
            finally:
                self._client = None
                self._collection = None
