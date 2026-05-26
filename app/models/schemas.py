"""
Pydantic schemas shared across the entire MeetingMind pipeline.
These are the canonical data shapes — from ingest to final output.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, EmailStr


# ── Enums ─────────────────────────────────────────────────────────────────────

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MeetingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineStep(str, Enum):
    TRANSCRIPT_INGESTED = "transcript_ingested"
    AI_EXTRACTION_DONE = "ai_extraction_done"
    JIRA_TICKETS_CREATED = "jira_tickets_created"
    EMAILS_SENT = "emails_sent"
    SLACK_POSTED = "slack_posted"


# ── Core domain models ────────────────────────────────────────────────────────

class Attendee(BaseModel):
    name: str
    email: Optional[str] = None
    role: Optional[str] = None          # e.g., "Product Manager", "Tech Lead"
    is_organizer: bool = False


class ActionItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str = ""
    owner: Optional[str] = None         # name (matched to attendees)
    owner_email: Optional[str] = None   # resolved from attendees list
    deadline: Optional[str] = None      # ISO date string or natural language
    priority: Priority = Priority.MEDIUM
    context: str = ""                   # quote from transcript that originated this
    jira_ticket_key: Optional[str] = None  # filled after Jira creation


class BlockerItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    description: str
    blocking_owner: Optional[str] = None  # who is blocked
    blocker_owner: Optional[str] = None   # who is causing the block
    action_item_id: Optional[str] = None  # linked action item


class Decision(BaseModel):
    description: str
    rationale: Optional[str] = None
    decided_by: Optional[str] = None


# ── AI extraction output ──────────────────────────────────────────────────────

class MeetingAnalysis(BaseModel):
    """Structured output from the AI extraction agent."""
    summary: str                                = Field(description="2-3 sentence executive summary of the meeting")
    key_topics: list[str]                       = Field(default_factory=list)
    action_items: list[ActionItem]              = Field(default_factory=list)
    blockers: list[BlockerItem]                 = Field(default_factory=list)
    decisions: list[Decision]                   = Field(default_factory=list)
    follow_up_topics: list[str]                 = Field(default_factory=list, description="Topics to revisit in next meeting")
    estimated_next_meeting: Optional[str]       = None


# ── Meeting request / response ────────────────────────────────────────────────

class TranscriptSource(str, Enum):
    MANUAL = "manual"
    ZOOM = "zoom"
    GOOGLE_MEET = "google_meet"
    FILE_UPLOAD = "file_upload"


class MeetingIngestRequest(BaseModel):
    title: str
    transcript: str                             = Field(..., min_length=50, description="Raw meeting transcript text")
    attendees: list[Attendee]                   = Field(default_factory=list)
    source: TranscriptSource                    = TranscriptSource.MANUAL
    meeting_date: Optional[datetime]            = None
    duration_minutes: Optional[int]             = None
    jira_project_key: Optional[str]             = None  # override default
    slack_channel: Optional[str]                = None  # override default channel
    send_emails: bool                           = True
    create_jira_tickets: bool                   = True
    post_to_slack: bool                         = True

    class Config:
        json_schema_extra = {
            "example": {
                "title": "Sprint 42 Planning",
                "transcript": "Alice: Let's start the sprint planning...",
                "attendees": [
                    {"name": "Alice Chen", "email": "alice@company.com", "role": "Product Manager", "is_organizer": True},
                    {"name": "Bob Smith", "email": "bob@company.com", "role": "Tech Lead"},
                ],
                "source": "manual",
                "send_emails": True,
                "create_jira_tickets": True,
                "post_to_slack": True,
            }
        }


class PipelineStepResult(BaseModel):
    step: PipelineStep
    success: bool
    detail: str = ""
    artifacts: dict = Field(default_factory=dict)   # e.g. {"jira_keys": ["ENG-123"]}


class MeetingProcessResponse(BaseModel):
    meeting_id: str
    status: MeetingStatus
    analysis: Optional[MeetingAnalysis]     = None
    pipeline_results: list[PipelineStepResult] = Field(default_factory=list)
    errors: list[str]                       = Field(default_factory=list)
    created_at: datetime                    = Field(default_factory=datetime.utcnow)


# ── Webhook payloads ──────────────────────────────────────────────────────────

class ZoomWebhookPayload(BaseModel):
    event: str
    payload: dict


class MeetWebhookPayload(BaseModel):
    meeting_id: str
    recording_ready: bool = False
    transcript_uri: Optional[str] = None


# ── Jira output ───────────────────────────────────────────────────────────────

class JiraTicketResult(BaseModel):
    action_item_id: str
    ticket_key: str
    ticket_url: str
    success: bool
    error: Optional[str] = None
