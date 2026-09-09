"""
formatter.py — Telegram message formatter.

Builds the daily OTT release post as an HTML-formatted string.
All dynamic text from external APIs (title, language, etc.) is
HTML-escaped before inclusion to prevent injection or parse errors.

Telegram HTML tags supported: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="">
"""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.providers import get_emoji
from app.releases import DateConfidence, Release

logger = logging.getLogger(__name__)

# Map ISO 639-1 language codes to flag emoji + language name
LANGUAGE_MAP: Dict[str, Tuple[str, str]] = {
    "hi": ("🇮🇳", "Hindi"),
    "ta": ("🇮🇳", "Tamil"),
    "te": ("🇮🇳", "Telugu"),
    "ml": ("🇮🇳", "Malayalam"),
    "kn": ("🇮🇳", "Kannada"),
    "bn": ("🇮🇳", "Bengali"),
    "mr": ("🇮🇳", "Marathi"),
    "gu": ("🇮🇳", "Gujarati"),
    "pa": ("🇮🇳", "Punjabi"),
    "or": ("🇮🇳", "Odia"),
    "as": ("🇮🇳", "Assamese"),
    "en": ("🌐", "English"),
    "fr": ("🇫🇷", "French"),
    "es": ("🇪🇸", "Spanish"),
    "ko": ("🇰🇷", "Korean"),
    "ja": ("🇯🇵", "Japanese"),
    "zh": ("🇨🇳", "Chinese"),
    "ar": ("🇸🇦", "Arabic"),
    "pt": ("🇵🇹", "Portuguese"),
    "de": ("🇩🇪", "German"),
    "it": ("🇮🇹", "Italian"),
    "tr": ("🇹🇷", "Turkish"),
    "th": ("🇹🇭", "Thai"),
}

# Date formats
HEADER_DATE_FORMAT = "%d %B %Y"      # "10 September 2026"
ITEM_DATE_FORMAT = "%d %b"           # "10 Sep"


def format_daily_post(
    releases: List[Tuple["Release", str]],
    today: date,
) -> str:
    """
    Build the full Telegram HTML post for today's OTT releases.

    Args:
        releases: List of (Release, provider_canonical_name) tuples.
        today:    The logical date (IST) for the post header.

    Returns:
        A string formatted with Telegram HTML markup, ready to send.
    """
    if not releases:
        return "📺 <b>OTT UPDATE</b>\n\nNo new releases found for today."

    # Group by provider
    by_provider: Dict[str, List[Release]] = defaultdict(list)
    for release, provider in releases:
        by_provider[provider].append(release)

    header_date = today.strftime(HEADER_DATE_FORMAT).upper()
    lines: List[str] = [
        f"🎬 <b>OTT RELEASES — {html.escape(header_date)}</b>",
        "",
    ]

    for provider, provider_releases in sorted(by_provider.items()):
        emoji = get_emoji(provider)
        lines.append(f"{emoji} <b>{html.escape(provider.upper())}</b>")
        lines.append("")

        for release in provider_releases:
            title_escaped = html.escape(release.title)
            lang_info = _format_language(release.original_language)
            date_info = _format_date(release.release_date, release.confidence)
            media_icon = "🎬" if release.media_type == "movie" else "📺"

            lines.append(f"  • {media_icon} <b>{title_escaped}</b>")
            if lang_info:
                lines.append(f"    {lang_info}")
            if date_info:
                lines.append(f"    📅 {html.escape(date_info)}")
            lines.append("")

    # Summary footer
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🔥 <b>TODAY'S COUNT</b>")
    for provider, provider_releases in sorted(by_provider.items()):
        count = len(provider_releases)
        emoji = get_emoji(provider)
        lines.append(
            f"  {emoji} {html.escape(provider)} • <b>{count}</b>"
        )

    lines.append("")
    lines.append(
        '<i>Data source: <a href="https://www.themoviedb.org/">TMDB</a></i>'
    )

    return "\n".join(lines)


def format_empty_post() -> str:
    """Return the message to send when no new releases are found."""
    return "📺 <b>OTT UPDATE</b>\n\nNo new releases found for today."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_language(lang_code: str) -> Optional[str]:
    """Return a formatted language string with flag emoji, or None."""
    if not lang_code:
        return None
    lang_code = lang_code.lower()
    if lang_code in LANGUAGE_MAP:
        flag, name = LANGUAGE_MAP[lang_code]
        return f"{flag} {name}"
    return f"🌐 {html.escape(lang_code.upper())}"


def _format_date(
    release_date: str,
    confidence: DateConfidence = DateConfidence.APPROXIMATE_DATE,
) -> Optional[str]:
    """
    Parse a YYYY-MM-DD release date string and return a short formatted date.

    Prefixes the date with "~" for APPROXIMATE_DATE to signal to readers
    that the date is an approximation (the TMDB release/air date, not a
    confirmed OTT platform-addition date).

    Returns None if the date is empty or cannot be parsed.
    """
    if not release_date:
        return None
    try:
        from datetime import datetime
        dt = datetime.strptime(release_date, "%Y-%m-%d")
        formatted = dt.strftime(ITEM_DATE_FORMAT)
        if confidence == DateConfidence.APPROXIMATE_DATE:
            # ~ prefix signals the date is approximate
            return f"~{formatted}"
        return formatted
    except ValueError:
        return release_date  # Return as-is if format is unexpected
