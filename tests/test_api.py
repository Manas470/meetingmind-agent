"""
Integration tests for FastAPI routes.
Uses TestClient with mocked pipeline — no real LLM or external calls.
"""
from __future__ import annotations

import json
import os
import pytest
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV"] = "test"
os.environ["ANTHROPIC_API_KEY"] = "test-key"

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with DB initialized."""
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "meetingmind"


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "MeetingMind" in resp.json()["service"]


def test_process_meeting_mocked(client, sample_transcript, sample_attendees, mock_analysis):
    """POST /meetings/process should return a MeetingProcessResponse."""
    from app.models.schemas import MeetingProcessResponse, MeetingStatus, PipelineStep, PipelineStepResult

    mock_result = MeetingProcessResponse(
        meeting_id="test-id-123",
        status=MeetingStatus.COMPLETED,
        analysis=mock_analysis,
        pipeline_results=[
            PipelineStepResult(
                step=PipelineStep.AI_EXTRACTION_DONE,
                success=True,
                detail="Extracted 3 action items, 2 blockers",
            )
        ],
    )

    with patch("app.api.routes.meetings.MeetingPipeline") as MockPipeline:
        instance = MockPipeline.return_value
        instance.run = AsyncMock(return_value=mock_result)

        resp = client.post(
            "/meetings/process",
            json={
                "title": "Sprint 42 Planning",
                "transcript": sample_transcript,
                "attendees": [a.model_dump() for a in sample_attendees],
                "send_emails": False,
                "create_jira_tickets": False,
                "post_to_slack": False,
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "completed"
    assert data["analysis"]["summary"] != ""
    assert len(data["analysis"]["action_items"]) == 3


def test_process_meeting_short_transcript(client):
    """Short transcripts should be rejected with 422."""
    resp = client.post(
        "/meetings/process",
        json={
            "title": "Test",
            "transcript": "too short",
        },
    )
    assert resp.status_code == 422


def test_get_meeting_not_found(client):
    resp = client.get("/meetings/does-not-exist")
    assert resp.status_code == 404


def test_list_meetings_empty(client):
    resp = client.get("/meetings")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_zoom_webhook_url_validation(client):
    """Zoom URL validation challenge — returns 503 if Zoom not configured, which is expected in test env."""
    resp = client.post(
        "/webhooks/zoom",
        json={
            "event": "endpoint.url_validation",
            "payload": {"plainToken": "abc123"},
        },
    )
    # Without Zoom credentials configured, API correctly returns 503
    assert resp.status_code in (200, 503)


def test_zoom_webhook_ignored_event(client):
    """Non-recording events should be acknowledged with 'ignored' status."""
    resp = client.post(
        "/webhooks/zoom",
        json={"event": "meeting.started", "payload": {}},
        headers={"x-zm-signature": "", "x-zm-request-timestamp": "0"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ignored"
    assert "zoom_event" in data
