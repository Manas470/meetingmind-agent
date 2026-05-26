"""
Jira integration — creates tickets from extracted action items.
Uses the atlassian-python-api (jira) library with Basic Auth.
"""
from __future__ import annotations

import structlog
from jira import JIRA, JIRAError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.schemas import ActionItem, JiraTicketResult, Priority

logger = structlog.get_logger(__name__)

# Priority mapping: MeetingMind → Jira priority name
_PRIORITY_MAP: dict[Priority, str] = {
    Priority.CRITICAL: "Highest",
    Priority.HIGH: "High",
    Priority.MEDIUM: "Medium",
    Priority.LOW: "Low",
}


class JiraClient:
    """
    Thin wrapper around the Jira Python client.
    Thread-safe; creates one authenticated session per instance.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.jira_enabled:
            raise RuntimeError(
                "Jira is not configured. Set JIRA_SERVER_URL, JIRA_USER_EMAIL, JIRA_API_TOKEN."
            )
        self._client = JIRA(
            server=settings.jira_server_url,
            basic_auth=(settings.jira_user_email, settings.jira_api_token),
            options={"verify": True},
        )
        self._default_project = settings.jira_project_key
        self._default_issue_type = settings.jira_issue_type
        self._server_url = settings.jira_server_url.rstrip("/")

    # ── Public API ────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
    def create_ticket(
        self,
        action_item: ActionItem,
        project_key: str | None = None,
        meeting_title: str = "",
    ) -> JiraTicketResult:
        """Create a single Jira ticket from an ActionItem."""
        project = project_key or self._default_project
        priority_name = _PRIORITY_MAP.get(action_item.priority, "Medium")

        description = self._build_description(action_item, meeting_title)

        fields: dict = {
            "project": {"key": project},
            "summary": action_item.title,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description}],
                    }
                ],
            },
            "issuetype": {"name": self._default_issue_type},
            "priority": {"name": priority_name},
        }

        # Assign to reporter if Jira user email is known
        if action_item.owner_email:
            try:
                fields["assignee"] = {"accountId": self._resolve_account_id(action_item.owner_email)}
            except Exception:
                logger.warning("jira.assignee_lookup_failed", email=action_item.owner_email)

        # Due date
        if action_item.deadline and len(action_item.deadline) == 10:  # YYYY-MM-DD
            fields["duedate"] = action_item.deadline

        try:
            issue = self._client.create_issue(fields=fields)
            ticket_url = f"{self._server_url}/browse/{issue.key}"
            logger.info("jira.ticket_created", key=issue.key, title=action_item.title)
            return JiraTicketResult(
                action_item_id=action_item.id,
                ticket_key=issue.key,
                ticket_url=ticket_url,
                success=True,
            )
        except JIRAError as e:
            logger.error("jira.ticket_failed", error=str(e), title=action_item.title)
            return JiraTicketResult(
                action_item_id=action_item.id,
                ticket_key="",
                ticket_url="",
                success=False,
                error=str(e),
            )

    def create_tickets_bulk(
        self,
        action_items: list[ActionItem],
        project_key: str | None = None,
        meeting_title: str = "",
    ) -> list[JiraTicketResult]:
        """Create tickets for all action items. Failures are soft — never raises."""
        results = []
        for ai in action_items:
            result = self.create_ticket(ai, project_key=project_key, meeting_title=meeting_title)
            if result.success:
                ai.jira_ticket_key = result.ticket_key
            results.append(result)
        return results

    # ── Private helpers ───────────────────────────────────────────────────────

    def _resolve_account_id(self, email: str) -> str:
        users = self._client.search_users(query=email)
        if users:
            return users[0].accountId
        raise ValueError(f"No Jira user found for {email}")

    def _build_description(self, ai: ActionItem, meeting_title: str) -> str:
        parts = []
        if meeting_title:
            parts.append(f"From meeting: {meeting_title}")
        if ai.description:
            parts.append(f"\n{ai.description}")
        if ai.context:
            parts.append(f'\n\nTranscript context:\n"{ai.context}"')
        if ai.owner:
            parts.append(f"\n\nOwner: {ai.owner}")
        return "\n".join(parts)
