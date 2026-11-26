"""
Database Models
SQLAlchemy ORM models for calls, transcripts, events, and escalations
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime, JSON,
    ForeignKey, Index, Enum as SAEnum
)
from sqlalchemy.orm import relationship
import enum

from .database import Base


class CallStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    FAILED = "failed"


class EscalationStatus(str, enum.Enum):
    REQUESTED = "requested"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class LogLevel(str, enum.Enum):
    """Log level severity"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LogCategory(str, enum.Enum):
    """Log category for filtering and organization"""
    API_CALL = "api_call"
    AUTH = "auth"
    WEBHOOK = "webhook"
    CALL_EVENT = "call_event"
    AI_ANALYSIS = "ai_analysis"
    ERROR = "error"
    SYSTEM = "system"


class Call(Base):
    """Represents a single phone call / conversation"""
    __tablename__ = "calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    conversation_id = Column(String(255), unique=True, nullable=True, index=True)
    call_sid = Column(String(255), nullable=True, index=True)
    caller_number = Column(String(50), nullable=True)
    called_number = Column(String(50), nullable=True)
    agent_id = Column(String(255), nullable=True)
    status = Column(String(20), default=CallStatus.ACTIVE, index=True)
    live_status = Column(String(32), nullable=True, index=True)
    source = Column(String(32), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    last_event_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    end_reason = Column(String(100), nullable=True)
    is_escalated = Column(Boolean, default=False)
    transferred_to_human = Column(Boolean, default=False)
    current_agent = Column(String(255), nullable=True)
    sentiment = Column(String(50), nullable=True)
    intent = Column(String(255), nullable=True)
    summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    security_json = Column(JSON, nullable=True)
    api_client_id = Column(Integer, ForeignKey("api_clients.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    api_client = relationship("APIClient", back_populates="calls")

    # Relationships
    transcript_messages = relationship(
        "TranscriptMessage", back_populates="call",
        cascade="all, delete-orphan", order_by="TranscriptMessage.sequence_number"
    )
    events = relationship(
        "Event", back_populates="call",
        cascade="all, delete-orphan", order_by="Event.created_at"
    )
    escalations = relationship(
        "Escalation", back_populates="call",
        cascade="all, delete-orphan", order_by="Escalation.requested_at"
    )
    tickets = relationship(
        "Ticket", back_populates="call",
        cascade="all, delete-orphan", order_by="desc(Ticket.created_at)"
    )
    appointments = relationship(
        "Appointment", back_populates="call",
        cascade="all, delete-orphan", order_by="desc(Appointment.created_at)"
    )

    __table_args__ = (
        Index("ix_calls_status_started", "status", "started_at"),
    )

    def to_dict(self):
        loaded_tickets = self.__dict__.get("tickets") or []
        latest_ticket = loaded_tickets[0].to_dict() if loaded_tickets else None
        loaded_appts = self.__dict__.get("appointments") or []
        latest_appointment = loaded_appts[0].to_dict() if loaded_appts else None
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "call_sid": self.call_sid,
            "caller_number": self.caller_number,
            "called_number": self.called_number,
            "agent_id": self.agent_id,
            "status": self.status,
            "live_status": self.live_status,
            "source": self.source,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "duration_seconds": self.duration_seconds,
            "end_reason": self.end_reason,
            "is_escalated": self.is_escalated,
            "transferred_to_human": self.transferred_to_human,
            "current_agent": self.current_agent,
            "sentiment": self.sentiment,
            "intent": self.intent,
            "summary": self.summary,
            "latest_ticket": latest_ticket,
            "latest_appointment": latest_appointment,
            "security": self.security_json or None,
        }

    def to_detail_dict(self):
        data = self.to_dict()
        data["transcript"] = [m.to_dict() for m in self.transcript_messages]
        data["escalations"] = [e.to_dict() for e in self.escalations]
        data["appointments"] = [a.to_dict() for a in (self.__dict__.get("appointments") or [])]
        return data


class TranscriptMessage(Base):
    """Individual message within a call transcript"""
    __tablename__ = "transcript_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "agent"
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    sequence_number = Column(Integer, default=0)

    # Relationship
    call = relationship("Call", back_populates="transcript_messages")

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "message": self.message,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "sequence_number": self.sequence_number,
        }


class Event(Base):
    """Generic event log for call lifecycle events"""
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    call = relationship("Call", back_populates="events")

    def to_dict(self):
        return {
            "id": self.id,
            "call_id": self.call_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Escalation(Base):
    """Tracks escalation / human handoff lifecycle"""
    __tablename__ = "escalations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    status = Column(String(20), default=EscalationStatus.REQUESTED)
    requested_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    transferred_to = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationship
    call = relationship("Call", back_populates="escalations")

    def to_dict(self):
        return {
            "id": self.id,
            "call_id": self.call_id,
            "reason": self.reason,
            "status": self.status,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "transferred_to": self.transferred_to,
            "notes": self.notes,
        }


class Ticket(Base):
    """Stores tickets created from ElevenLabs tool calls."""
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    ticket_id = Column(String(32), unique=True, nullable=False, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    caller_number = Column(String(50), nullable=False, index=True)
    intent = Column(String(255), nullable=False)
    issue = Column(Text, nullable=False)
    priority = Column(String(50), nullable=False)
    transcript_summary = Column(Text, nullable=False)
    sentiment = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    call = relationship("Call", back_populates="tickets")

    def to_dict(self):
        return {
            "id": self.id,
            "ticket_id": self.ticket_id,
            "call_id": self.call_id,
            "caller_number": self.caller_number,
            "intent": self.intent,
            "issue": self.issue,
            "priority": self.priority,
            "transcript_summary": self.transcript_summary,
            "sentiment": self.sentiment,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Appointment(Base):
    """Stores doctor appointments booked by the medical receptionist agent."""
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    appointment_id = Column(String(32), unique=True, nullable=False, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    patient_name = Column(String(120), nullable=True)
    caller_number = Column(String(50), nullable=True, index=True)
    is_new_patient = Column(Boolean, default=True)
    problem = Column(Text, nullable=True)
    doctor_name = Column(String(120), nullable=True)
    doctor_specialty = Column(String(120), nullable=True)
    appointment_time = Column(String(120), nullable=True)
    status = Column(String(40), default="scheduled")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    call = relationship("Call", back_populates="appointments")

    def to_dict(self):
        return {
            "id": self.id,
            "appointment_id": self.appointment_id,
            "call_id": self.call_id,
            "patient_name": self.patient_name,
            "caller_number": self.caller_number,
            "is_new_patient": self.is_new_patient,
            "problem": self.problem,
            "doctor_name": self.doctor_name,
            "doctor_specialty": self.doctor_specialty,
            "appointment_time": self.appointment_time,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KycSession(Base):
    """Identity (KYC) verification for a patient, linked to the call that requested it.

    Privacy: we keep only the minimum — verified name, birth YEAR, gender, last-4 of the
    Aadhaar number, scores and consent. No Aadhaar number, no photos. The ID-photo embedding
    is held only until the session finishes and is then wiped.
    """
    __tablename__ = "kyc_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    code = Column(String(8), nullable=True, index=True)             # spoken one-time code (wiped when done)
    token = Column(String(64), unique=True, nullable=False, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    caller_number = Column(String(50), nullable=True)
    claimed_name = Column(String(120), nullable=True)               # what the patient said on the call

    status = Column(String(24), default="pending", nullable=False, index=True)
    # pending -> consented -> qr_verified -> verified | failed | expired
    attempts_qr = Column(Integer, default=0)
    attempts_selfie = Column(Integer, default=0)
    failed_exchanges = Column(Integer, default=0)
    extra_challenge = Column(String(12), default="nod")             # nod | zoom (randomised per session)

    consent_at = Column(DateTime, nullable=True)
    consent_ip = Column(String(64), nullable=True)
    consent_ua = Column(String(255), nullable=True)

    trust = Column(String(12), nullable=True)                       # uidai | demo
    signer = Column(String(80), nullable=True)
    verified_name = Column(String(120), nullable=True)
    birth_year = Column(String(4), nullable=True)
    gender = Column(String(2), nullable=True)
    aadhaar_last4 = Column(String(4), nullable=True)
    name_match = Column(Float, nullable=True)                       # similarity(claimed, verified), 0..1
    id_embedding = Column(Text, nullable=True)                      # JSON; wiped on completion/expiry

    face_score = Column(Float, nullable=True)
    liveness_passed = Column(Boolean, nullable=True)
    failure_reason = Column(String(80), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    verified_at = Column(DateTime, nullable=True)

    def summary(self):
        return {
            "id": self.id,
            "call_id": self.call_id,
            "status": self.status,
            "trust": self.trust,
            "verified_name": self.verified_name,
            "birth_year": self.birth_year,
            "aadhaar_last4": self.aadhaar_last4,
            "face_score": self.face_score,
            "liveness_passed": self.liveness_passed,
            "name_match": self.name_match,
            "failure_reason": self.failure_reason,
            "demo": self.trust == "demo",
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class APIClient(Base):
    """External API client with key-based auth for outbound call API."""
    __tablename__ = "api_clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    client_name = Column(String(255), nullable=False)
    client_company = Column(String(255), nullable=True)
    api_key_hash = Column(String(128), unique=True, nullable=False, index=True)
    key_prefix = Column(String(20), nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    rate_limit_per_minute = Column(Integer, default=5)
    daily_call_quota = Column(Integer, default=100)
    monthly_call_quota = Column(Integer, default=3000)
    calls_today = Column(Integer, default=0)
    calls_this_month = Column(Integer, default=0)
    last_quota_reset = Column(DateTime, default=datetime.utcnow)
    callback_url = Column(String(512), nullable=True)
    email = Column(String(255), nullable=True, index=True)     # self-registered via /api/v1/auth/register
    plan = Column(String(32), nullable=True, default="free")   # free / pro / enterprise
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(255), nullable=True)

    calls = relationship("Call", back_populates="api_client")
    chat_sessions = relationship("ChatSession", back_populates="api_client", cascade="all, delete-orphan")
    webhooks = relationship("WebhookEndpoint", back_populates="api_client", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "client_name": self.client_name,
            "client_company": self.client_company,
            "email": self.email,
            "plan": self.plan or "free",
            "key_prefix": self.key_prefix,
            "is_active": self.is_active,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "daily_call_quota": self.daily_call_quota,
            "monthly_call_quota": self.monthly_call_quota,
            "calls_today": self.calls_today,
            "calls_this_month": self.calls_this_month,
            "callback_url": self.callback_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by
        }


class ChatSession(Base):
    """Persistent multi-turn chat session for the Public Chat API."""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), unique=True, nullable=False, index=True)
    api_client_id = Column(Integer, ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id = Column(Integer, nullable=True)
    turn_count = Column(Integer, default=0)
    history_json = Column(JSON, nullable=True)   # list of {role, content}
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    api_client = relationship("APIClient", back_populates="chat_sessions")

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "api_client_id": self.api_client_id,
            "workflow_id": self.workflow_id,
            "turn_count": self.turn_count,
            "history": self.history_json or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WebhookEndpoint(Base):
    """Registered webhook endpoint for the Public Webhooks API."""
    __tablename__ = "webhook_endpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_client_id = Column(Integer, ForeignKey("api_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(512), nullable=False)
    events = Column(JSON, nullable=False)   # list of event names e.g. ["call.ended", "ticket.created"]
    secret = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    api_client = relationship("APIClient", back_populates="webhooks")

    def to_dict(self):
        return {
            "id": self.id,
            "api_client_id": self.api_client_id,
            "url": self.url,
            "events": self.events or [],
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeDocument(Base):
    """Persisted Knowledge Base RAG Documents table."""
    __tablename__ = "knowledge_documents"

    id = Column(String(64), primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    size_kb = Column(Float, nullable=False, default=0.0)
    chunks = Column(Integer, nullable=False, default=1)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "size_kb": self.size_kb,
            "chunks": self.chunks,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else datetime.utcnow().isoformat(),
        }


class Log(Base):
    """Structured logs for debugging: API calls, auth, webhooks, AI analysis, call events, errors"""
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)  # api_call, auth, webhook, call_event, ai_analysis, error, system
    level = Column(String(20), nullable=False, index=True)  # debug, info, warning, error
    message = Column(Text, nullable=False)
    
    # Foreign keys (optional)
    call_id = Column(String(255), nullable=True, index=True)  # Internal call ID
    conversation_id = Column(String(255), nullable=True, index=True)  # ElevenLabs conversation ID
    
    # API-specific fields
    api_key_type = Column(String(50), nullable=True, index=True)  # elevenlabs, twilio, google, etc.
    status_code = Column(Integer, nullable=True)
    
    # Error details
    error = Column(Text, nullable=True)
    
    # Flexible JSON metadata
    metadata_json = Column(JSON, nullable=True)
    
    # Composite indexes for fast filtering
    __table_args__ = (
        Index("ix_logs_timestamp_category", "timestamp", "category"),
        Index("ix_logs_timestamp_level", "timestamp", "level"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "category": self.category,
            "level": self.level,
            "message": self.message,
            "call_id": self.call_id,
            "conversation_id": self.conversation_id,
            "api_key_type": self.api_key_type,
            "status_code": self.status_code,
            "error": self.error,
            "metadata": self.metadata_json,
        }

class Workflow(Base):
    """Stores a visual workflow graph configuration."""
    __tablename__ = "workflows"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=False)
    settings_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    nodes = relationship("WorkflowNode", back_populates="workflow", cascade="all, delete-orphan")
    edges = relationship("WorkflowEdge", back_populates="workflow", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "settings": self.settings_json or {},
            "tenant_id": self.tenant_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


class WorkflowNode(Base):
    """A single node (Start, Agent, End, Tool) on the canvas."""
    __tablename__ = "workflow_nodes"
    id = Column(String(255), primary_key=True) # UUID from React Flow
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(50), nullable=False) # e.g. 'startNode', 'agentNode', 'endNode', 'toolNode'
    
    # Visual position on canvas
    position_x = Column(Float, nullable=False, default=0.0)
    position_y = Column(Float, nullable=False, default=0.0)
    
    # Configuration (prompt, model, voice, kb config)
    data_json = Column(JSON, nullable=False)

    workflow = relationship("Workflow", back_populates="nodes")

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "position": {"x": self.position_x, "y": self.position_y},
            "data": self.data_json
        }


class WorkflowEdge(Base):
    """Connections between nodes, with transition conditions."""
    __tablename__ = "workflow_edges"
    id = Column(String(255), primary_key=True) # UUID from React Flow
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    source_node_id = Column(String(255), ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False)
    target_node_id = Column(String(255), ForeignKey("workflow_nodes.id", ondelete="CASCADE"), nullable=False)
    
    # The condition/keyword the LLM must output to take this path
    condition = Column(String(255), nullable=True) 
    label = Column(String(255), nullable=True)

    workflow = relationship("Workflow", back_populates="edges")

    def to_dict(self):
        return {
            "id": self.id,
            "source": self.source_node_id,
            "target": self.target_node_id,
            "condition": self.condition,
            "label": self.label
        }



class Tenant(Base):
    """An organization (customer) on the platform. The first/system tenant is STL itself.
    Every tenant-owned row carries this tenant's id; data is isolated per tenant."""
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    status = Column(String(32), default="active")          # active | suspended
    is_system = Column(Boolean, default=False)              # True for the built-in STL tenant
    # Object-store isolation (see the multi-tenancy brainstorm): the bucket this tenant's call
    # JSON / recordings / KPIs / docs live in. NULL = use the platform default bucket.
    gcs_bucket = Column(String(255), nullable=True)
    settings_json = Column(JSON, nullable=True)             # per-tenant provider keys/flags (later)
    plan = Column(String(32), nullable=True, default="standard")
    timezone = Column(String(64), nullable=True, default="Asia/Kolkata")  # scheduler/console local time (Sanjeevani: Asia/Dubai)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "status": self.status,
            "is_system": self.is_system,
            "gcs_bucket": self.gcs_bucket,
            "settings": self.settings_json or {},
            "storage": (self.settings_json or {}).get("storage"),
            "handover_number": (self.settings_json or {}).get("handover_number"),
            "calling_window": (self.settings_json or {}).get("calling_window"),
            "timezone": self.timezone or "Asia/Kolkata",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "plan": self.plan,
        }


class TenantMembership(Base):
    """Which users (by email, from the Google/Firebase sign-in) belong to which tenant, and their
    role there. Super admins are additionally listed in SUPER_ADMIN_EMAILS (tenancy.py) and can act
    across all tenants regardless of membership."""
    __tablename__ = "tenant_memberships"
    __table_args__ = (Index("ix_tenant_memberships_tenant_email", "tenant_id", "email", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    # super_admin | tenant_admin | manager | operator | viewer
    role = Column(String(32), nullable=False, default="viewer")
    status = Column(String(16), nullable=False, default="active")              # active / invited
    invited_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "tenant_id": self.tenant_id, "email": self.email, "role": self.role,
                "status": self.status, "invited_by": self.invited_by,
                "created_at": self.created_at.isoformat() if self.created_at else None}

# --- Numbers, external models and vector stores (all belong to a tenant) ---------------

class PhoneNumber(Base):
    """Number inventory. A number belongs to at most one organization and, inside it, to at most one agent (workflow)."""
    __tablename__ = "phone_numbers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    number = Column(String(20), nullable=False, unique=True, index=True)       # E.164, for example +918069879488
    carrier = Column(String(32), nullable=False, default="smartflo")           # smartflo / twilio / other
    direction = Column(String(10), nullable=False, default="both")             # inbound / outbound / both
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True)
    label = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "number": self.number, "carrier": self.carrier, "direction": self.direction,
                "tenant_id": self.tenant_id, "workflow_id": self.workflow_id, "label": self.label,
                "status": "unallocated" if self.tenant_id is None else ("assigned" if self.workflow_id else "allocated"),
                "created_at": self.created_at.isoformat() if self.created_at else None}


class NumberAllocationEvent(Base):
    """History of every allocation change."""
    __tablename__ = "number_allocation_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    number_id = Column(Integer, ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(24), nullable=False)        # added / allocated / assigned / unassigned / released
    from_tenant_id = Column(Integer, nullable=True)
    to_tenant_id = Column(Integer, nullable=True)
    from_workflow_id = Column(Integer, nullable=True)
    to_workflow_id = Column(Integer, nullable=True)
    actor = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "number_id": self.number_id, "action": self.action, "from_tenant_id": self.from_tenant_id,
                "to_tenant_id": self.to_tenant_id, "from_workflow_id": self.from_workflow_id, "to_workflow_id": self.to_workflow_id,
                "actor": self.actor, "created_at": self.created_at.isoformat() if self.created_at else None}


class ProviderCredential(Base):
    """An organization's own API key for a provider. The key is stored encrypted and is never returned."""
    __tablename__ = "provider_credentials"
    __table_args__ = (Index("ux_provider_credentials", "tenant_id", "kind", "provider", unique=True),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(8), nullable=False)             # llm / stt / tts
    provider = Column(String(32), nullable=False)
    secret_enc = Column(Text, nullable=False)
    last4 = Column(String(8), nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "tenant_id": self.tenant_id, "kind": self.kind, "provider": self.provider,
                "key_hint": f"...{self.last4}" if self.last4 else "",
                "updated_at": self.updated_at.isoformat() if self.updated_at else None}


class ModelEndpoint(Base):
    """A custom OpenAI-compatible model endpoint (an organization's own, or platform-wide when tenant_id is NULL)."""
    __tablename__ = "model_endpoints"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(512), nullable=False)
    model = Column(String(200), nullable=False)
    api_key_enc = Column(Text, nullable=True)
    headers_enc = Column(Text, nullable=True)
    supports_tools = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    acknowledged_by = Column(String(255), nullable=True)   # who accepted that transcripts are sent to this service
    last_test_ok = Column(Boolean, nullable=True)
    last_test_ms = Column(Float, nullable=True)
    last_test_error = Column(String(300), nullable=True)
    last_tested_at = Column(DateTime, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "tenant_id": self.tenant_id, "name": self.name, "base_url": self.base_url, "model": self.model,
                "has_api_key": bool(self.api_key_enc), "has_headers": bool(self.headers_enc),
                "supports_tools": bool(self.supports_tools), "enabled": bool(self.enabled),
                "last_test_ok": self.last_test_ok, "last_test_ms": self.last_test_ms, "last_test_error": self.last_test_error,
                "last_tested_at": self.last_tested_at.isoformat() if self.last_tested_at else None,
                "provider_id": f"custom:{self.id}"}


class VectorStoreConfig(Base):
    """An organization's own vector database for knowledge retrieval (one per organization)."""
    __tablename__ = "vector_stores"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    provider = Column(String(24), nullable=False, default="qdrant")
    url = Column(String(512), nullable=False)
    api_key_enc = Column(Text, nullable=True)
    collection = Column(String(120), nullable=False)
    mode = Column(String(12), nullable=False, default="index")      # index = we write our vectors; read_only = query an existing index
    text_field = Column(String(80), nullable=True)                  # read_only: payload field holding the passage text
    source_field = Column(String(80), nullable=True)                # read_only: payload field naming the source
    dim = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True)
    last_test_ok = Column(Boolean, nullable=True)
    last_test_ms = Column(Float, nullable=True)
    last_test_error = Column(String(300), nullable=True)
    last_tested_at = Column(DateTime, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "tenant_id": self.tenant_id, "provider": self.provider, "url": self.url,
                "has_api_key": bool(self.api_key_enc), "collection": self.collection, "mode": self.mode,
                "text_field": self.text_field, "source_field": self.source_field, "dim": self.dim,
                "enabled": bool(self.enabled), "last_test_ok": self.last_test_ok, "last_test_ms": self.last_test_ms,
                "last_test_error": self.last_test_error,
                "last_tested_at": self.last_tested_at.isoformat() if self.last_tested_at else None}


class AdminAuditEntry(Base):
    """Who did what: administrative actions only. Never holds secret values."""
    __tablename__ = "admin_audit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    actor = Column(String(255), nullable=False)
    tenant_id = Column(Integer, nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    target = Column(String(255), nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {"id": self.id, "actor": self.actor, "tenant_id": self.tenant_id, "action": self.action, "target": self.target,
                "detail": self.detail, "created_at": self.created_at.isoformat() if self.created_at else None}


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# Sanjeevani patient-medication voice agent (outbound explain + feedback). All tenant-scoped.
# ──────────────────────────────────────────────────────────────────────────────────────────────────
class Patient(Base):
    """A patient (or their guardian) the agent calls. Identity check uses `identity_dob`."""
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    external_id = Column(String(120), nullable=True, index=True)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    guardian_name = Column(String(120), nullable=True)          # person spoken to, if calling about a child
    phone_e164 = Column(String(32), nullable=True, index=True)
    identity_dob = Column(String(32), nullable=True)            # DOB asked to confirm identity (child's, for a guardian)
    preferred_language = Column(String(16), default="en")       # "en" | "ar" | ...
    do_not_call = Column(Boolean, default=False)
    consent_calls = Column(Boolean, default=True)
    consent_recorded_at = Column(DateTime, nullable=True)
    extra_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {"id": self.id, "external_id": self.external_id, "first_name": self.first_name,
                "last_name": self.last_name, "guardian_name": self.guardian_name,
                "phone_e164": self.phone_e164, "preferred_language": self.preferred_language,
                "do_not_call": self.do_not_call, "consent_calls": self.consent_calls}


class Prescription(Base):
    __tablename__ = "prescriptions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    external_id = Column(String(120), nullable=True, index=True)
    doctor_name = Column(String(120), nullable=True)
    doctor_external_id = Column(String(120), nullable=True)
    facility = Column(String(160), nullable=True)
    course_days = Column(Integer, nullable=True)
    extra_instruction = Column(Text, nullable=True)
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=True)
    status = Column(String(24), default="issued", index=True)   # draft|issued|explained|completed|cancelled
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    items = relationship("PrescriptionItem", back_populates="prescription", cascade="all, delete-orphan")

    def to_dict(self):
        return {"id": self.id, "external_id": self.external_id, "patient_id": self.patient_id,
                "doctor_name": self.doctor_name, "facility": self.facility, "course_days": self.course_days,
                "extra_instruction": self.extra_instruction, "status": self.status,
                "items": [i.to_dict() for i in (self.items or [])]}


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    prescription_id = Column(Integer, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False, index=True)
    drug_name = Column(String(160), nullable=True)
    strength = Column(String(80), nullable=True)
    form = Column(String(40), nullable=True)                    # tablet/syrup/injection...
    dose_text = Column(String(120), nullable=True)              # read verbatim; null when needs_clarification
    frequency_per_day = Column(Integer, nullable=True)
    timing = Column(String(160), nullable=True)                 # before/after food, morning, bedtime...
    start_date = Column(DateTime, nullable=True)
    duration_days = Column(Integer, nullable=True)
    end_date = Column(DateTime, nullable=True, index=True)      # computed start_date + duration_days
    refills = Column(Integer, default=0)
    special_instructions = Column(Text, nullable=True)
    plain_summary_en = Column(Text, nullable=True)              # pharmacist-approved; never generated at call time
    plain_summary_ar = Column(Text, nullable=True)
    warnings = Column(Text, nullable=True)
    status = Column(String(24), default="confirmed")           # draft|confirmed|needs_clarification
    open_question = Column(Text, nullable=True)

    prescription = relationship("Prescription", back_populates="items")

    def to_dict(self):
        return {"id": self.id, "drug_name": self.drug_name, "strength": self.strength, "form": self.form,
                "dose_text": self.dose_text, "frequency_per_day": self.frequency_per_day, "timing": self.timing,
                "duration_days": self.duration_days,
                "end_date": self.end_date.isoformat() if self.end_date else None,
                "plain_summary_en": self.plain_summary_en, "plain_summary_ar": self.plain_summary_ar,
                "warnings": self.warnings, "status": self.status, "open_question": self.open_question}


class FollowUpTask(Base):
    __tablename__ = "follow_up_tasks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    prescription_id = Column(Integer, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=True, index=True)
    meeting_id = Column(Integer, ForeignKey("doctor_meeting_requests.id", ondelete="CASCADE"), nullable=True, index=True)   # a feedback call after a visit
    kind = Column(String(16), nullable=False, index=True)       # explain|feedback
    due_at = Column(DateTime, nullable=False, index=True)
    window_start = Column(String(8), default="09:00")           # local HH:MM calling window
    window_end = Column(String(8), default="20:00")
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    status = Column(String(24), default="pending", index=True)  # pending|calling|done|no_answer|patient_declined|cancelled|needs_human
    last_call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True)
    next_attempt_at = Column(DateTime, nullable=True, index=True)
    locked_at = Column(DateTime, nullable=True)                 # set while a worker is placing the call (restart safety)
    outcome_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {"id": self.id, "patient_id": self.patient_id, "prescription_id": self.prescription_id,
                "meeting_id": self.meeting_id, "kind": self.kind, "due_at": self.due_at.isoformat() if self.due_at else None,
                "attempts": self.attempts, "max_attempts": self.max_attempts, "status": self.status,
                "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None}


class MedicineFeedback(Base):
    __tablename__ = "medicine_feedback"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    prescription_item_id = Column(Integer, ForeignKey("prescription_items.id", ondelete="SET NULL"), nullable=True, index=True)
    prescription_id = Column(Integer, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=True, index=True)
    meeting_id = Column(Integer, ForeignKey("doctor_meeting_requests.id", ondelete="SET NULL"), nullable=True, index=True)   # the visit this is about
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    taken = Column(String(16), nullable=True)                   # yes|partly|no|not_taken
    helped = Column(String(16), nullable=True)                  # yes|partly|no
    satisfaction = Column(Integer, nullable=True)               # 1-5
    side_effects_text = Column(Text, nullable=True)
    wants_doctor = Column(Boolean, default=False)
    refill_requested = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {"id": self.id, "prescription_id": self.prescription_id, "meeting_id": self.meeting_id,
                "taken": self.taken, "helped": self.helped,
                "satisfaction": self.satisfaction, "side_effects_text": self.side_effects_text,
                "wants_doctor": self.wants_doctor, "refill_requested": self.refill_requested,
                "created_at": self.created_at.isoformat() if self.created_at else None}


class DoctorMeetingRequest(Base):
    __tablename__ = "doctor_meeting_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=True, index=True)
    prescription_id = Column(Integer, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=True, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    doctor_name = Column(String(120), nullable=True)
    reason = Column(Text, nullable=True)
    preferred_times_json = Column(JSON, nullable=True)
    status = Column(String(24), default="requested", index=True)  # requested|booked|done|cancelled
    booked_slot = Column(String(120), nullable=True)
    external_ref = Column(String(120), nullable=True)
    # A meeting is also an appointment the agent booked on an inbound call (source "inbound"): who, with which doctor, at what
    # time, what it costs and how it is paid. The clinic marks it done after the visit, which schedules the feedback call.
    source = Column(String(16), default="followup")             # followup (asked for on a feedback call) | inbound (booked by the agent)
    specialty = Column(String(80), nullable=True)
    slot_start = Column(DateTime, nullable=True, index=True)    # UTC
    patient_name = Column(String(120), nullable=True)
    phone = Column(String(32), nullable=True)
    insurer = Column(String(80), nullable=True)
    fee_amount = Column(Float, nullable=True)
    copay_amount = Column(Float, nullable=True)
    payment_status = Column(String(32), nullable=True)          # to_pay_at_visit | paid
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {"id": self.id, "patient_id": self.patient_id, "prescription_id": self.prescription_id,
                "doctor_name": self.doctor_name, "reason": self.reason,
                "preferred_times": self.preferred_times_json, "status": self.status,
                "booked_slot": self.booked_slot, "external_ref": self.external_ref,
                "source": self.source, "specialty": self.specialty, "patient_name": self.patient_name, "phone": self.phone,
                "slot_start": self.slot_start.isoformat() if self.slot_start else None,
                "insurer": self.insurer, "fee_amount": self.fee_amount, "copay_amount": self.copay_amount,
                "payment_status": self.payment_status,
                "completed_at": self.completed_at.isoformat() if self.completed_at else None}


class MedicineOrder(Base):
    """A medicine order taken on an inbound call (the Pharmacy Desk): what was asked for, the prescription that was uploaded,
    where and when it is delivered and whether it is paid."""
    __tablename__ = "medicine_orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    reference = Column(String(24), nullable=True, index=True)
    status = Column(String(32), default="awaiting_prescription", index=True)   # awaiting_prescription|prescription_received|awaiting_payment|paid|scheduled|delivered|cancelled
    requested_text = Column(Text, nullable=True)                # what the caller said they want
    items_json = Column(JSON, nullable=True)                    # read from the uploaded prescription: [{drug_name, strength, form, dose_text, price, ...}]
    id_document_received = Column(Boolean, default=False)
    contact_name = Column(String(120), nullable=True)
    contact_phone = Column(String(32), nullable=True)
    address = Column(Text, nullable=True)
    amount = Column(Float, nullable=True)
    payment_status = Column(String(24), default="unpaid")       # unpaid|paid
    delivery_slot = Column(String(120), nullable=True)
    delivery_start = Column(DateTime, nullable=True)            # UTC
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {"id": self.id, "reference": self.reference, "status": self.status, "patient_id": self.patient_id,
                "requested_text": self.requested_text, "items": self.items_json or [], "contact_name": self.contact_name,
                "contact_phone": self.contact_phone, "address": self.address, "amount": self.amount,
                "payment_status": self.payment_status, "delivery_slot": self.delivery_slot, "call_id": self.call_id,
                "id_document_received": bool(self.id_document_received),
                "created_at": self.created_at.isoformat() if self.created_at else None}


class CareLink(Base):
    """A one-time link the agent gives a caller on a call (upload a prescription, pay). The link is a long random token; to use it
    the person also types the short code the agent read out. It expires, and locks after a few wrong codes."""
    __tablename__ = "care_links"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    call_id = Column(Integer, ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = Column(Integer, ForeignKey("medicine_orders.id", ondelete="CASCADE"), nullable=True, index=True)
    kind = Column(String(16), nullable=False)                   # prescription|payment
    token = Column(String(64), nullable=False, unique=True, index=True)
    code = Column(String(8), nullable=False)
    status = Column(String(16), default="pending")              # pending|done|expired|locked
    attempts = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=False)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    used_at = Column(DateTime, nullable=True)


# Every new tenant-owned row is stamped with a tenant (its call's, else the STL tenant).
from .tenancy import install_read_filter, install_row_stamping  # noqa: E402

install_row_stamping()
install_read_filter()
