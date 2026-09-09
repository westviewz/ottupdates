"""
releases.py — Release detection and source abstraction.

Defines:
  - DateConfidence: enum classifying how reliable a release date is.
  - Release dataclass: normalised representation of an OTT title.
  - DateWindow dataclass: the date range to query.
  - Source ABC: interface that any data source must implement.
  - TMDBSource: concrete implementation using the TMDB API.

Design intent:
    Later, other sources (e.g. RSSSource, JustWatchSource) can be added
    without modifying the rest of the application:

        sources = [TMDBSource(config), RSSSource(config)]
        all_releases = []
        for source in sources:
            all_releases.extend(source.fetch_releases(window))

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IMPORTANT — TMDB data limitations and confidence model
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TMDB's watch-provider data reflects the *current* streaming status of a
title, NOT the date it was added to a platform. This means:

  - A film released theatrically six months ago but newly added to
    Netflix TODAY will still show up in TMDB's watch-provider data —
    but will NOT be surfaced by the date-window filter unless the bot
    explicitly finds the digital-release date.
  - Missing watch-provider data for India is NOT proof that a title is
    unavailable in India — TMDB relies on community contributions and
    coverage is incomplete.
  - The bot NEVER invents a release date or provider. If the date
    cannot be established with sufficient confidence, the title is
    skipped entirely.

Date confidence levels (DateConfidence enum)
--------------------------------------------
CONFIRMED_DATE:
    A TMDB digital/OTT release date (release_type=4 for India or a
    fallback country) exists within the date window. This is the
    strongest signal available from TMDB.

APPROXIMATE_DATE:
    The title's primary_release_date (movies) or first_air_date (TV)
    falls within the date window. TMDB records this as the
    theatrical/broadcast premiere date; it is used as a *proxy* for
    streaming availability only. It may be accurate for direct-to-OTT
    releases, or inaccurate for titles that went to streaming months
    after theatrical release.
    ⚠ This date is an approximation — the post header will mark it as
    such with a "~" prefix.

UNKNOWN_DATE:
    No usable release date is available, or the available date falls
    entirely outside the date window. Titles at this level are skipped
    by default and NEVER posted.

The configurable threshold MIN_RELEASE_CONFIDENCE (default:
APPROXIMATE_DATE) controls the minimum confidence required for a title
to be included in the Telegram post.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from app.config import Config
from app.providers import filter_by_configured
from app.tmdb import TMDBClient, TMDBError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date confidence enum
# ---------------------------------------------------------------------------


class DateConfidence(Enum):
    """
    Indicates how reliably TMDB data supports the claim that a title became
    available on a streaming platform around the queried date.

    Ordered from highest to lowest confidence. Comparison uses .value so
    that CONFIRMED > APPROXIMATE > UNKNOWN.
    """

    CONFIRMED_DATE = 3    # TMDB digital release date within window
    APPROXIMATE_DATE = 2  # Primary/air release date within window (proxy)
    UNKNOWN_DATE = 1      # No usable date — title should be skipped

    def meets_threshold(self, threshold: "DateConfidence") -> bool:
        """Return True if this confidence is at or above the threshold."""
        return self.value >= threshold.value

    @classmethod
    def from_string(cls, value: str) -> "DateConfidence":
        """Parse a string like 'APPROXIMATE_DATE' into the enum member."""
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(
                f"Unknown DateConfidence value: '{value}'. "
                f"Valid values: {[m.name for m in cls]}"
            )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DateWindow:
    """Inclusive date range used to filter releases."""

    start: date
    end: date

    def contains(self, d: date) -> bool:
        """Return True if d falls within [start, end] inclusive."""
        return self.start <= d <= self.end

    def __str__(self) -> str:
        return f"{self.start} → {self.end}"


@dataclass
class Release:
    """
    Normalised representation of a single OTT title + provider combination.

    A single TMDB title may produce multiple Release objects if it is
    available on more than one configured streaming platform.

    Fields
    ------
    confidence:
        How reliable the release_date is as an OTT-availability signal.
        See DateConfidence for the full definition.
    release_date:
        The best available date string (YYYY-MM-DD). For CONFIRMED_DATE
        this is the TMDB digital release date; for APPROXIMATE_DATE it
        is the primary/air date. Empty string when unknown.
    date_note:
        A short human-readable note explaining the date source, e.g.
        "digital release (IN)" or "primary release date (approximate)".
        Shown in debug logs; not displayed to end-users in the post.
    """

    tmdb_id: int
    media_type: str              # "movie" | "tv"
    title: str
    release_date: str            # YYYY-MM-DD — best available date
    providers: List[str]         # Canonical provider names (after filtering)
    original_language: str       # ISO 639-1 code, e.g. "hi", "ta", "en"
    confidence: DateConfidence = DateConfidence.UNKNOWN_DATE
    date_note: str = ""          # Source of the date (for logs/transparency)
    poster_path: Optional[str] = None
    overview: str = ""


# ---------------------------------------------------------------------------
# Source abstraction
# ---------------------------------------------------------------------------


class Source(ABC):
    """
    Abstract base class for OTT release data sources.

    Any concrete source must implement fetch_releases() and return a list
    of Release objects filtered to the configured OTT providers.
    """

    @abstractmethod
    def fetch_releases(self, window: DateWindow) -> List[Release]:
        """
        Return releases that fall within the given date window and match
        the configured OTT providers.

        Each returned Release must have a DateConfidence value set.
        Callers are responsible for filtering by confidence threshold.
        """
        ...


# ---------------------------------------------------------------------------
# TMDB source implementation
# ---------------------------------------------------------------------------


class TMDBSource(Source):
    """
    Fetches OTT releases from the TMDB API.

    Strategy
    --------
    1. Resolve the configured canonical provider names to TMDB provider IDs
       by querying /watch/providers/movie and /watch/providers/tv.
    2. Run /discover/movie and /discover/tv with:
         - with_watch_providers = pipe-joined IDs of configured providers
         - watch_region = IN (or configured region)
         - date window filters on primary_release_date / first_air_date
    3. For each discovered title, fetch its per-region watch-provider detail
       to get the exact flatrate/streaming list.
    4. For movies, additionally call /movie/{id}/release_dates to look for a
       digital release date (type 4) in the configured region — this upgrades
       confidence from APPROXIMATE to CONFIRMED when the date is in-window.
    5. Normalize and filter providers against the configured list.
    6. Classify each title's DateConfidence.
    7. Return Release objects only for titles with at least one provider match.
       Titles with UNKNOWN_DATE confidence are returned but will be filtered
       by the caller based on MIN_RELEASE_CONFIDENCE.

    NOTE: The bot NEVER invents a release date. If no date is available or
    determinable, confidence is set to UNKNOWN_DATE and the title is excluded
    by the default threshold.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = TMDBClient(
            api_key=config.tmdb_api_key,
            region=config.tmdb_region,
        )

    def fetch_releases(self, window: DateWindow) -> List[Release]:
        """
        Fetch and return filtered releases within the given date window.

        Returns an empty list if TMDB is unreachable or returns no results.
        All returned releases have a DateConfidence value. The caller is
        responsible for applying the MIN_RELEASE_CONFIDENCE threshold.
        """
        logger.info("TMDB fetch — window: %s", window)

        # Step 1: Resolve provider IDs
        provider_ids = self._resolve_provider_ids()
        if not provider_ids:
            logger.warning(
                "No TMDB provider IDs found for configured providers %s. "
                "Falling back to unfiltered discover (may return many results).",
                self.config.ott_providers,
            )
            provider_id_str: Optional[str] = None
        else:
            provider_id_str = "|".join(str(pid) for pid in provider_ids)
            logger.info(
                "Resolved %d provider IDs: %s", len(provider_ids), provider_id_str
            )

        date_gte = window.start.isoformat()
        date_lte = window.end.isoformat()

        releases: List[Release] = []
        seen_combinations: set[tuple] = set()  # (tmdb_id, media_type, provider)

        # Step 2a: Discover movies
        movies_found = 0
        try:
            for item in self.client.discover_movies(
                with_watch_providers=provider_id_str,
                date_gte=date_gte,
                date_lte=date_lte,
            ):
                movies_found += 1
                release = self._process_item(item, "movie", seen_combinations, window)
                if release:
                    releases.append(release)
        except TMDBError as exc:
            logger.error("TMDB movie discover failed: %s", exc)

        logger.info("TMDB movies scanned: %d", movies_found)

        # Step 2b: Discover TV shows
        tv_found = 0
        try:
            for item in self.client.discover_tv(
                with_watch_providers=provider_id_str,
                date_gte=date_gte,
                date_lte=date_lte,
            ):
                tv_found += 1
                release = self._process_item(item, "tv", seen_combinations, window)
                if release:
                    releases.append(release)
        except TMDBError as exc:
            logger.error("TMDB TV discover failed: %s", exc)

        logger.info("TMDB TV shows scanned: %d", tv_found)
        logger.info(
            "TMDB releases after provider filter (pre-confidence): %d", len(releases)
        )
        return releases

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_item(
        self,
        item: Dict[str, Any],
        media_type: str,
        seen: set[tuple],
        window: DateWindow,
    ) -> Optional[Release]:
        """
        Process one TMDB discover result into a Release, or return None.

        Steps:
          1. Extract core fields. Skip if tmdb_id is missing.
          2. Fetch watch-provider data for the configured region.
          3. Filter to configured providers. Skip if none match.
          4. Classify DateConfidence (movies get an extra digital-date check).
          5. Deduplicate (tmdb_id, media_type, provider) combos.
          6. Return Release with confidence set.

        Returns None if the item should be entirely skipped (no provider
        match, or missing tmdb_id). UNKNOWN_DATE items are returned — the
        caller filters by threshold so the log can show the skip reason.
        """
        tmdb_id: int = item.get("id", 0)
        if not tmdb_id:
            return None

        title = (
            item.get("title") or item.get("name") or "Unknown Title"
        ).strip()

        # Primary date from discover result (may be theatrical, not OTT)
        primary_date_str = (
            item.get("release_date") or item.get("first_air_date") or ""
        ).strip()

        original_language = item.get("original_language", "").lower()
        poster_path = item.get("poster_path")
        overview = item.get("overview", "")

        # --- Provider check ---
        provider_results = self.client.get_watch_providers(tmdb_id, media_type)
        region_data: Dict[str, Any] = provider_results.get(
            self.config.tmdb_region, {}
        )

        if not region_data:
            logger.debug(
                "No %s watch-provider data for %s %d (%s)",
                self.config.tmdb_region,
                media_type,
                tmdb_id,
                title,
            )
            return None

        # Collect flatrate + free + ads tiers
        all_providers: List[Dict] = []
        for tier in ("flatrate", "free", "ads"):
            all_providers.extend(region_data.get(tier, []))

        if not all_providers:
            return None

        matched_providers = filter_by_configured(
            all_providers, self.config.ott_providers
        )
        if not matched_providers:
            return None

        # --- Confidence classification ---
        confidence, best_date, date_note = self._classify_confidence(
            tmdb_id=tmdb_id,
            media_type=media_type,
            primary_date_str=primary_date_str,
            window=window,
        )

        logger.debug(
            "[%s] %s — confidence=%s date=%s note=%s providers=%s",
            media_type,
            title,
            confidence.name,
            best_date,
            date_note,
            matched_providers,
        )

        # --- Deduplication ---
        unique_providers = []
        for provider in matched_providers:
            key = (tmdb_id, media_type, provider)
            if key not in seen:
                seen.add(key)
                unique_providers.append(provider)

        if not unique_providers:
            return None

        return Release(
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            release_date=best_date,
            providers=unique_providers,
            original_language=original_language,
            confidence=confidence,
            date_note=date_note,
            poster_path=poster_path,
            overview=overview,
        )

    def _classify_confidence(
        self,
        tmdb_id: int,
        media_type: str,
        primary_date_str: str,
        window: DateWindow,
    ) -> tuple[DateConfidence, str, str]:
        """
        Determine the date confidence for a title and return the best
        available date string.

        Returns:
            (confidence, best_date_str, date_note)

        For movies:
            Tries to find a TMDB digital release date (type 4) in the
            configured region. Falls back to US. If found within the
            window → CONFIRMED_DATE.
            Falls back to primary_release_date → APPROXIMATE_DATE if
            that is within the window.

        For TV shows:
            Only first_air_date is available (no per-country digital date
            endpoint for TV). If in window → APPROXIMATE_DATE.

        If no usable date exists or all dates are outside the window:
            → UNKNOWN_DATE with empty date string.

        NOTE: This method never fabricates a date. If TMDB does not
        provide a date, the date string is returned as empty and
        confidence is UNKNOWN_DATE.
        """
        if media_type == "movie":
            return self._classify_movie_confidence(
                tmdb_id, primary_date_str, window
            )
        else:
            return self._classify_tv_confidence(primary_date_str, window)

    def _classify_movie_confidence(
        self,
        tmdb_id: int,
        primary_date_str: str,
        window: DateWindow,
    ) -> tuple[DateConfidence, str, str]:
        """
        Classify confidence for a movie by checking TMDB release_dates.

        TMDB release type codes:
            1 = Premiere, 2 = Theatrical (limited), 3 = Theatrical,
            4 = Digital, 5 = Physical, 6 = TV
        We prefer type 4 (Digital) as it is closest to OTT availability.
        """
        digital_date = self._get_digital_release_date(tmdb_id)
        if digital_date:
            if window.contains(digital_date):
                return (
                    DateConfidence.CONFIRMED_DATE,
                    digital_date.isoformat(),
                    f"digital release date from TMDB ({self.config.tmdb_region} or US)",
                )
            else:
                # Digital date exists but outside window — don't use it
                # as confirmation; fall through to primary date check.
                logger.debug(
                    "Movie %d: digital date %s is outside window %s",
                    tmdb_id,
                    digital_date,
                    window,
                )

        # Fall back to primary_release_date
        return self._check_primary_date(
            primary_date_str,
            window,
            note="primary release date (approximate — may be theatrical, not OTT)",
        )

    def _classify_tv_confidence(
        self,
        primary_date_str: str,
        window: DateWindow,
    ) -> tuple[DateConfidence, str, str]:
        """
        Classify confidence for a TV show.

        TMDB does not expose a per-country digital/streaming premiere date
        for TV series via public API endpoints. first_air_date is used as
        a proxy. Direct-to-OTT series (where first_air_date IS the
        streaming debut) will be classified correctly; series that first
        aired on broadcast TV and later moved to streaming will not be
        detected unless their first_air_date falls in the window.
        """
        return self._check_primary_date(
            primary_date_str,
            window,
            note="first air date (approximate — OTT streaming date may differ)",
        )

    def _check_primary_date(
        self,
        date_str: str,
        window: DateWindow,
        note: str,
    ) -> tuple[DateConfidence, str, str]:
        """
        Evaluate a primary/air date string against the window.

        Returns APPROXIMATE_DATE if the date parses and is within the window,
        otherwise UNKNOWN_DATE. Never returns CONFIRMED_DATE.
        """
        if not date_str:
            return DateConfidence.UNKNOWN_DATE, "", "no date available from TMDB"

        try:
            from datetime import datetime
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.debug("Unparseable date string: %r", date_str)
            return DateConfidence.UNKNOWN_DATE, "", "date string could not be parsed"

        if window.contains(d):
            return DateConfidence.APPROXIMATE_DATE, date_str, note

        return (
            DateConfidence.UNKNOWN_DATE,
            "",
            f"date {date_str} is outside window {window}",
        )

    def _get_digital_release_date(self, tmdb_id: int) -> Optional[date]:
        """
        Fetch TMDB's per-country release dates for a movie and return the
        earliest digital (type 4) release date found for the configured
        region (e.g. IN) or, as a fallback, for US.

        Returns None if no digital date is available or the API call fails.

        This is called only for movies; TV shows do not have this endpoint.
        """
        try:
            data = self.client.get_movie_release_dates(tmdb_id)
        except TMDBError as exc:
            logger.debug(
                "Could not fetch release_dates for movie %d: %s", tmdb_id, exc
            )
            return None

        results: List[Dict[str, Any]] = data.get("results", [])

        # Build a map: country → list of digital dates
        digital_dates_by_country: Dict[str, List[date]] = {}
        for entry in results:
            country = entry.get("iso_3166_1", "")
            release_entries = entry.get("release_dates", [])
            for r in release_entries:
                if r.get("type") == 4:  # Digital
                    raw_date = r.get("release_date", "")
                    if raw_date:
                        try:
                            from datetime import datetime
                            # TMDB format: "2026-09-10T00:00:00.000Z"
                            d = datetime.fromisoformat(
                                raw_date.replace("Z", "+00:00")
                            ).date()
                            digital_dates_by_country.setdefault(country, []).append(d)
                        except (ValueError, TypeError):
                            pass

        # Prefer the configured region; fall back to US
        for country in (self.config.tmdb_region, "US"):
            dates = digital_dates_by_country.get(country)
            if dates:
                return min(dates)  # Earliest digital date in that country

        return None

    def _resolve_provider_ids(self) -> List[int]:
        """
        Map configured canonical provider names to TMDB provider IDs.

        Queries both /watch/providers/movie and /watch/providers/tv,
        merges the results, and returns deduplicated IDs.
        """
        all_provider_data: List[Dict[str, Any]] = []

        for media_type in ("movie", "tv"):
            providers = self.client.get_provider_list(media_type)
            all_provider_data.extend(providers)

        # Build a map: TMDB provider_name (lowercased) → provider_id
        name_to_id: Dict[str, int] = {}
        for p in all_provider_data:
            name = p.get("provider_name", "")
            pid = p.get("provider_id")
            if name and pid is not None:
                name_to_id[name.lower()] = pid

        # Match configured providers via alias normalization
        matched_ids: set[int] = set()
        for configured_name in self.config.ott_providers:
            from app.providers import PROVIDER_ALIASES
            aliases = PROVIDER_ALIASES.get(configured_name, [configured_name])
            for alias in aliases:
                pid = name_to_id.get(alias.lower())
                if pid is not None:
                    matched_ids.add(pid)
                    break

        return sorted(matched_ids)
