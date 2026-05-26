"""
Shared pytest fixtures for MeetingMind tests.
"""
from __future__ import annotations

import os
import pytest
import pytest_asyncio
from pathlib import Path

# Force test environment before importing app modules
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")
os.environ.setdefault("SECRET_KEY", "test-secret")


from app.models.schemas import Attendee, MeetingIngestRequest, TranscriptSource


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_transcript() -> str:
    return (FIXTURE_DIR / "sample_transcript.txt").read_text()


@pytest.fixture
def sample_attendees() -> list[Attendee]:
    return [
        Attendee(name="Alice Chen", email="alice@company.com", role="Product Manager", is_organizer=True),
        Attendee(name="Bob Smith", email="bob@company.com", role="Tech Lead"),
        Attendee(name="David Park", email="david@company.com", role="Backend Engineer"),
        Attendee(name="Priya Nair", email="priya@company.com", role="Mobile Engineer"),
    ]


@pytest.fixture
def sample_request(sample_transcript, sample_attendees) -> MeetingIngestRequest:
    return MeetingIngestRequest(
        title="Sprint 42 Planning",
        transcript=sample_transcript,
        attendees=sample_attendees,
        source=TranscriptSource.MANUAL,
        send_emails=False,          # disable live integrations in tests
        create_jira_tickets=False,
        post_to_slack=False,
    )


@pytest.fixture
def mock_analysis():
    """Pre-built MeetingAnalysis fixture for unit tests that skip LLM calls."""
    from app.models.schemas import (
        ActionItem, BlockerItem, Decision, MeetingAnalysis, Priority
    )
    return MeetingAnalysis(
        summary="Sprint 42 planning covered auth refactor carry-overs, push notifications, and Datadog setup.",
        key_topics=["auth refactor", "push notifications", "Datadog", "Node.js upgrade"],
        action_items=[
            ActionItem(
                id="ai-1",
                title="Finish authentication service refactor",
                description="Complete after receiving Carol's design mockups",
                owner="Bob Smith",
                owner_email="bob@company.com",
                deadline="2026-05-30",
                priority=Priority.HIGH,
                context="auth refactor is still pending",
            ),
            ActionItem(
                id="ai-2",
                title="Write Stripe API requirements document",
                description="Document what keys and permissions are needed from Finance",
                owner="David Park",
                owner_email="david@company.com",
                deadline="2026-05-25",
                priority=Priority.CRITICAL,
                context="I'll put together a requirements doc by end of day today",
            ),
            ActionItem(
                id="ai-3",
                title="Escalate Stripe API keys request to Finance",
                owner="Alice Chen",
                owner_email="alice@company.com",
                deadline="2026-05-27",
                priority=Priority.CRITICAL,
                context="I'll escalate to Finance today to get those keys",
            ),
        ],
        blockers=[
            BlockerItem(
                id="b-1",
                description="Carol's design mockups not finalized — blocking auth refactor",
                blocking_owner="Bob Smith",
                blocker_owner="Carol",
            ),
            BlockerItem(
                id="b-2",
                description="Stripe API keys not received from Finance",
                blocking_owner="David Park",
                blocker_owner="Finance team",
            ),
        ],
        decisions=[
            Decision(
                description="Push notifications feature goes into Sprint 42",
                decided_by="Alice Chen",
            ),
            Decision(
                description="Datadog monitoring setup is this sprint",
                decided_by="group consensus",
            ),
        ],
        follow_up_topics=["Search service performance investigation results"],
        estimated_next_meeting="June 8, 2026",
    )
