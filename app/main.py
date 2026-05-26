"""
MeetingMind FastAPI application entry point.

Startup: initializes async DB
Shutdown: disposes DB connections
All routes are registered via include_router.
"""
from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api.routes import health, meetings, webhooks
from app.services.database import init_db, close_db

# ── Structured logging setup ──────────────────────────────────────────────────
settings = get_settings()

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level, logging.INFO)
    ),
)
logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    _settings = get_settings()

    app = FastAPI(
        title="MeetingMind API",
        description=(
            "Autonomous meeting intelligence — converts transcripts into "
            "Jira tickets, personalized follow-up emails, and Slack summaries."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — tighten origins in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if _settings.app_env == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Lifespan ──────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        await init_db()
        structlog.get_logger().info("meetingmind.startup", env=_settings.app_env)

    @app.on_event("shutdown")
    async def shutdown():
        await close_db()
        structlog.get_logger().info("meetingmind.shutdown")

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(meetings.router)
    app.include_router(webhooks.router)

    return app


app = create_app()
