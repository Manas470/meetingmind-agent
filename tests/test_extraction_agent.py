"""
Tests for the AI ExtractionAgent.
LLM calls are mocked so tests run without an API key.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.extraction_agent import ExtractionAgent, _format_attendees, _format_action_items
from app.models.schemas import Attendee, MeetingAnalysis, Priority


MOCK_LLM_RESPONSE = {
    "summary": "Sprint 42 planning covered auth carry-overs and new features.",
    "key_topics": ["auth refactor", "push notifications", "Datadog"],
    "action_items": [
        {
            "title": "Finish authentication service refactor",
            "description": "Complete after design mockups arrive",
            "owner": "Bob Smith",
            "deadline": "2026-05-30",
            "priority": "high",
            "context": "auth refactor is still pending",
        },
        {
            "title": "Write Stripe requirements document",
            "description": "Document what keys Finance needs to provide",
            "owner": "David Park",
            "deadline": "2026-05-25",
            "priority": "critical",
            "context": "I'll put together a requirements doc by end of day",
        },
    ],
    "blockers": [
        {
            "description": "Carol's mockups blocking auth refactor",
            "blocking_owner": "Bob Smith",
            "blocker_owner": "Carol",
        }
    ],
    "decisions": [
        {
            "description": "Push notifications go into Sprint 42",
            "rationale": "Feature is scoped and team is ready",
            "decided_by": "Alice Chen",
        }
    ],
    "follow_up_topics": ["Search service perf review"],
    "estimated_next_meeting": "2026-06-08",
}


@pytest.fixture
def mock_llm_response():
    """A mock LangChain message response with JSON content."""
    msg = MagicMock()
    msg.content = json.dumps(MOCK_LLM_RESPONSE)
    return msg


@pytest.mark.asyncio
async def test_extract_returns_meeting_analysis(
    sample_transcript, sample_attendees, mock_llm_response
):
    with patch("app.agents.extraction_agent.ChatAnthropic") as MockLLM:
        instance = MockLLM.return_value
        instance.ainvoke = AsyncMock(return_value=mock_llm_response)

        agent = ExtractionAgent()
        result = await agent.extract(
            transcript=sample_transcript,
            meeting_title="Sprint 42 Planning",
            attendees=sample_attendees,
        )

    assert isinstance(result, MeetingAnalysis)
    assert len(result.action_items) == 2
    assert len(result.blockers) == 1
    assert len(result.decisions) == 1
    assert result.summary != ""


@pytest.mark.asyncio
async def test_extract_resolves_owner_email(
    sample_transcript, sample_attendees, mock_llm_response
):
    """Owner emails should be resolved from the attendees list."""
    with patch("app.agents.extraction_agent.ChatAnthropic") as MockLLM:
        instance = MockLLM.return_value
        instance.ainvoke = AsyncMock(return_value=mock_llm_response)

        agent = ExtractionAgent()
        result = await agent.extract(
            transcript=sample_transcript,
            meeting_title="Sprint 42 Planning",
            attendees=sample_attendees,
        )

    bob_item = next(ai for ai in result.action_items if "Bob" in (ai.owner or ""))
    assert bob_item.owner_email == "bob@company.com"

    david_item = next(ai for ai in result.action_items if "David" in (ai.owner or ""))
    assert david_item.owner_email == "david@company.com"


@pytest.mark.asyncio
async def test_extract_handles_invalid_priority(sample_attendees, mock_llm_response):
    """Unknown priority strings should fall back to MEDIUM."""
    bad_response = dict(MOCK_LLM_RESPONSE)
    bad_response["action_items"] = [
        {**MOCK_LLM_RESPONSE["action_items"][0], "priority": "SUPER_URGENT"}
    ]
    mock_llm_response.content = json.dumps(bad_response)

    with patch("app.agents.extraction_agent.ChatAnthropic") as MockLLM:
        instance = MockLLM.return_value
        instance.ainvoke = AsyncMock(return_value=mock_llm_response)

        agent = ExtractionAgent()
        result = await agent.extract(
            transcript="This is a sample transcript with enough content to pass validation.",
            meeting_title="Test",
            attendees=sample_attendees,
        )

    assert result.action_items[0].priority == Priority.MEDIUM


@pytest.mark.asyncio
async def test_generate_followup_email(mock_analysis, sample_attendees):
    """Followup email should address the specific attendee and mention their items."""
    email_msg = MagicMock()
    email_msg.content = "Hi Bob,\n\nThank you for attending Sprint 42 Planning.\n\nYour action items:\n1. Finish auth refactor — due 2026-05-30"

    with patch("app.agents.extraction_agent.ChatAnthropic") as MockLLM:
        instance = MockLLM.return_value
        instance.ainvoke = AsyncMock(return_value=email_msg)

        agent = ExtractionAgent()
        bob = next(a for a in sample_attendees if a.name == "Bob Smith")
        subject, body = await agent.generate_followup_email(
            recipient=bob,
            analysis=mock_analysis,
            meeting_title="Sprint 42 Planning",
        )

    assert "Sprint 42 Planning" in subject
    assert len(body) > 10


def test_format_attendees(sample_attendees):
    result = _format_attendees(sample_attendees)
    assert "Alice Chen" in result
    assert "Product Manager" in result
    assert "alice@company.com" in result
    assert "[organizer]" in result


def test_format_action_items(mock_analysis):
    result = _format_action_items(mock_analysis.action_items)
    assert "Finish authentication service refactor" in result
    assert "HIGH" in result or "CRITICAL" in result
