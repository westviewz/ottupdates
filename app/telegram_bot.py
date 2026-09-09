"""
telegram_bot.py — Telegram channel poster.

Sends messages and photos to a Telegram channel via the Bot HTTP API.
Uses direct HTTP requests (no async framework) — appropriate for a
one-shot batch job that does not need a polling loop.

Telegram Bot API docs: https://core.telegram.org/bots/api
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"

# Maximum message length accepted by Telegram (4096 chars for HTML)
MAX_MESSAGE_LENGTH = 4096

# Request timeout for Telegram API calls
REQUEST_TIMEOUT = (10, 30)


class TelegramError(Exception):
    """Raised for non-recoverable Telegram API errors."""


class TelegramBot:
    """
    Thin wrapper around the Telegram Bot HTTP API.

    Only implements what the bot needs:
      - sendMessage (HTML parse mode)
      - sendPhoto   (with HTML caption)
    """

    def __init__(self, token: str, channel_id: str) -> None:
        """
        Args:
            token:      Telegram bot token from @BotFather.
            channel_id: Channel username (@mychannel) or numeric ID (-100...).
        """
        self.token = token
        self.channel_id = channel_id
        self._base_url = f"https://api.telegram.org/bot{token}"

    def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> Optional[int]:
        """
        Send a text message to the configured channel.

        Args:
            text:                    Message text (HTML or Markdown).
            parse_mode:              "HTML" or "Markdown".
            disable_web_page_preview: Suppress link previews.

        Returns:
            The Telegram message_id on success, or None on failure.
        """
        # Truncate if over Telegram's limit (shouldn't happen in normal use)
        if len(text) > MAX_MESSAGE_LENGTH:
            logger.warning(
                "Message too long (%d chars); truncating to %d.",
                len(text),
                MAX_MESSAGE_LENGTH,
            )
            text = text[: MAX_MESSAGE_LENGTH - 3] + "..."

        payload = {
            "chat_id": self.channel_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }

        return self._post("sendMessage", payload)

    def send_photo(
        self,
        photo_url: str,
        caption: str = "",
        parse_mode: str = "HTML",
    ) -> Optional[int]:
        """
        Send a photo with an optional caption to the configured channel.

        Telegram captions are limited to 1024 characters.

        Args:
            photo_url:  Publicly accessible image URL.
            caption:    Optional caption text (HTML or Markdown).
            parse_mode: "HTML" or "Markdown".

        Returns:
            The Telegram message_id on success, or None on failure.
        """
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        payload = {
            "chat_id": self.channel_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": parse_mode,
        }

        return self._post("sendPhoto", payload)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _post(self, method: str, payload: dict) -> Optional[int]:
        """
        POST to a Telegram Bot API method with retry on transient errors.

        Returns the message_id on success, or None if all attempts fail.
        """
        url = f"{self._base_url}/{method}"

        for attempt in range(3):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )

                # Handle Telegram rate limiting (429)
                if response.status_code == 429:
                    retry_after = int(
                        response.json()
                        .get("parameters", {})
                        .get("retry_after", 5)
                    )
                    logger.warning(
                        "Telegram rate limit; waiting %d seconds.", retry_after
                    )
                    time.sleep(retry_after + 1)
                    continue

                data = response.json()

                if not data.get("ok"):
                    error_code = data.get("error_code")
                    description = data.get("description", "Unknown error")

                    # 400 errors (bad request) are not retryable
                    if error_code == 400:
                        logger.error(
                            "Telegram API error [%s/%d]: %s",
                            method,
                            error_code,
                            description,
                        )
                        return None

                    logger.warning(
                        "Telegram API error [%s/%s] (attempt %d/3): %s",
                        method,
                        error_code,
                        attempt + 1,
                        description,
                    )
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    return None

                message_id: int = data["result"]["message_id"]
                logger.info(
                    "Telegram %s succeeded — message_id=%d", method, message_id
                )
                return message_id

            except requests.exceptions.Timeout:
                logger.warning(
                    "Telegram request timed out (attempt %d/3): %s",
                    attempt + 1,
                    method,
                )
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue

            except requests.exceptions.RequestException as exc:
                logger.warning(
                    "Telegram request error (attempt %d/3): %s", attempt + 1, exc
                )
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue

        logger.error("Telegram %s failed after 3 attempts.", method)
        return None
