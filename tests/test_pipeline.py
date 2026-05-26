"""
Tests for the MeetingPipeline orchestrator.
All external calls are mocked.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.schemas import MeetingStatus, PipelineStep


@pytest.mark.asyncio
async def test_pipeline_extraction_only(sample_request, mock_analysis):
    """Pipeline with integrations disabled returns analysis only."""
    with patch("app.services.pipeline.ExtractionAgent") as MockAgent:
        instance = MockAgent.return_value
        instance.extract = AsyncMock(return_value=mock_analysis)
        # emails/jira/slack are all False in sample_request

        from app.services.pipeline import MeetingPipeline
        pipeline = MeetingPipeline()
        result = await pipeline.run(sample_request)

    assert result.status == MeetingStatus.COMPLETED
    assert result.analysis is not None
    assert any(r.step == PipelineStep.AI_EXTRACTION_DONE for r in result.pipeline_results)
    # No Jira/email/slack results since disabled
    step_names = {r.step for r in result.pipeline_results}
    assert PipelineStep.JIRA_TICKETS_CREATED not in step_names
    assert PipelineStep.EMAILS_SENT not in step_names


@pytest.mark.asyncio
async def test_pipeline_extraction_failure_returns_failed(sample_request):
    """If AI extraction fails, pipeline should return FAILED status."""
    with patch("app.services.pipeline.ExtractionAgent") as MockAgent:
        instance = MockAgent.return_value
        instance.extract = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        from app.services.pipeline import MeetingPipeline
        pipeline = MeetingPipeline()
        result = await pipeline.run(sample_request)

    assert result.status == MeetingStatus.FAILED
    assert len(result.errors) > 0
    assert "extraction failed" in result.errors[0].lower()


@pytest.mark.asyncio
async def test_pipeline_jira_failure_doesnt_abort(sample_request, mock_analysis):
    """Jira step errors must be captured; analysis must still appear in result."""
    sample_request.create_jira_tickets = True
    sample_request.send_emails = False
    sample_request.post_to_slack = False

    # Build a fake jira_client module so the lazy import inside pipeline works
    # without needing the real `jira` package installed.
    fake_jira_module = MagicMock()
    fake_jira_client_instance = MagicMock()
    fake_jira_client_instance.create_tickets_bulk.side_effect = RuntimeError("Jira unreachable")
    fake_jira_module.JiraClient.return_value = fake_jira_client_instance

    mock_settings = MagicMock()
    mock_settings.jira_enabled = True
    mock_settings.email_enabled = False
    mock_settings.slack_enabled = False

    import sys
    # Inject fake module BEFORE importing pipeline so the lazy import sees it
    sys.modules.setdefault("jira", MagicMock())          # satisfy top-level import in jira_client
    sys.modules["app.integrations.jira_client"] = fake_jira_module

    try:
        with patch("app.services.pipeline.ExtractionAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.extract = AsyncMock(return_value=mock_analysis)

            from app.services.pipeline import MeetingPipeline
            pipeline = MeetingPipeline()
            pipeline._settings = mock_settings   # override instance attribute directly
            result = await pipeline.run(sample_request)
    finally:
        # Clean up injected modules so they don't bleed into other tests
        sys.modules.pop("app.integrations.jira_client", None)

    assert result.analysis is not None
    # Jira step result should exist and be a failure
    jira_steps = [r for r in result.pipeline_results if "jira" in r.step.value]
    assert jira_steps, "Expected a Jira pipeline step result"
    assert not jira_steps[0].success
