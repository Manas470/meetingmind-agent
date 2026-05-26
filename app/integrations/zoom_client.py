"""
Zoom integration — OAuth2 server-to-server auth + transcript fetching.
Handles Zoom webhook validation and fetches cloud recording transcripts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

logger = structlog.get_logger(__name__)

_ZOOM_API_BASE = "https://api.zoom.us/v2"
_ZOOM_OAUTH_URL = "https://zoom.us/oauth/token"


class ZoomClient:
    """
    Zoom Server-to-Server OAuth client for fetching meeting transcripts.
    Token is cached and auto-refreshed.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.zoom_enabled:
            raise RuntimeError("Zoom is not configured. Set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET.")
        self._account_id = settings.zoom_account_id
        self._client_id = settings.zoom_client_id
        self._client_secret = settings.zoom_client_secret
        self._webhook_secret = settings.zoom_webhook_secret_token
        self._access_token: str | None = None
        self._token_expiry: float = 0

    # ── Webhook validation ─────────────────────────────────────────────────────

    def validate_webhook(self, payload: dict[str, Any], timestamp: str, signature: str) -> bool:
        """
        Validate incoming Zoom webhook using HMAC-SHA256.
        https://developers.zoom.us/docs/api/rest/webhook-reference/
        """
        if not self._webhook_secret:
            return True  # skip validation in dev if not set
        message = f"v0:{timestamp}:{json.dumps(payload, separators=(',', ':'))}"
        expected = "v0=" + hmac.new(
            self._webhook_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def get_challenge_response(self, plain_token: str) -> str:
        """Return HMAC response for Zoom webhook URL validation challenge."""
        return hmac.new(
            self._webhook_secret.encode(),
            plain_token.encode(),
            hashlib.sha256,
        ).hexdigest()

    # ── Transcript fetching ───────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_transcript(self, meeting_id: str) -> str | None:
        """
        Fetch the VTT transcript for a completed Zoom meeting.
        Returns the plain-text transcript or None if unavailable.
        """
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=30) as client:
            # Get cloud recordings for the meeting
            resp = await client.get(
                f"{_ZOOM_API_BASE}/meetings/{meeting_id}/recordings",
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning("zoom.recordings_fetch_failed", status=resp.status_code, meeting_id=meeting_id)
                return None

            recordings = resp.json()
            transcript_file = next(
                (f for f in recordings.get("recording_files", []) if f.get("file_type") == "TRANSCRIPT"),
                None,
            )
            if not transcript_file:
                logger.info("zoom.no_transcript", meeting_id=meeting_id)
                return None

            download_url = transcript_file["download_url"]
            vtt_resp = await client.get(download_url, headers=headers)
            if vtt_resp.status_code != 200:
                logger.warning("zoom.transcript_download_failed", status=vtt_resp.status_code)
                return None

            return _parse_vtt(vtt_resp.text)

    # ── OAuth token management ────────────────────────────────────────────────

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _ZOOM_OAUTH_URL,
                params={"grant_type": "account_credentials", "account_id": self._account_id},
                auth=(self._client_id, self._client_secret),
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expiry = time.time() + data.get("expires_in", 3600)
            return self._access_token


# ── VTT parser ────────────────────────────────────────────────────────────────

def _parse_vtt(vtt_content: str) -> str:
    """Convert WebVTT transcript to clean plain text (strips timestamps/cue IDs)."""
    lines = []
    skip = True  # skip WEBVTT header
    for line in vtt_content.splitlines():
        line = line.strip()
        if skip:
            if line == "":
                skip = False
            continue
        # skip timestamp lines (e.g. 00:00:01.000 --> 00:00:04.500)
        if "-->" in line:
            continue
        # skip cue identifiers (pure numbers)
        if line.isdigit():
            continue
        if line:
            lines.append(line)
    return "\n".join(lines)
