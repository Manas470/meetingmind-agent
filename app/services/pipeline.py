"""
MeetingPipeline — the master orchestrator.

Chains: Transcript → AI Extraction → Jira → Gmail → Slack
Each step is isolated; failures are captured without stopping the next step.
Results are persisted to the database and returned to the caller.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

import structlog

from app.agents.extraction_agent import ExtractionAgent
from app.config import get_settings
from app.models.schemas import (
    Attendee,
    MeetingAnalysis,
    MeetingIngestRequest,
    MeetingProcessResponse,
    MeetingStatus,
    PipelineStep,
    PipelineStepResult,
    TranscriptSource,
)

logger = structlog.get_logger(__name__)


class MeetingPipeline:
    """
    Orchestrates the full meeting-to-execution pipeline.
    Each run gets a unique meeting_id. Steps are executed sequentially;
    any step failure is logged and captured but does not abort remaining steps.
    """

    def __init__(self) -> None:
        self._agent = ExtractionAgent()
        self._settings = get_settings()

    async def run(
        self,
        request: MeetingIngestRequest,
        meeting_id: str | None = None,
    ) -> MeetingProcessResponse:
        """Execute the full pipeline and return a structured result."""
        meeting_id = meeting_id or str(uuid.uuid4())
        log = logger.bind(meeting_id=meeting_id, title=request.title)
        log.info("pipeline.start")

        response = MeetingProcessResponse(
            meeting_id=meeting_id,
            status=MeetingStatus.PROCESSING,
        )

        # ── Step 1: AI Extraction ─────────────────────────────────────────────
        analysis: MeetingAnalysis | None = None
        try:
            analysis = await self._agent.extract(
                transcript=request.transcript,
                meeting_title=request.title,
                attendees=request.attendees,
                meeting_date=request.meeting_date,
            )
            response.analysis = analysis
            response.pipeline_results.append(PipelineStepResult(
                step=PipelineStep.AI_EXTRACTION_DONE,
                success=True,
                detail=f"Extracted {len(analysis.action_items)} action items, {len(analysis.blockers)} blockers",
            ))
            log.info("pipeline.extraction_done", action_items=len(analysis.action_items))
        except Exception as e:
            log.error("pipeline.extraction_failed", error=str(e))
            response.errors.append(f"AI extraction failed: {e}")
            response.status = MeetingStatus.FAILED
            return response

        # ── Step 2: Jira ticket creation ──────────────────────────────────────
        if request.create_jira_tickets and self._settings.jira_enabled:
            try:
                from app.integrations.jira_client import JiraClient
                jira = JiraClient()
                ticket_results = jira.create_tickets_bulk(
                    action_items=analysis.action_items,
                    project_key=request.jira_project_key,
                    meeting_title=request.title,
                )
                successful = [r for r in ticket_results if r.success]
                failed = [r for r in ticket_results if not r.success]
                ticket_keys = [r.ticket_key for r in successful]

                response.pipeline_results.append(PipelineStepResult(
                    step=PipelineStep.JIRA_TICKETS_CREATED,
                    success=len(failed) == 0,
                    detail=f"Created {len(successful)}/{len(ticket_results)} tickets",
                    artifacts={"jira_keys": ticket_keys},
                ))
                if failed:
                    response.errors.extend([f"Jira failed for item: {r.error}" for r in failed])
                log.info("pipeline.jira_done", created=len(successful), failed=len(failed))
            except Exception as e:
                log.error("pipeline.jira_error", error=str(e))
                response.errors.append(f"Jira step error: {e}")
                response.pipeline_results.append(PipelineStepResult(
                    step=PipelineStep.JIRA_TICKETS_CREATED,
                    success=False,
                    detail=str(e),
                ))
        elif request.create_jira_tickets:
            log.info("pipeline.jira_skipped", reason="not_configured")

        # ── Step 3: Gmail follow-up emails ────────────────────────────────────
        if request.send_emails and self._settings.email_enabled:
            try:
                from app.integrations.gmail_client import GmailClient
                gmail = GmailClient()
                recipients_with_email = [a for a in request.attendees if a.email]
                email_payloads = []

                for attendee in recipients_with_email:
                    subject, body = await self._agent.generate_followup_email(
                        recipient=attendee,
                        analysis=analysis,
                        meeting_title=request.title,
                        meeting_date=request.meeting_date,
                    )
                    email_payloads.append((attendee.email, attendee.name, subject, body))

                results = await gmail.send_bulk(email_payloads)
                sent = sum(1 for r in results if r.success)
                failed = [r for r in results if not r.success]

                response.pipeline_results.append(PipelineStepResult(
                    step=PipelineStep.EMAILS_SENT,
                    success=len(failed) == 0,
                    detail=f"Sent {sent}/{len(results)} emails",
                    artifacts={"sent_to": [r.recipient_email for r in results if r.success]},
                ))
                if failed:
                    response.errors.extend([f"Email failed to {r.recipient_email}: {r.error}" for r in failed])
                log.info("pipeline.emails_done", sent=sent)
            except Exception as e:
                log.error("pipeline.email_error", error=str(e))
                response.errors.append(f"Email step error: {e}")
                response.pipeline_results.append(PipelineStepResult(
                    step=PipelineStep.EMAILS_SENT,
                    success=False,
                    detail=str(e),
                ))
        elif request.send_emails:
            log.info("pipeline.email_skipped", reason="not_configured")

        # ── Step 4: Slack summary post ────────────────────────────────────────
        if request.post_to_slack and self._settings.slack_enabled:
            try:
                from app.integrations.slack_client import SlackClient
                slack = SlackClient()
                success = await slack.post_meeting_summary(
                    analysis=analysis,
                    meeting_title=request.title,
                    meeting_date=request.meeting_date,
                    channel=request.slack_channel,
                )
                response.pipeline_results.append(PipelineStepResult(
                    step=PipelineStep.SLACK_POSTED,
                    success=success,
                    detail="Posted to Slack" if success else "Slack post failed",
                ))
                log.info("pipeline.slack_done", success=success)
            except Exception as e:
                log.error("pipeline.slack_error", error=str(e))
                response.errors.append(f"Slack step error: {e}")
                response.pipeline_results.append(PipelineStepResult(
                    step=PipelineStep.SLACK_POSTED,
                    success=False,
                    detail=str(e),
                ))
        elif request.post_to_slack:
            log.info("pipeline.slack_skipped", reason="not_configured")

        # ── Finalize ──────────────────────────────────────────────────────────
        response.status = (
            MeetingStatus.COMPLETED if not response.errors else MeetingStatus.COMPLETED
            # Still COMPLETED even with partial failures — pipeline ran
        )
        log.info(
            "pipeline.complete",
            steps=len(response.pipeline_results),
            errors=len(response.errors),
        )
        return response
