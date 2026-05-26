"""
Google Meet integration — fetches transcripts from Google Workspace.
Uses the Google Meet REST API (v2) with service account credentials.

Note: Google Meet transcript API requires:
  - Google Workspace Business Standard or higher
  - Meet API enabled in Google Cloud Console
  - Service account with domain-wide delegation
"""
from __future__ import annotations

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    _GOOGLE_AVAILABLE = True
except ImportError:
    _GOOGLE_AVAILABLE = False

_SCOPES = [
    "https://www.googleapis.com/auth/meetings.space.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


class MeetClient:
    """
    Fetches Google Meet transcripts via the Meet API.
    Transcripts are stored in Google Drive as Docs when recording is enabled.
    """

    def __init__(self) -> None:
        if not _GOOGLE_AVAILABLE:
            raise RuntimeError("google-api-python-client is not installed.")
        settings = get_settings()
        if not settings.meet_enabled:
            raise RuntimeError(
                "Google Meet is not configured. Set GOOGLE_CLOUD_PROJECT and GMAIL_SERVICE_ACCOUNT_JSON."
            )
        self._admin_email = settings.google_workspace_admin_email
        self._service_account_json = settings.gmail_service_account_json
        self._meet_service = self._build_service("meet", "v2")
        self._drive_service = self._build_service("drive", "v3")

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch_transcript(self, space_id: str) -> str | None:
        """
        Fetch transcript for a Google Meet space (conference code).
        Returns plain text transcript or None.
        """
        try:
            # List recording artifacts in the meeting space
            artifacts = (
                self._meet_service.spaces()
                .recordings()
                .list(parent=f"spaces/{space_id}")
                .execute()
            )
            recordings = artifacts.get("recordings", [])
            if not recordings:
                logger.info("meet.no_recordings", space_id=space_id)
                return None

            # Find the transcript document in Drive
            for recording in recordings:
                transcript_doc_id = recording.get("transcriptUri", "")
                if transcript_doc_id:
                    return self._fetch_drive_doc(transcript_doc_id)

            logger.info("meet.no_transcript_doc", space_id=space_id)
            return None

        except Exception as e:
            logger.error("meet.fetch_failed", error=str(e), space_id=space_id)
            return None

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_service(self, service_name: str, version: str):
        """Build an authenticated Google API service client."""
        if self._service_account_json:
            creds = service_account.Credentials.from_service_account_file(
                self._service_account_json,
                scopes=_SCOPES,
            )
            if self._admin_email:
                creds = creds.with_subject(self._admin_email)
        else:
            raise RuntimeError("GMAIL_SERVICE_ACCOUNT_JSON must be set for Google Meet integration.")

        return build(service_name, version, credentials=creds)

    def _fetch_drive_doc(self, file_id: str) -> str | None:
        """Export a Google Doc as plain text."""
        try:
            content = (
                self._drive_service.files()
                .export(fileId=file_id, mimeType="text/plain")
                .execute()
            )
            if isinstance(content, bytes):
                return content.decode("utf-8")
            return str(content)
        except Exception as e:
            logger.error("meet.drive_export_failed", file_id=file_id, error=str(e))
            return None
