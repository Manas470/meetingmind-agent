"""
Slack integration — posts meeting summaries and action item digests to channels.
Uses the official Slack SDK (slack-sdk).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.schemas import ActionItem, BlockerItem, MeetingAnalysis

logger = structlog.get_logger(__name__)

# Priority emoji map
_PRIORITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}


class SlackClient:
    """Posts structured meeting summaries as rich Slack Block Kit messages."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.slack_enabled:
            raise RuntimeError(
                "Slack is not configured. Set SLACK_BOT_TOKEN."
            )
        self._client = WebClient(token=settings.slack_bot_token)
        self._default_channel = settings.slack_default_channel

    # ── Public API ────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6))
    async def post_meeting_summary(
        self,
        analysis: MeetingAnalysis,
        meeting_title: str,
        meeting_date: datetime | None = None,
        channel: str | None = None,
    ) -> bool:
        """Post a full meeting summary Block Kit message to Slack."""
        target_channel = channel or self._default_channel
        date_str = (meeting_date or datetime.utcnow()).strftime("%B %d, %Y")
        blocks = self._build_blocks(analysis, meeting_title, date_str)

        try:
            self._client.chat_postMessage(
                channel=target_channel,
                text=f"📋 Meeting summary: {meeting_title}",   # fallback text
                blocks=blocks,
            )
            logger.info("slack.posted", channel=target_channel, title=meeting_title)
            return True
        except SlackApiError as e:
            logger.error("slack.post_failed", error=str(e), channel=target_channel)
            return False

    # ── Block Kit builder ─────────────────────────────────────────────────────

    def _build_blocks(
        self,
        analysis: MeetingAnalysis,
        title: str,
        date_str: str,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []

        # Header
        blocks.append({
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 {title}", "emoji": True},
        })
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{date_str}*  •  {len(analysis.action_items)} action items  •  {len(analysis.blockers)} blockers"}],
        })
        blocks.append({"type": "divider"})

        # Summary
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary*\n{analysis.summary}"},
        })

        # Key Decisions
        if analysis.decisions:
            decisions_text = "\n".join(f"• {d.description}" for d in analysis.decisions)
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*✅ Decisions Made*\n{decisions_text}"},
            })

        # Action Items
        if analysis.action_items:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*🎯 Action Items*"},
            })
            for ai in analysis.action_items:
                emoji = _PRIORITY_EMOJI.get(ai.priority.value, "🟡")
                owner_str = f"  👤 {ai.owner}" if ai.owner else ""
                deadline_str = f"  📅 {ai.deadline}" if ai.deadline else ""
                jira_str = f"  🔗 {ai.jira_ticket_key}" if ai.jira_ticket_key else ""
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{emoji} *{ai.title}*{owner_str}{deadline_str}{jira_str}",
                    },
                })

        # Blockers
        if analysis.blockers:
            blocks.append({"type": "divider"})
            blockers_text = "\n".join(
                f"🚫 {b.description}" + (f"  (blocking: {b.blocking_owner})" if b.blocking_owner else "")
                for b in analysis.blockers
            )
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🚫 Blockers*\n{blockers_text}"},
            })

        # Follow-ups
        if analysis.follow_up_topics:
            blocks.append({"type": "divider"})
            followups_text = "\n".join(f"• {t}" for t in analysis.follow_up_topics)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*🔜 Follow-up Topics*\n{followups_text}"},
            })

        # Footer
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_Generated by MeetingMind AI_"}],
        })

        return blocks
