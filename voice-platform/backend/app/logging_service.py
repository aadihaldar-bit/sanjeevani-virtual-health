"""
Centralized Logging Service
Handles structured logging for API calls, authentication, active calls, and AI analysis.
All logs are stored in the database for easy querying and debugging.
"""

import json
import logging
import traceback
from datetime import datetime
from typing import Optional, Any
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from .database import async_session
from .models import Log, LogLevel, LogCategory

logger = logging.getLogger(__name__)


class LogService:
    """Centralized service for structured logging"""

    @staticmethod
    async def log(
        category: LogCategory,
        level: LogLevel,
        message: str,
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        api_key_type: Optional[str] = None,  # "elevenlabs", "twilio", "google", etc.
        status_code: Optional[int] = None,
        error: Optional[str] = None,
        metadata: Optional[dict] = None,
        db: Optional[AsyncSession] = None,
    ):
        """
        Log a structured message to database and stdout logger.
        
        Args:
            category: LogCategory enum (API_CALL, AUTH, WEBHOOK, CALL_EVENT, etc.)
            level: LogLevel enum (DEBUG, INFO, WARNING, ERROR)
            message: Human-readable message
            call_id: Internal call ID if applicable
            conversation_id: ElevenLabs conversation ID if applicable
            api_key_type: Type of API key being used
            status_code: HTTP status code for API calls
            error: Error message or traceback
            metadata: Additional structured data
            db: AsyncSession for database operations
        """
        try:
            # Determine Python logging level
            py_level = getattr(logging, level.value.upper(), logging.INFO)
            
            # Log to stdout
            logger_instance = logger
            if metadata:
                logger_instance.log(
                    py_level,
                    f"[{category.value}] {message}",
                    extra={"metadata": metadata, "call_id": call_id, "conversation_id": conversation_id}
                )
            else:
                logger_instance.log(
                    py_level,
                    f"[{category.value}] {message}"
                )

            # Create database log entry
            if not db:
                db = async_session()

            log_entry = Log(
                category=category.value,
                level=level.value,
                message=message,
                call_id=call_id,
                conversation_id=conversation_id,
                api_key_type=api_key_type,
                status_code=status_code,
                error=error,
                metadata_json=metadata or {},
                timestamp=datetime.utcnow(),
            )

            db.add(log_entry)
            await db.commit()

        except Exception as e:
            logger.error(f"Failed to write log: {str(e)}", exc_info=True)

    @staticmethod
    async def log_api_call(
        api_type: str,  # "elevenlabs", "twilio", "google", etc.
        endpoint: str,
        method: str = "GET",
        status_code: Optional[int] = None,
        response_time_ms: Optional[float] = None,
        error: Optional[str] = None,
        request_data: Optional[dict] = None,
        response_data: Optional[dict] = None,
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ):
        """Log an API call with detailed information"""
        
        level = LogLevel.ERROR if error or (status_code and status_code >= 400) else LogLevel.DEBUG
        message = f"{api_type.upper()} {method} {endpoint} - Status: {status_code}"
        
        metadata = {
            "api_type": api_type,
            "endpoint": endpoint,
            "method": method,
            "response_time_ms": response_time_ms,
            "request_data": request_data,
            "response_data": response_data,
        }

        await LogService.log(
            category=LogCategory.API_CALL,
            level=level,
            message=message,
            call_id=call_id,
            conversation_id=conversation_id,
            api_key_type=api_type,
            status_code=status_code,
            error=error,
            metadata=metadata,
            db=db,
        )

    @staticmethod
    async def log_api_key_check(
        api_key_type: str,
        is_valid: bool,
        error: Optional[str] = None,
        response_time_ms: Optional[float] = None,
        db: Optional[AsyncSession] = None,
    ):
        """Log API key validation attempt"""
        
        level = LogLevel.ERROR if not is_valid else LogLevel.INFO
        message = f"{api_key_type.upper()} API Key: {'✓ VALID' if is_valid else '✗ INVALID'}"

        metadata = {
            "api_key_type": api_key_type,
            "is_valid": is_valid,
            "response_time_ms": response_time_ms,
        }

        await LogService.log(
            category=LogCategory.AUTH,
            level=level,
            message=message,
            api_key_type=api_key_type,
            error=error,
            metadata=metadata,
            db=db,
        )

    @staticmethod
    async def log_call_event(
        event_type: str,
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
        message: Optional[str] = None,
        metadata: Optional[dict] = None,
        db: Optional[AsyncSession] = None,
    ):
        """Log call-related events (started, updated, ended, etc.)"""
        
        log_message = message or f"Call event: {event_type}"

        merged_metadata = metadata or {}
        merged_metadata["event_type"] = event_type
        if status:
            merged_metadata["status"] = status

        await LogService.log(
            category=LogCategory.CALL_EVENT,
            level=LogLevel.INFO,
            message=log_message,
            call_id=call_id,
            conversation_id=conversation_id,
            metadata=merged_metadata,
            db=db,
        )

    @staticmethod
    async def log_ai_analysis(
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        analysis_type: str = "sentiment",
        result: Optional[dict] = None,
        error: Optional[str] = None,
        response_time_ms: Optional[float] = None,
        db: Optional[AsyncSession] = None,
    ):
        """Log AI analysis results (sentiment, summary, etc.)"""
        
        level = LogLevel.ERROR if error else LogLevel.INFO
        message = f"AI Analysis ({analysis_type}): {error if error else 'Completed'}"

        metadata = {
            "analysis_type": analysis_type,
            "result": result,
            "response_time_ms": response_time_ms,
        }

        await LogService.log(
            category=LogCategory.AI_ANALYSIS,
            level=level,
            message=message,
            call_id=call_id,
            conversation_id=conversation_id,
            error=error,
            metadata=metadata,
            db=db,
        )

    @staticmethod
    async def log_webhook_received(
        source: str,  # "elevenlabs", "twilio"
        webhook_type: str,
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        data: Optional[dict] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ):
        """Log incoming webhooks"""
        
        level = LogLevel.ERROR if error else LogLevel.INFO
        message = f"Webhook received from {source}: {webhook_type}"

        metadata = {
            "source": source,
            "webhook_type": webhook_type,
            "payload_summary": str(data)[:500] if data else None,
        }

        await LogService.log(
            category=LogCategory.WEBHOOK,
            level=level,
            message=message,
            call_id=call_id,
            conversation_id=conversation_id,
            error=error,
            metadata=metadata,
            db=db,
        )

    @staticmethod
    async def log_exception(
        error: Exception,
        context: str = "Unknown",
        call_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ):
        """Log an exception with full traceback"""
        
        error_msg = f"{type(error).__name__}: {str(error)}"
        traceback_str = traceback.format_exc()

        metadata = {
            "context": context,
            "error_type": type(error).__name__,
            "traceback": traceback_str,
        }

        await LogService.log(
            category=LogCategory.ERROR,
            level=LogLevel.ERROR,
            message=f"Exception in {context}",
            call_id=call_id,
            conversation_id=conversation_id,
            error=error_msg,
            metadata=metadata,
            db=db,
        )


# Convenience instances for quick access
async def log_api_call(*args, **kwargs):
    await LogService.log_api_call(*args, **kwargs)


async def log_api_key_check(*args, **kwargs):
    await LogService.log_api_key_check(*args, **kwargs)


async def log_call_event(*args, **kwargs):
    await LogService.log_call_event(*args, **kwargs)


async def log_ai_analysis(*args, **kwargs):
    await LogService.log_ai_analysis(*args, **kwargs)


async def log_webhook_received(*args, **kwargs):
    await LogService.log_webhook_received(*args, **kwargs)


async def log_exception(*args, **kwargs):
    await LogService.log_exception(*args, **kwargs)
