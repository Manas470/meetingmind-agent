"""
AI Extraction Agent — the intelligence core of MeetingMind.

Uses Claude (via langchain-anthropic) to parse meeting transcripts and return
fully structured MeetingAnalysis objects. Includes retry logic and validation.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, date
from typing import Any

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.agents.prompts import (
    SYSTEM_PROMPT,
    EXTRACTION_PROMPT_TEMPLATE,
    FOLLOWUP_EMAIL_TEMPLATE,
    EMAIL_SUBJECT_TEMPLATE,
)
from app.config import get_settings
from app.models.schemas import (
    ActionItem,
    Attendee,
    BlockerItem,
    Decision,
    MeetingAnalysis,
    Priority,
)

logger = structlog.get_logger(__name__)


class ExtractionAgent:
    """
    Wraps Claude calls for:
      1. Meeting transcript → MeetingAnalysis (action items, blockers, decisions…)
      2. MeetingAnalysis + attendee → personalized follow-up email body
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._llm = ChatAnthropic(
            model=settings.claude_model,
            api_key=settings.anthropic_api_key,
            max_tokens=4096,
            temperature=0,          # deterministic extraction
        )
        self._email_llm = ChatAnthropic(
            model=settings.claude_model,
            api_key=settings.anthropic_api_key,
            max_tokens=1024,
            temperature=0.3,        # slight creativity for emails
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ValueError, json.JSONDecodeError)),
    )
    async def extract(
        self,
        transcript: str,
        meeting_title: str,
        attendees: list[Attendee],
        meeting_date: datetime | None = None,
    ) -> MeetingAnalysis:
        """Parse transcript → MeetingAnalysis. Retries up to 3× on parse failure."""
        date_str = (meeting_date or datetime.utcnow()).strftime("%Y-%m-%d")
        attendees_list = _format_attendees(attendees)

        prompt = EXTRACTION_PROMPT_TEMPLATE.format(
            meeting_title=meeting_title,
            meeting_date=date_str,
            attendees_list=attendees_list,
            transcript=transcript,
        )

        logger.info("extraction_agent.extract.start", title=meeting_title)
        response = await self._llm.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        raw = response.content

        analysis = self._parse_analysis(raw, attendees)
        logger.info(
            "extraction_agent.extract.done",
            action_items=len(analysis.action_items),
            blockers=len(analysis.blockers),
        )
        return analysis

    async def generate_followup_email(
        self,
        recipient: Attendee,
        analysis: MeetingAnalysis,
        meeting_title: str,
        meeting_date: datetime | None = None,
    ) -> tuple[str, str]:
        """
        Returns (subject, body) for a personalized follow-up email to recipient.
        """
        date_str = (meeting_date or datetime.utcnow()).strftime("%B %d, %Y")

        # Split action items: theirs vs. others
        personal = [
            ai for ai in analysis.action_items
            if ai.owner and recipient.name.lower() in ai.owner.lower()
        ]
        others = [ai for ai in analysis.action_items if ai not in personal]

        relevant_blockers = [
            b for b in analysis.blockers
            if (b.blocking_owner and recipient.name.lower() in b.blocking_owner.lower())
            or (b.blocker_owner and recipient.name.lower() in b.blocker_owner.lower())
        ]

        prompt = FOLLOWUP_EMAIL_TEMPLATE.format(
            meeting_title=meeting_title,
            meeting_date=date_str,
            recipient_name=recipient.name,
            meeting_summary=analysis.summary,
            personal_action_items=_format_action_items(personal) or "None assigned to you.",
            other_action_items=_format_action_items(others) or "None.",
            decisions=_format_decisions(analysis.decisions) or "No formal decisions recorded.",
            relevant_blockers=_format_blockers(relevant_blockers) or "None.",
        )

        response = await self._email_llm.ainvoke([HumanMessage(content=prompt)])
        body = response.content.strip()
        subject = EMAIL_SUBJECT_TEMPLATE.format(
            meeting_title=meeting_title,
            meeting_date=date_str,
        )
        return subject, body

    # ── Private helpers ───────────────────────────────────────────────────────

    def _parse_analysis(self, raw: str, attendees: list[Attendee]) -> MeetingAnalysis:
        """Parse raw LLM JSON output into MeetingAnalysis, resolving owner emails."""
        # Strip markdown fences if present
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        data: dict[str, Any] = json.loads(cleaned)

        email_map = {a.name.lower(): a.email for a in attendees if a.email}

        action_items = []
        for raw_ai in data.get("action_items", []):
            owner = raw_ai.get("owner")
            owner_email = None
            if owner:
                # fuzzy match owner name to attendee emails
                for name_lower, email in email_map.items():
                    if owner.lower() in name_lower or name_lower in owner.lower():
                        owner_email = email
                        break

            priority_str = raw_ai.get("priority", "medium").lower()
            try:
                priority = Priority(priority_str)
            except ValueError:
                priority = Priority.MEDIUM

            action_items.append(ActionItem(
                id=str(uuid.uuid4()),
                title=raw_ai.get("title", ""),
                description=raw_ai.get("description", ""),
                owner=owner,
                owner_email=owner_email,
                deadline=raw_ai.get("deadline"),
                priority=priority,
                context=raw_ai.get("context", ""),
            ))

        blockers = [
            BlockerItem(
                id=str(uuid.uuid4()),
                description=b.get("description", ""),
                blocking_owner=b.get("blocking_owner"),
                blocker_owner=b.get("blocker_owner"),
            )
            for b in data.get("blockers", [])
        ]

        decisions = [
            Decision(
                description=d.get("description", ""),
                rationale=d.get("rationale"),
                decided_by=d.get("decided_by"),
            )
            for d in data.get("decisions", [])
        ]

        return MeetingAnalysis(
            summary=data.get("summary", ""),
            key_topics=data.get("key_topics", []),
            action_items=action_items,
            blockers=blockers,
            decisions=decisions,
            follow_up_topics=data.get("follow_up_topics", []),
            estimated_next_meeting=data.get("estimated_next_meeting"),
        )


# ── Formatters (prompt helpers) ───────────────────────────────────────────────

def _format_attendees(attendees: list[Attendee]) -> str:
    if not attendees:
        return "Unknown"
    lines = []
    for a in attendees:
        line = f"- {a.name}"
        if a.role:
            line += f" ({a.role})"
        if a.email:
            line += f" <{a.email}>"
        if a.is_organizer:
            line += " [organizer]"
        lines.append(line)
    return "\n".join(lines)


def _format_action_items(items: list[ActionItem]) -> str:
    if not items:
        return ""
    lines = []
    for i, ai in enumerate(items, 1):
        deadline = f" — due {ai.deadline}" if ai.deadline else ""
        lines.append(f"{i}. [{ai.priority.upper()}] {ai.title}{deadline}")
        if ai.description:
            lines.append(f"   {ai.description}")
    return "\n".join(lines)


def _format_decisions(decisions: list[Decision]) -> str:
    return "\n".join(f"• {d.description}" for d in decisions)


def _format_blockers(blockers: list[BlockerItem]) -> str:
    return "\n".join(f"• {b.description}" for b in blockers)
