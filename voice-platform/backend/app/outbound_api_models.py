"""
Pydantic request and response models for the Outbound Voice API.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class OutboundCallAPIRequest(BaseModel):
    """Payload for POST /api/v1/outbound/call"""
    phone_number: str = Field(..., description="Destination phone number (E.164 or 10-digit Indian number)", json_schema_extra={"example": "+919876543210"})
    message_context: str = Field(..., description="The reason or topic for the call. Used by LLM to drive conversation", json_schema_extra={"example": "Firebase quota exceeded for project stl-voicebot-prod"})
    recipient_name: Optional[str] = Field(None, description="Optional name of the person being called", json_schema_extra={"example": "Mithun"})
    system_prompt: Optional[str] = Field(None, description="Optional custom system prompt overlay for the AI agent")
    greeting_template: Optional[str] = Field(None, description="Optional customized initial greeting. Supports {recipient_name} placeholder")
    callback_url: Optional[str] = Field(None, description="Optional webhook URL to receive call completion status and transcript")
    voice: Optional[str] = Field("Aarohi", description="TTS speaker voice")
    workflow_id: Optional[int] = Field(None, description="Optional workflow ID override")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Arbitrary client metadata to attach to the call record")
    provider: Optional[str] = Field(None, description="Optional provider preference ('smartflo', 'twilio', or 'auto')")

class OutboundCallAPIResponse(BaseModel):
    """Response from POST /api/v1/outbound/call"""
    status: str = Field(..., json_schema_extra={"example": "ok"})
    call_id: str = Field(..., json_schema_extra={"example": "outbound-1727012345"})
    message: str = Field(..., json_schema_extra={"example": "Call to Mithun (+919876543210) queued successfully"})
    provider: Optional[str] = Field(None, json_schema_extra={"example": "smartflo"})
    ref_id: Optional[str] = Field(None, json_schema_extra={"example": "SF-12345"})

class CallStatusResponse(BaseModel):
    """Response from GET /api/v1/outbound/calls/{call_id}/status"""
    call_id: str
    status: str
    live_status: Optional[str] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    transcript: Optional[List[Dict[str, Any]]] = None
    summary: Optional[str] = None
    sentiment: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class CreateAPIClientRequest(BaseModel):
    """Request payload for admin creating an API Client"""
    client_name: str = Field(..., json_schema_extra={"example": "GCP Monitoring Service"})
    client_company: Optional[str] = Field(None, json_schema_extra={"example": "Internal Systems"})
    rate_limit_per_minute: Optional[int] = Field(5, json_schema_extra={"example": 5})
    daily_call_quota: Optional[int] = Field(100, json_schema_extra={"example": 100})
    monthly_call_quota: Optional[int] = Field(3000, json_schema_extra={"example": 3000})
    callback_url: Optional[str] = Field(None)

class APIClientResponse(BaseModel):
    """Response model for API Client listing/details"""
    id: int
    client_name: str
    client_company: Optional[str]
    key_prefix: str
    is_active: bool
    rate_limit_per_minute: int
    daily_call_quota: int
    monthly_call_quota: int
    calls_today: int
    calls_this_month: int
    callback_url: Optional[str]
    created_at: Optional[str]
    api_key: Optional[str] = Field(None, description="Only populated upon key creation/rotation")
