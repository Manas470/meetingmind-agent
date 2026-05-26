"""Tests for Pydantic models and schema validation."""
import pytest
from app.models.schemas import (
    ActionItem,
    Attendee,
    MeetingAnalysis,
    MeetingIngestRequest,
    Priority,
    TranscriptSource,
)


def test_meeting_ingest_request_valid(sample_transcript, sample_attendees):
    req = MeetingIngestRequest(
        title="Test Meeting",
        transcript=sample_transcript,
        attendees=sample_attendees,
    )
    assert req.title == "Test Meeting"
    assert len(req.attendees) == 4
    assert req.source == TranscriptSource.MANUAL
    assert req.send_emails is True  # default


def test_meeting_ingest_request_too_short():
    with pytest.raises(Exception):
        MeetingIngestRequest(
            title="Test",
            transcript="short",  # < 50 chars
        )


def test_action_item_defaults():
    ai = ActionItem(title="Do something", description="Details here")
    assert ai.priority == Priority.MEDIUM
    assert ai.id  # auto-generated UUID


def test_priority_enum():
    assert Priority.CRITICAL.value == "critical"
    assert Priority("high") == Priority.HIGH


def test_meeting_analysis_serialization(mock_analysis):
    """MeetingAnalysis should round-trip through JSON."""
    json_str = mock_analysis.model_dump_json()
    restored = MeetingAnalysis.model_validate_json(json_str)
    assert restored.summary == mock_analysis.summary
    assert len(restored.action_items) == len(mock_analysis.action_items)
    assert len(restored.blockers) == len(mock_analysis.blockers)


def test_attendee_organizer_default():
    a = Attendee(name="Alice", email="alice@co.com")
    assert a.is_organizer is False
