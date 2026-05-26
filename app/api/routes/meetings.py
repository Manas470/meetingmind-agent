"""
Meeting ingestion routes.

POST /meetings/process   — Synchronous pipeline run (returns when done)
POST /meetings/async     — Background pipeline run (returns job ID immediately)
GET  /meetings/{id}      — Retrieve stored meeting result
GET  /meetings           — List recent meetings
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models.schemas import (
    MeetingIngestRequest,
    MeetingProcessResponse,
    MeetingStatus,
)
from app.models.db_models import MeetingRecord
from app.services.database import get_db
from app.services.pipeline import MeetingPipeline

router = APIRouter(prefix="/meetings", tags=["meetings"])
logger = structlog.get_logger(__name__)


# ── POST /meetings/process ────────────────────────────────────────────────────

@router.post(
    "/process",
    response_model=MeetingProcessResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Process a meeting transcript (synchronous)",
    description="Submit a transcript and run the full pipeline synchronously. Returns when complete.",
)
async def process_meeting(
    request: MeetingIngestRequest,
    db: AsyncSession = Depends(get_db),
) -> MeetingProcessResponse:
    meeting_id = str(uuid.uuid4())

    # Persist initial record
    record = MeetingRecord(
        id=meeting_id,
        title=request.title,
        source=request.source.value,
        status=MeetingStatus.PROCESSING.value,
        transcript=request.transcript,
        attendees_json=json.dumps([a.model_dump() for a in request.attendees]),
        meeting_date=request.meeting_date,
        duration_minutes=request.duration_minutes,
    )
    db.add(record)
    await db.flush()

    # Run pipeline
    pipeline = MeetingPipeline()
    try:
        result = await pipeline.run(request, meeting_id=meeting_id)
    except Exception as e:
        logger.error("meetings.process_unhandled", error=str(e), meeting_id=meeting_id)
        record.status = MeetingStatus.FAILED.value
        record.errors_json = json.dumps([str(e)])
        raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")

    # Persist result
    record.status = result.status.value
    record.analysis_json = result.analysis.model_dump_json() if result.analysis else None
    record.pipeline_results_json = json.dumps([r.model_dump() for r in result.pipeline_results])
    record.errors_json = json.dumps(result.errors)

    return result


# ── POST /meetings/async ──────────────────────────────────────────────────────

@router.post(
    "/async",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Process a meeting transcript (async background task)",
    description="Submit a transcript for async processing. Returns meeting_id immediately.",
)
async def process_meeting_async(
    request: MeetingIngestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    meeting_id = str(uuid.uuid4())

    record = MeetingRecord(
        id=meeting_id,
        title=request.title,
        source=request.source.value,
        status=MeetingStatus.PENDING.value,
        transcript=request.transcript,
        attendees_json=json.dumps([a.model_dump() for a in request.attendees]),
        meeting_date=request.meeting_date,
        duration_minutes=request.duration_minutes,
    )
    db.add(record)
    await db.flush()

    background_tasks.add_task(_run_pipeline_background, request, meeting_id)

    return {
        "meeting_id": meeting_id,
        "status": "accepted",
        "message": "Pipeline started. Poll GET /meetings/{meeting_id} for results.",
    }


# ── GET /meetings/{meeting_id} ────────────────────────────────────────────────

@router.get(
    "/{meeting_id}",
    response_model=MeetingProcessResponse,
    summary="Retrieve meeting result",
)
async def get_meeting(
    meeting_id: str,
    db: AsyncSession = Depends(get_db),
) -> MeetingProcessResponse:
    result = await db.get(MeetingRecord, meeting_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")
    return _record_to_response(result)


# ── GET /meetings ─────────────────────────────────────────────────────────────

@router.get(
    "",
    summary="List recent meetings",
)
async def list_meetings(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(MeetingRecord).order_by(desc(MeetingRecord.created_at)).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "meeting_id": r.id,
            "title": r.title,
            "status": r.status,
            "source": r.source,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_pipeline_background(request: MeetingIngestRequest, meeting_id: str) -> None:
    """Background task wrapper — uses its own DB session."""
    from app.services.database import get_session
    from app.models.schemas import MeetingAnalysis

    pipeline = MeetingPipeline()
    try:
        result = await pipeline.run(request, meeting_id=meeting_id)
    except Exception as e:
        logger.error("meetings.async_pipeline_error", error=str(e), meeting_id=meeting_id)
        return

    async with get_session() as db:
        record = await db.get(MeetingRecord, meeting_id)
        if record:
            record.status = result.status.value
            record.analysis_json = result.analysis.model_dump_json() if result.analysis else None
            record.pipeline_results_json = json.dumps([r.model_dump() for r in result.pipeline_results])
            record.errors_json = json.dumps(result.errors)


def _record_to_response(record: MeetingRecord) -> MeetingProcessResponse:
    from app.models.schemas import MeetingAnalysis, PipelineStepResult
    analysis = None
    if record.analysis_json:
        analysis = MeetingAnalysis.model_validate_json(record.analysis_json)
    pipeline_results = []
    if record.pipeline_results_json:
        pipeline_results = [
            PipelineStepResult.model_validate(r)
            for r in json.loads(record.pipeline_results_json)
        ]
    errors = json.loads(record.errors_json) if record.errors_json else []
    return MeetingProcessResponse(
        meeting_id=record.id,
        status=MeetingStatus(record.status),
        analysis=analysis,
        pipeline_results=pipeline_results,
        errors=errors,
        created_at=record.created_at or datetime.utcnow(),
    )
