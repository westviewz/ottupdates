"""
tmdb.py — TMDB API client.

Encapsulates all HTTP communication with the TMDB v3 API.
Handles authentication, pagination, retries, and rate limiting.

TMDB API documentation: https://developer.themoviedb.org/docs
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# Maximum pages to fetch per discover query (TMDB caps total results at 500 pages)
MAX_PAGES = 10

# Request timeout (connect, read) in seconds
REQUEST_TIMEOUT = (10, 30)


class TMDBError(Exception):
    """Raised for non-recoverable TMDB API errors."""


class TMDBClient:
    """
    Thin wrapper around the TMDB REST API.

    Authentication uses the Bearer token (API Read Access Token) via the
    Authorization header, which is the recommended approach for v3 endpoints.
    """

    def __init__(self, api_key: str, region: str = "IN") -> None:
        """
        Args:
            api_key: TMDB API Read Access Token (Bearer token).
            region:  ISO 3166-1 alpha-2 country code for watch-region filtering.
        """
        self.api_key = api_key
        self.region = region
        self._session = self._build_session()

    # ------------------------------------------------------------------
    # Public discovery methods
    # ------------------------------------------------------------------

    def discover_movies(
        self,
        with_watch_providers: Optional[str] = None,
        date_gte: Optional[str] = None,
        date_lte: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Paginate through /discover/movie filtered by watch providers and date.

        Args:
            with_watch_providers: Pipe-separated TMDB provider IDs, e.g. "8|337".
            date_gte: Minimum primary_release_date (YYYY-MM-DD).
            date_lte: Maximum primary_release_date (YYYY-MM-DD).

        Yields:
            Individual movie result dicts from TMDB.
        """
        params: Dict[str, Any] = {
            "watch_region": self.region,
            "sort_by": "primary_release_date.desc",
            "include_adult": "false",
            "include_video": "false",
        }
        if with_watch_providers:
            params["with_watch_providers"] = with_watch_providers
        if date_gte:
            params["primary_release_date.gte"] = date_gte
        if date_lte:
            params["primary_release_date.lte"] = date_lte

        yield from self._paginate("/discover/movie", params)

    def discover_tv(
        self,
        with_watch_providers: Optional[str] = None,
        date_gte: Optional[str] = None,
        date_lte: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Paginate through /discover/tv filtered by watch providers and date.

        Args:
            with_watch_providers: Pipe-separated TMDB provider IDs, e.g. "8|337".
            date_gte: Minimum first_air_date (YYYY-MM-DD).
            date_lte: Maximum first_air_date (YYYY-MM-DD).

        Yields:
            Individual TV show result dicts from TMDB.
        """
        params: Dict[str, Any] = {
            "watch_region": self.region,
            "sort_by": "first_air_date.desc",
            "include_adult": "false",
        }
        if with_watch_providers:
            params["with_watch_providers"] = with_watch_providers
        if date_gte:
            params["first_air_date.gte"] = date_gte
        if date_lte:
            params["first_air_date.lte"] = date_lte

        yield from self._paginate("/discover/tv", params)

    def get_watch_providers(
        self, tmdb_id: int, media_type: str
    ) -> Dict[str, Any]:
        """
        Fetch watch provider data for a single title.

        Returns the full results dict keyed by country code.
        Returns {} on error (non-fatal — caller handles missing data).

        Args:
            tmdb_id:    TMDB movie or TV show ID.
            media_type: "movie" or "tv".
        """
        endpoint = f"/{media_type}/{tmdb_id}/watch/providers"
        try:
            data = self._get(endpoint, {})
            return data.get("results", {})
        except TMDBError as exc:
            logger.warning(
                "Could not fetch watch providers for %s %d: %s",
                media_type,
                tmdb_id,
                exc,
            )
            return {}

    def get_movie_release_dates(self, tmdb_id: int) -> Dict[str, Any]:
        """
        Fetch per-country release dates for a movie via /movie/{id}/release_dates.

        TMDB release type codes:
            1=Premiere  2=Theatrical(limited)  3=Theatrical
            4=Digital   5=Physical             6=TV

        Type 4 (Digital) is used as a proxy for OTT streaming availability.

        Returns the full API response dict (with a 'results' list keyed by
        country). Returns {} on error (non-fatal — caller handles missing data).

        Args:
            tmdb_id: TMDB movie ID.
        """
        endpoint = f"/movie/{tmdb_id}/release_dates"
        try:
            return self._get(endpoint, {})
        except TMDBError as exc:
            logger.debug(
                "Could not fetch release_dates for movie %d: %s", tmdb_id, exc
            )
            return {}

    def get_provider_list(self, media_type: str = "movie") -> List[Dict[str, Any]]:
        """
        Fetch the full list of streaming providers available from TMDB.

        Args:
            media_type: "movie" or "tv".

        Returns:
            List of provider dicts with 'provider_id' and 'provider_name' keys.
        """
        endpoint = f"/watch/providers/{media_type}"
        params = {"watch_region": self.region, "language": "en-US"}
        try:
            data = self._get(endpoint, params)
            return data.get("results", [])
        except TMDBError as exc:
            logger.warning("Could not fetch provider list for %s: %s", media_type, exc)
            return []

    def poster_url(self, poster_path: Optional[str]) -> Optional[str]:
        """Return a full poster image URL or None."""
        if not poster_path:
            return None
        return f"{TMDB_IMAGE_BASE_URL}{poster_path}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _paginate(
        self, endpoint: str, params: Dict[str, Any]
    ) -> Iterator[Dict[str, Any]]:
        """
        Iterate over all pages of a TMDB list endpoint up to MAX_PAGES.

        Yields individual result items.
        """
        page = 1
        total_pages = 1  # Updated after first response

        while page <= min(total_pages, MAX_PAGES):
            params["page"] = page
            try:
                data = self._get(endpoint, params)
            except TMDBError as exc:
                logger.error("TMDB paginate error on page %d: %s", page, exc)
                break

            results = data.get("results", [])
            total_pages = data.get("total_pages", 1)
            total_results = data.get("total_results", 0)

            logger.debug(
                "TMDB %s page %d/%d — %d results (total %d)",
                endpoint,
                page,
                min(total_pages, MAX_PAGES),
                len(results),
                total_results,
            )

            yield from results

            if page >= total_pages:
                break
            page += 1

    def _get(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform a GET request against the TMDB API.

        Handles 429 rate-limiting with a backoff wait.
        Raises TMDBError for non-recoverable HTTP errors.
        """
        url = f"{TMDB_BASE_URL}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        for attempt in range(3):
            try:
                response = self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "5"))
                    logger.warning(
                        "TMDB rate limit hit; waiting %d seconds (attempt %d/3).",
                        retry_after,
                        attempt + 1,
                    )
                    time.sleep(retry_after + 1)
                    continue

                if response.status_code == 404:
                    # Not all titles have provider data — treat as empty
                    return {}

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                logger.warning(
                    "TMDB request timed out (attempt %d/3): %s", attempt + 1, url
                )
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise TMDBError(f"TMDB request timed out after 3 attempts: {url}")

            except requests.exceptions.HTTPError as exc:
                raise TMDBError(
                    f"TMDB HTTP error {response.status_code} for {url}: {exc}"
                ) from exc

            except requests.exceptions.RequestException as exc:
                if attempt < 2:
                    logger.warning(
                        "TMDB request error (attempt %d/3): %s", attempt + 1, exc
                    )
                    time.sleep(2 ** attempt)
                    continue
                raise TMDBError(f"TMDB request failed: {exc}") from exc

        raise TMDBError(f"TMDB request failed after retries: {url}")

    @staticmethod
    def _build_session() -> requests.Session:
        """Create a requests Session with a retry adapter for connection errors."""
        session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
