"""
Logging API Routes
Provides endpoints for querying logs, checking API key health, and debugging.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Log, LogLevel, LogCategory
from .logging_service import LogService, log_api_key_check
from .twilio_client import twilio_sync_client
from .llm_client import llm_client
from .event_logger import event_logger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logging"])


class LogResponse(BaseModel):
    id: int
    timestamp: str
    category: str
    level: str
    message: str
    call_id: Optional[str] = None
    conversation_id: Optional[str] = None
    api_key_type: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    metadata: Optional[dict] = None

    class Config:
        from_attributes = True


class ApiKeyHealthResponse(BaseModel):
    api_key_type: str
    is_valid: bool
    status: str
    last_checked: str
    response_time_ms: Optional[float] = None
    error: Optional[str] = None


class LogStatsResponse(BaseModel):
    total_logs: int
    by_category: dict
    by_level: dict
    by_api_key: dict
    errors_last_24h: int
    api_calls_last_24h: int


@router.get("/", response_model=List[LogResponse])
async def get_logs(
    category: Optional[str] = Query(None, description="Filter by category"),
    level: Optional[str] = Query(None, description="Filter by level"),
    api_key_type: Optional[str] = Query(None, description="Filter by API key type"),
    call_id: Optional[str] = Query(None, description="Filter by call ID"),
    conversation_id: Optional[str] = Query(None, description="Filter by conversation ID"),
    search: Optional[str] = Query(None, description="Search in message/error"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    hours: int = Query(24, ge=1, le=720, description="Search last N hours"),
    db: AsyncSession = Depends(get_db),
):
    """
    Query logs with filtering and search.
    
    Filters:
    - category: api_call, auth, webhook, call_event, ai_analysis, error, system
    - level: debug, info, warning, error
    - api_key_type: elevenlabs, twilio, google, etc.
    - call_id, conversation_id: specific identifiers
    - search: full-text search in message and error fields
    - hours: show last N hours of logs
    """
    
    query = select(Log).order_by(desc(Log.timestamp))
    
    # Apply time filter
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    query = query.where(Log.timestamp >= cutoff_time)
    
    # Apply filters
    filters = []
    if category:
        filters.append(Log.category == category)
    if level:
        filters.append(Log.level == level)
    if api_key_type:
        filters.append(Log.api_key_type == api_key_type)
    if call_id:
        filters.append(Log.call_id == call_id)
    if conversation_id:
        filters.append(Log.conversation_id == conversation_id)
    if search:
        filters.append(
            (Log.message.ilike(f"%{search}%")) | (Log.error.ilike(f"%{search}%"))
        )
    
    if filters:
        query = query.where(and_(*filters))
    
    # Count total matching
    count_query = select(func.count(Log.id)).select_from(Log)
    if filters:
        for f in filters:
            count_query = count_query.where(f)
    total = await db.execute(count_query)
    total = total.scalar() or 0
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return [log.to_dict() for log in logs]


@router.get("/stats", response_model=LogStatsResponse)
async def get_log_stats(
    hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
):
    """Get statistics about logs in the last N hours"""
    
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    
    # Total logs
    result = await db.execute(
        select(func.count(Log.id)).where(Log.timestamp >= cutoff_time)
    )
    total_logs = result.scalar() or 0
    
    # By category
    result = await db.execute(
        select(Log.category, func.count(Log.id))
        .where(Log.timestamp >= cutoff_time)
        .group_by(Log.category)
    )
    by_category = dict(result.all())
    
    # By level
    result = await db.execute(
        select(Log.level, func.count(Log.id))
        .where(Log.timestamp >= cutoff_time)
        .group_by(Log.level)
    )
    by_level = dict(result.all())
    
    # By API key type
    result = await db.execute(
        select(Log.api_key_type, func.count(Log.id))
        .where(Log.timestamp >= cutoff_time)
        .where(Log.api_key_type.isnot(None))
        .group_by(Log.api_key_type)
    )
    by_api_key = dict(result.all())
    
    # Errors in last 24h
    result = await db.execute(
        select(func.count(Log.id))
        .where(Log.level == "error")
        .where(Log.timestamp >= datetime.utcnow() - timedelta(hours=24))
    )
    errors_last_24h = result.scalar() or 0
    
    # API calls in last 24h
    result = await db.execute(
        select(func.count(Log.id))
        .where(Log.category == "api_call")
        .where(Log.timestamp >= datetime.utcnow() - timedelta(hours=24))
    )
    api_calls_last_24h = result.scalar() or 0
    
    return LogStatsResponse(
        total_logs=total_logs,
        by_category=by_category,
        by_level=by_level,
        by_api_key=by_api_key,
        errors_last_24h=errors_last_24h,
        api_calls_last_24h=api_calls_last_24h,
    )


@router.post("/api-key-health", response_model=List[ApiKeyHealthResponse])
async def check_api_key_health(
    db: AsyncSession = Depends(get_db),
):
    """
    Check health of all configured API keys.
    Tests: ElevenLabs, Twilio, Google LLM
    """
    
    results = []
    
    # Twilio API Key Check
    start_time = datetime.utcnow()
    try:
        account = twilio_sync_client.twilio_client.api.accounts(
            twilio_sync_client.account_sid
        ).fetch()
        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        is_valid = account.status == "active"
        error = None if is_valid else f"Account status: {account.status}"
        status = "✓ VALID - Account active" if is_valid else f"✗ Account inactive: {account.status}"
    except Exception as e:
        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        is_valid = False
        error = str(e)
        status = f"✗ INVALID - {type(e).__name__}"
    
    await log_api_key_check("twilio", is_valid, error, response_time, db)
    results.append(
        ApiKeyHealthResponse(
            api_key_type="twilio",
            is_valid=is_valid,
            status=status,
            last_checked=datetime.utcnow().isoformat(),
            response_time_ms=response_time,
            error=error,
        )
    )
    
    # Google LLM API Key Check
    start_time = datetime.utcnow()
    try:
        # Test with a simple model list
        result = await llm_client.test_connection()
        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        is_valid = result.get("success", False)
        error = result.get("error")
        status = "✓ VALID - API responding" if is_valid else f"✗ Error: {error}"
    except Exception as e:
        response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
        is_valid = False
        error = str(e)
        status = f"✗ INVALID - {type(e).__name__}"
    
    await log_api_key_check("google_llm", is_valid, error, response_time, db)
    results.append(
        ApiKeyHealthResponse(
            api_key_type="google_llm",
            is_valid=is_valid,
            status=status,
            last_checked=datetime.utcnow().isoformat(),
            response_time_ms=response_time,
            error=error,
        )
    )
    
    return results


@router.get("/errors", response_model=List[LogResponse])
async def get_recent_errors(
    limit: int = Query(50, ge=1, le=500),
    hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
):
    """Get recent errors for debugging"""
    
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    query = (
        select(Log)
        .where(Log.level == "error")
        .where(Log.timestamp >= cutoff_time)
        .order_by(desc(Log.timestamp))
        .limit(limit)
    )
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return [log.to_dict() for log in logs]


@router.get("/call/{call_id}", response_model=List[LogResponse])
async def get_logs_for_call(
    call_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all logs related to a specific call"""
    
    query = (
        select(Log)
        .where(Log.call_id == call_id)
        .order_by(Log.timestamp)
    )
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    if not logs:
        return []
    
    return [log.to_dict() for log in logs]


@router.get("/conversation/{conversation_id}", response_model=List[LogResponse])
async def get_logs_for_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all logs related to a specific conversation"""
    
    query = (
        select(Log)
        .where(Log.conversation_id == conversation_id)
        .order_by(Log.timestamp)
    )
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    if not logs:
        return []
    
    return [log.to_dict() for log in logs]


@router.delete("/")
async def clear_old_logs(
    days: int = Query(7, ge=1, description="Delete logs older than N days"),
    db: AsyncSession = Depends(get_db),
):
    """
    Clear logs older than N days to maintain database size.
    Default: 7 days
    """
    
    cutoff_time = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(func.count(Log.id)).where(Log.timestamp < cutoff_time)
    )
    count = result.scalar() or 0
    
    if count > 0:
        await db.execute(select(Log).where(Log.timestamp < cutoff_time).delete())
        await db.commit()
        
        await LogService.log(
            category=LogCategory.SYSTEM,
            level=LogLevel.INFO,
            message=f"Deleted {count} logs older than {days} days",
            db=db,
        )
    
    return {
        "status": "success",
        "deleted_count": count,
        "cutoff_time": cutoff_time.isoformat(),
    }


@router.get("/events/status")
async def get_event_logger_status():
    """Get diagnostic status of the NDJSON event logger and GCS synchronization."""
    return event_logger.get_status()


@router.get("/events/recent")
async def get_recent_event_logs(
    limit: int = Query(50, ge=1, le=500),
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (defaults to today)"),
):
    """Retrieve recent structured events from daily NDJSON log file."""
    events = event_logger.read_recent_events(limit=limit, target_date=date)
    return {
        "date": date or datetime.utcnow().strftime("%Y-%m-%d"),
        "count": len(events),
        "events": events,
    }


@router.post("/events/flush-gcs")
async def trigger_gcs_flush(
    bucket: Optional[str] = Query(None, description="Optional target GCS bucket override"),
    date: Optional[str] = Query(None, description="Optional date YYYY-MM-DD to flush"),
):
    """Trigger an immediate flush of the daily NDJSON event log to Google Cloud Storage."""
    res = await event_logger.flush_to_gcs(bucket_name=bucket, target_date=date)
    return res


@router.post("/calls/sync-gcs")
async def sync_calls_to_gcs(
    limit: int = Query(20, ge=1, le=100, description="Number of recent calls to format and sync to GCS"),
):
    """Formats recent calls as structured pretty JSON files and syncs to GCS partitioned by date."""
    results = await event_logger.backfill_call_logs(limit=limit)
    synced = [r for r in results if r.get("success")]
    return {
        "status": "success",
        "synced_count": len(synced),
        "total_requested": len(results),
        "results": results,
    }


