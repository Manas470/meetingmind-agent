"""
Central configuration — loaded once at startup, injected via dependency.
All values come from environment variables (or .env file in dev).
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: Literal["development", "production", "test"] = "development"
    secret_key: str = "dev-secret-change-in-prod"
    log_level: str = "INFO"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    claude_model: str = "claude-sonnet-4-6"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = ""          # empty → SQLite fallback in get_db_url()

    def get_db_url(self) -> str:
        if self.database_url:
            return self.database_url
        # Dev / test SQLite
        db_name = "meetingmind_test.db" if self.app_env == "test" else "meetingmind.db"
        return f"sqlite+aiosqlite:///{db_name}"

    # ── Jira ──────────────────────────────────────────────────────────────────
    jira_server_url: str = ""
    jira_user_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "ENG"
    jira_issue_type: str = "Task"

    @property
    def jira_enabled(self) -> bool:
        return bool(self.jira_server_url and self.jira_api_token)

    # ── Gmail / SMTP ──────────────────────────────────────────────────────────
    gmail_service_account_json: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from_name: str = "MeetingMind"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password)

    # ── Slack ─────────────────────────────────────────────────────────────────
    slack_bot_token: str = ""
    slack_default_channel: str = "#meeting-summaries"

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_bot_token)

    # ── Zoom ──────────────────────────────────────────────────────────────────
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""
    zoom_webhook_secret_token: str = ""

    @property
    def zoom_enabled(self) -> bool:
        return bool(self.zoom_account_id and self.zoom_client_id)

    # ── Google Meet ───────────────────────────────────────────────────────────
    google_cloud_project: str = ""
    google_workspace_admin_email: str = ""

    @property
    def meet_enabled(self) -> bool:
        return bool(self.google_cloud_project)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper


@lru_cache
def get_settings() -> Settings:
    return Settings()
