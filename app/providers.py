"""
providers.py — OTT provider normalization and alias mapping.

TMDB provider names sometimes differ from the names users configure.
This module maps every known TMDB name variant back to a single
canonical name so that filtering and deduplication are consistent.

To add a new provider or alias:
  1. Add the canonical name to PROVIDER_ALIASES with its known TMDB variants.
  2. Add an emoji to PROVIDER_EMOJIS.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Alias map
# key   = canonical provider name (what the user puts in OTT_PROVIDERS)
# value = list of TMDB provider name variants that map to this canonical name
# ---------------------------------------------------------------------------
PROVIDER_ALIASES: Dict[str, List[str]] = {
    "Netflix": [
        "Netflix",
        "Netflix basic with Ads",
    ],
    "Amazon Prime Video": [
        "Amazon Prime Video",
        "Prime Video",
        "Amazon Video",
    ],
    "JioHotstar": [
        "JioHotstar",
        "Jio Cinema",
        "JioCinema",
        "Disney+ Hotstar",
        "Hotstar",
        "Disney Plus Hotstar",
    ],
    "ZEE5": [
        "ZEE5",
        "Zee5",
    ],
    "Sony Pictures Networks India": [
        "Sony Pictures Networks India",
        "SonyLIV",
        "Sony Liv",
        "Sony LIV",
    ],
    "Aha": [
        "Aha",
        "aha",
    ],
    "Sun NXT": [
        "Sun NXT",
        "SunNXT",
    ],
    "ManoramaMAX": [
        "ManoramaMAX",
        "Manorama Max",
    ],
    "Apple TV+": [
        "Apple TV+",
        "Apple TV Plus",
        "Apple TV",
    ],
    "Lionsgate Play": [
        "Lionsgate Play",
        "Lionsgate",
    ],
    "MUBI": [
        "MUBI",
        "Mubi",
    ],
    "Curiosity Stream": [
        "Curiosity Stream",
        "CuriosityStream",
    ],
    "Voot": [
        "Voot",
        "Voot Select",
    ],
    "Hoichoi": [
        "Hoichoi",
    ],
    "ErosNow": [
        "ErosNow",
        "Eros Now",
    ],
    "BookMyShow Stream": [
        "BookMyShow Stream",
    ],
}

# ---------------------------------------------------------------------------
# Emoji / colour per provider for Telegram formatting
# ---------------------------------------------------------------------------
PROVIDER_EMOJIS: Dict[str, str] = {
    "Netflix": "🔴",
    "Amazon Prime Video": "🔵",
    "JioHotstar": "🟣",
    "ZEE5": "🟡",
    "Sony Pictures Networks India": "🟠",
    "Aha": "🟢",
    "Sun NXT": "🌟",
    "ManoramaMAX": "📺",
    "Apple TV+": "⚪",
    "Lionsgate Play": "🎬",
    "MUBI": "🎭",
    "Curiosity Stream": "🔬",
    "Voot": "🎯",
    "Hoichoi": "🎪",
    "ErosNow": "💫",
    "BookMyShow Stream": "🎟",
}

# Build a reverse lookup: TMDB name variant → canonical name
_REVERSE_MAP: Dict[str, str] = {}
for _canonical, _variants in PROVIDER_ALIASES.items():
    for _v in _variants:
        _REVERSE_MAP[_v.lower()] = _canonical


def normalize_provider(tmdb_name: str) -> Optional[str]:
    """
    Map a TMDB provider name to its canonical name.

    Returns None if the provider is not in the alias map.

    Example:
        normalize_provider("Prime Video")  →  "Amazon Prime Video"
        normalize_provider("SonyLIV")      →  "Sony Pictures Networks India"
        normalize_provider("Unknown")      →  None
    """
    return _REVERSE_MAP.get(tmdb_name.lower())


def get_emoji(canonical_name: str) -> str:
    """Return the emoji for a canonical provider name, or 📺 as fallback."""
    return PROVIDER_EMOJIS.get(canonical_name, "📺")


def filter_by_configured(
    tmdb_providers: List[Dict],
    configured_providers: List[str],
) -> List[str]:
    """
    Given a list of TMDB watch-provider dicts (each with a 'provider_name' key)
    and the user's configured canonical provider list, return the subset of
    canonical names that appear in both.

    Args:
        tmdb_providers: List of dicts from TMDB's flatrate/free/etc. arrays.
        configured_providers: Canonical names from OTT_PROVIDERS env var.

    Returns:
        Sorted list of matching canonical provider names (deduplicated).
    """
    configured_set = {p.lower() for p in configured_providers}
    matched: set[str] = set()

    for p in tmdb_providers:
        tmdb_name = p.get("provider_name", "")
        canonical = normalize_provider(tmdb_name)
        if canonical and canonical.lower() in configured_set:
            matched.add(canonical)

    return sorted(matched)
