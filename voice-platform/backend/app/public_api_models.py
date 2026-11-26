"""
Pydantic request and response models for the STL Voice Agent Public API v1.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
import re


# ─── Auth / Self-service Token ─────────────────────────────────────────────

STL_ALLOWED_DOMAINS = {"stl.tech", "stltech.in", "stldigital.com", "stldigitaltech.com"}

class RegisterAPIClientRequest(BaseModel):
    """POST /api/v1/auth/register — self-service API key generation"""
    name: str = Field(..., min_length=2, max_length=255, description="Your name or service name", json_schema_extra={"example": "GCP Monitoring Bot"})
    email: str = Field(..., description="Must be an @stl.tech / @stltech.in / @stldigital.com email", json_schema_extra={"example": "ops.admin@stltech.in"})
    company: Optional[str] = Field(None, description="Team or department", json_schema_extra={"example": "Infrastructure Team"})

    @field_validator("email")
    @classmethod
    def validate_stl_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        domain = v.split("@", 1)[1]
        if domain not in STL_ALLOWED_DOMAINS:
            raise ValueError(
                f"Only STL email domains are allowed: {', '.join(sorted(STL_ALLOWED_DOMAINS))}"
            )
        return v

class RegisterAPIClientResponse(BaseModel):
    """Response from POST /api/v1/auth/register"""
    api_key: str = Field(..., description="Your API key — shown ONCE. Store it securely.")
    key_prefix: str
    client_id: int
    email: str
    plan: str
    daily_quota: int
    monthly_quota: int
    message: str


# ─── Me / Profile ─────────────────────────────────────────────────────────

class MeResponse(BaseModel):
    client_id: int
    name: str
    company: Optional[str]
    email: Optional[str]
    plan: str
    key_prefix: str
    is_active: bool
    rate_limit_per_minute: int
    daily_call_quota: int
    monthly_call_quota: int
    calls_today: int
    calls_this_month: int
    daily_remaining: int
    monthly_remaining: int
    created_at: Optional[str]

class RotateKeyResponse(BaseModel):
    api_key: str = Field(..., description="New API key — shown ONCE. Old key is now invalid.")
    key_prefix: str
    message: str


# ─── Chat / Conversation ──────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str

class ChatRequest(BaseModel):
    """POST /api/v1/chat"""
    message: str = Field(..., description="User message text", json_schema_extra={"example": "My laptop won't connect to Wi-Fi"})
    session_id: Optional[str] = Field(None, description="Session ID for multi-turn conversation. Omit to start a new session.")
    workflow_id: Optional[int] = Field(None, description="Optional workflow override")
    history: Optional[List[ChatMessage]] = Field(None, description="Optional explicit history override (ignored if session_id is provided and session exists)")

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    agent: Optional[str] = None
    node_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    rag_context: Optional[str] = None
    turn: int

class CreateSessionRequest(BaseModel):
    """POST /api/v1/chat/sessions"""
    session_id: Optional[str] = Field(None, description="Custom session ID. Auto-generated if omitted.")
    workflow_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None

class SessionResponse(BaseModel):
    session_id: str
    workflow_id: Optional[int]
    turn_count: int
    created_at: str
    updated_at: str
    history: List[Dict[str, Any]]


# ─── TTS ──────────────────────────────────────────────────────────────────

class TTSSynthesizeRequest(BaseModel):
    """POST /api/v1/tts/synthesize"""
    text: str = Field(..., min_length=1, max_length=5000, description="Text to convert to speech", json_schema_extra={"example": "Your request has been received."})
    voice: Optional[str] = Field("Aarohi", description="Voice name", json_schema_extra={"example": "Aarohi"})
    format: Optional[str] = Field("mp3", description="Output format: 'mp3' or 'wav'")

class TTSSynthesizeResponse(BaseModel):
    audio_base64: str = Field(..., description="Base64-encoded audio bytes with data URI prefix, e.g. data:audio/mp3;base64,...")
    format: str
    voice: str
    character_count: int

class VoiceInfo(BaseModel):
    name: str
    language: str
    gender: str
    provider: str


# ─── Outbound Call (gaps) ─────────────────────────────────────────────────

class ListCallsResponse(BaseModel):
    calls: List[Dict[str, Any]]
    total: int
    page: int
    page_size: int


# ─── Tickets ──────────────────────────────────────────────────────────────

class CreateTicketRequest(BaseModel):
    """POST /api/v1/tickets"""
    caller_number: str = Field(..., json_schema_extra={"example": "+919876543210"})
    intent: str = Field(..., json_schema_extra={"example": "printer_issue"})
    issue: str = Field(..., json_schema_extra={"example": "Printer on 3rd floor not connecting to network"})
    priority: str = Field("medium", description="low / medium / high / critical")
    transcript_summary: Optional[str] = Field("", description="Optional conversation summary")
    sentiment: Optional[str] = Field(None)
    call_id: Optional[int] = Field(None, description="Link to an existing call record")

class UpdateTicketRequest(BaseModel):
    """PATCH /api/v1/tickets/{ticket_id}"""
    priority: Optional[str] = None
    issue: Optional[str] = None
    transcript_summary: Optional[str] = None
    sentiment: Optional[str] = None

class TicketResponse(BaseModel):
    id: int
    ticket_id: str
    caller_number: str
    intent: str
    issue: str
    priority: str
    transcript_summary: str
    sentiment: Optional[str]
    created_at: str


# ─── Analytics ────────────────────────────────────────────────────────────

class AnalyticsResponse(BaseModel):
    period_days: int
    total_calls: int
    active_calls: int
    completed_calls: int
    ai_resolved_calls: int
    escalated_calls: int
    transferred_calls: int
    transfer_rate_percent: float
    average_duration_seconds: float


# ─── Webhooks ─────────────────────────────────────────────────────────────

VALID_WEBHOOK_EVENTS = {
    "call.started", "call.ended", "call.transcribed",
    "ticket.created", "ticket.updated",
    "chat.turn",
}

class CreateWebhookRequest(BaseModel):
    """POST /api/v1/webhooks"""
    url: str = Field(..., description="HTTPS URL to send events to", json_schema_extra={"example": "https://yourapp.example.com/webhooks/stl"})
    events: List[str] = Field(..., description=f"Events to subscribe to: {', '.join(sorted(VALID_WEBHOOK_EVENTS))}")
    secret: Optional[str] = Field(None, description="Optional HMAC-SHA256 signing secret for payload verification")

    @field_validator("url")
    @classmethod
    def validate_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("Webhook URL must use HTTPS")
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: List[str]) -> List[str]:
        invalid = set(v) - VALID_WEBHOOK_EVENTS
        if invalid:
            raise ValueError(f"Unknown event types: {invalid}. Valid: {VALID_WEBHOOK_EVENTS}")
        return v

class WebhookResponse(BaseModel):
    id: int
    url: str
    events: List[str]
    is_active: bool
    created_at: str


# ─── Jev AI System 1 Decisions ────────────────────────────────────────────

class JevDecisionRequest(BaseModel):
    """POST /api/v1/decision/jev — evaluate calibrated decision primitives"""
    state: str = Field(..., description="Context or conversation history summary", json_schema_extra={"example": "User reported paper jam on 3rd floor laser printer"})
    questions: Dict[str, Any] = Field(..., description="Mapping of question key to choice/noul/score specification", json_schema_extra={
        "example": {
            "target_node": {
                "type": "choice",
                "instructions": "Which connected agent node should handle this?",
                "options": [
                    {"id": "node_printer", "name": "Printer Support Agent"},
                    {"id": "node_network", "name": "Network Agent"}
                ]
            },
            "wants_escalation": {
                "type": "noul",
                "instructions": "Does caller explicitly request human supervisor or live representative?"
            },
            "urgency": {
                "type": "score",
                "instructions": "Rate urgency or frustration severity from 1 to 5",
                "min": 1,
                "max": 5
            }
        }
    })

class JevDecisionResponse(BaseModel):
    model: str
    answers: Dict[str, Any]
    fallback: bool = False
    error: Optional[str] = None

