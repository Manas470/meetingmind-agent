"""
Webhook endpoints for Zoom and Google Meet.

POST /webhooks/zoom      — Zoom event webhook (recording.completed → fetch transcript)
POST /webhooks/meet      — Google Meet notification webhook
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.config import get_settings
from app.models.schemas import (
    Attendee,
    MeetingIngestRequest,
    TranscriptSource,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)


# ── Zoom webhook ──────────────────────────────────────────────────────────────

@router.post("/zoom", summary="Zoom recording webhook")
async def zoom_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_zm_signature: str = Header(default=""),
    x_zm_request_timestamp: str = Header(default=""),
) -> dict:
    settings = get_settings()
    body = await request.json()

    event = body.get("event", "")
    logger.info("zoom.webhook_received", zoom_event=event)

    # Handle Zoom URL validation challenge
    if event == "endpoint.url_validation":
        if not settings.zoom_enabled:
            raise HTTPException(status_code=503, detail="Zoom not configured")
        from app.integrations.zoom_client import ZoomClient
        zoom = ZoomClient()
        plain_token = body.get("payload", {}).get("plainToken", "")
        encrypted = zoom.get_challenge_response(plain_token)
        return {"plainToken": plain_token, "encryptedToken": encrypted}

    # Validate webhook signature
    if settings.zoom_enabled and settings.zoom_webhook_secret_token:
        from app.integrations.zoom_client import ZoomClient
        zoom = ZoomClient()
        if not zoom.validate_webhook(body, x_zm_request_timestamp, x_zm_signature):
            raise HTTPException(status_code=401, detail="Invalid Zoom webhook signature")

    # Handle recording completed
    if event == "recording.completed":
        meeting_payload = body.get("payload", {}).get("object", {})
        meeting_id = meeting_payload.get("id", "")
        meeting_title = meeting_payload.get("topic", "Zoom Meeting")
        host_email = meeting_payload.get("host_email", "")

        logger.info("zoom.recording_completed", meeting_id=meeting_id, title=meeting_title)
        background_tasks.add_task(
            _process_zoom_recording,
            zoom_meeting_id=meeting_id,
            meeting_title=meeting_title,
            host_email=host_email,
        )
        return {"status": "accepted", "meeting_id": meeting_id}

    return {"status": "ignored", "zoom_event": event}


# ── Google Meet webhook ───────────────────────────────────────────────────────

@router.post("/meet", summary="Google Meet recording webhook")
async def meet_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    body = await request.json()
    space_id = body.get("space_id") or body.get("meeting_id", "")
    title = body.get("title", "Google Meet")
    logger.info("meet.webhook_received", space_id=space_id)

    if space_id:
        background_tasks.add_task(
            _process_meet_recording,
            space_id=space_id,
            meeting_title=title,
        )
        return {"status": "accepted", "space_id": space_id}

    return {"status": "ignored"}


# ── Background task helpers ───────────────────────────────────────────────────

async def _process_zoom_recording(
    zoom_meeting_id: str,
    meeting_title: str,
    host_email: str,
) -> None:
    settings = get_settings()
    if not settings.zoom_enabled:
        logger.warning("zoom.not_configured")
        return

    from app.integrations.zoom_client import ZoomClient
    zoom = ZoomClient()
    transcript = await zoom.fetch_transcript(zoom_meeting_id)
    if not transcript:
        logger.warning("zoom.transcript_unavailable", meeting_id=zoom_meeting_id)
        return

    attendees = []
    if host_email:
        attendees.append(Attendee(name="Host", email=host_email, is_organizer=True))

    request = MeetingIngestRequest(
        title=meeting_title,
        transcript=transcript,
        attendees=attendees,
        source=TranscriptSource.ZOOM,
    )
    from app.services.pipeline import MeetingPipeline
    pipeline = MeetingPipeline()
    await pipeline.run(request)


async def _process_meet_recording(space_id: str, meeting_title: str) -> None:
    settings = get_settings()
    if not settings.meet_enabled:
        logger.warning("meet.not_configured")
        return

    from app.integrations.meet_client import MeetClient
    meet = MeetClient()
    transcript = await meet.fetch_transcript(space_id)
    if not transcript:
        logger.warning("meet.transcript_unavailable", space_id=space_id)
        return

    request = MeetingIngestRequest(
        title=meeting_title,
        transcript=transcript,
        source=TranscriptSource.GOOGLE_MEET,
    )
    from app.services.pipeline import MeetingPipeline
    pipeline = MeetingPipeline()
    await pipeline.run(request)
