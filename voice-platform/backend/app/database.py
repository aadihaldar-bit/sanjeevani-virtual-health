"""
Database Configuration & Session Factory
Provides async SQLAlchemy engine supporting PostgreSQL (asyncpg) for Cloud deployments
and SQLite (aiosqlite) for local development.
"""

import os
import logging
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from .config import get_config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models"""
    pass


def _create_engine():
    db_url = get_config().infra.database_url
    is_sqlite = "sqlite" in db_url
    
    engine_kwargs = {
        "echo": get_config().infra.debug,
    }
    
    if is_sqlite:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Production Cloud PostgreSQL connection pooling (asyncpg)
        engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "10"))
        engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "20"))
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 300

    return create_async_engine(db_url, **engine_kwargs)


engine = _create_engine()

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def seed_default_workflows():
    """Seeds default workflows (including Brother International, Banking, Symphony Incident Management) if not present."""
    from .models import Workflow, WorkflowNode, WorkflowEdge
    import json
    from sqlalchemy import select

    seed_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seed_workflows.json")
    if not os.path.exists(seed_file):
        seed_file = "/app/seed_workflows.json"
    if not os.path.exists(seed_file):
        return

    try:
        with open(seed_file, "r", encoding="utf-8") as f:
            workflows_data = json.load(f)

        async with async_session() as session:
            for wf_data in workflows_data:
                res = await session.execute(select(Workflow).where(Workflow.id == wf_data["id"]))
                existing = res.scalars().first()
                if not existing:
                    res = await session.execute(select(Workflow).where(Workflow.name == wf_data["name"]))
                    existing = res.scalars().first()

                if not existing:
                    wf = Workflow(
                        id=wf_data.get("id"),
                        name=wf_data["name"],
                        description=wf_data.get("description", ""),
                        is_active=wf_data.get("is_active", False)
                    )
                    session.add(wf)
                    await session.flush()

                    for n in wf_data.get("nodes", []):
                        node = WorkflowNode(
                            id=n["id"],
                            workflow_id=wf.id,
                            type=n["type"],
                            position_x=n.get("position_x", 0),
                            position_y=n.get("position_y", 0),
                            data_json=n.get("data_json", {})
                        )
                        session.add(node)

                    for e in wf_data.get("edges", []):
                        edge = WorkflowEdge(
                            id=e["id"],
                            workflow_id=wf.id,
                            source_node_id=e["source_node_id"],
                            target_node_id=e["target_node_id"],
                            condition=e.get("condition", "")
                        )
                        session.add(edge)

            await session.commit()
            logger.info("Default workflows verified and seeded successfully.")
    except Exception as e:
        logger.warning(f"Error seeding default workflows: {e}")


async def seed_default_tickets():
    """Seeds initial 54 Symphony Incident Tickets if tickets table is empty."""
    from .models import Ticket
    from sqlalchemy import select, func
    from datetime import datetime, timedelta

    async with async_session() as session:
        try:
            res = await session.execute(select(func.count(Ticket.id)))
            count = res.scalar_one_or_none() or 0
            if count > 0:
                return

            logger.info("Seeding initial 54 Symphony incident tickets...")
            intents_categories = [
                ("Hardware & Devices", "Printer Spooler service crashed in Logistics Hub", "P1 Critical", "Hardware failure on Brother HL-L6400DW spooler."),
                ("SAP & ERP Enterprise", "SAP ERP Plant Outage - Production Line 3 Timeout", "P1 Critical", "Enterprise SAP ERP connection timeout on Line 3."),
                ("VPN & Auth Access", "VPN GlobalProtect SSL Certificate Validation Failure", "P2 High Priority", "Authentication loop due to expired SSL cert."),
                ("Network & Connectivity", "Core Switch Switchport CRC Error on VLAN 40", "P2 High Priority", "Packet loss reported across VLAN 40 subnet."),
                ("Software & OS", "Outlook Desktop Client OAuth Sync Failure", "P3 Moderate", "Credentials sync failed after password rotation."),
                ("Hardware & Devices", "Barcode Scanner USB Driver Timeout", "P3 Moderate", "Terminal 04 barcode scanner disconnect."),
                ("SAP & ERP Enterprise", "SAP Material Master Stock Lookup Timeout", "P2 High Priority", "Inventory query latency exceeding 10s threshold."),
                ("VPN & Auth Access", "MFA Token Push Timeout on Mobile Device", "P3 Moderate", "Duo Security push notification timeout."),
                ("Network & Connectivity", "Wi-Fi AP Drop in Executive Conference Room 2B", "P4 Low Priority", "Intermittent packet loss on 5GHz band."),
                ("Software & OS", "Excel Macro Security Policy Blocking Shared Sheet", "P4 Low Priority", "VBA Macro restriction on finance network share."),
            ]

            sample_callers = ["+1 (800) 555-0199", "+91 98765 43210", "+1 (212) 555-0142", "+44 20 7946 0912", "+91 90000 00002"]
            sentiments = ["Frustrated", "Neutral", "Urgent", "Calm", "Annoyed"]

            now = datetime.utcnow()
            tickets_to_add = []

            for i in range(54):
                ticket_num = 10492 - i
                ticket_id = f"SYM-INC-{ticket_num}"
                cat, issue_title, priority, desc = intents_categories[i % len(intents_categories)]
                caller = sample_callers[i % len(sample_callers)]
                sentiment = sentiments[i % len(sentiments)]
                created_time = now - timedelta(hours=i * 2.5)

                t = Ticket(
                    ticket_id=ticket_id,
                    call_id=None,
                    caller_number=caller,
                    intent=cat,
                    issue=f"{issue_title} ({ticket_id})",
                    priority=priority,
                    transcript_summary=f"{desc} Voicebot logged and categorized incident under {cat}.",
                    sentiment=sentiment,
                    created_at=created_time
                )
                tickets_to_add.append(t)

            session.add_all(tickets_to_add)
            await session.commit()
            logger.info("Successfully seeded 54 Symphony incident tickets.")
        except Exception as e:
            logger.warning(f"Error seeding default tickets: {e}")


async def init_db():
    """Create all tables. Called on application startup."""
    from . import models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_startup_migrations)
    await seed_default_workflows()
    await seed_default_tickets()
    await seed_default_tenant()
    logger.info("Database initialized successfully.")


async def seed_default_tenant():
    """Ensure the built-in STL (system) tenant exists, backfill tenant_id on existing rows to it, and
    register the platform super-admins. Idempotent and additive — existing single-tenant behaviour is
    unchanged (everything belongs to the STL tenant). Safe to run on every startup."""
    from sqlalchemy import select, text
    from .models import Tenant, TenantMembership
    from .tenancy import super_admin_emails, SYSTEM_TENANT_SLUG, SYSTEM_TENANT_NAME
    try:
        async with async_session() as db:
            tenant = (await db.execute(
                select(Tenant).where(Tenant.slug == SYSTEM_TENANT_SLUG))).scalars().first()
            if not tenant:
                tenant = Tenant(name=SYSTEM_TENANT_NAME, slug=SYSTEM_TENANT_SLUG,
                                is_system=True, status="active")
                db.add(tenant)
                await db.commit()
                await db.refresh(tenant)
                logger.info("Created system tenant '%s' (id=%s)", SYSTEM_TENANT_SLUG, tenant.id)
            from .tenancy import set_system_tenant_id
            set_system_tenant_id(tenant.id)
            # backfill any rows not yet assigned to a tenant
            for tbl in ("calls", "workflows", "kyc_sessions", "appointments", "tickets",
                        "api_clients", "knowledge_documents"):
                try:
                    await db.execute(
                        text(f"UPDATE {tbl} SET tenant_id = :tid WHERE tenant_id IS NULL"),
                        {"tid": tenant.id})
                except Exception as exc:
                    logger.debug("tenant backfill skipped for %s: %s", tbl, exc)
            await db.commit()
            # register platform super-admins as members of the system tenant
            for email in sorted(super_admin_emails()):
                present = (await db.execute(select(TenantMembership).where(
                    TenantMembership.tenant_id == tenant.id,
                    TenantMembership.email == email))).scalars().first()
                if not present:
                    db.add(TenantMembership(tenant_id=tenant.id, email=email, role="super_admin"))
            await db.commit()
    except Exception as exc:
        logger.warning("seed_default_tenant failed: %s", exc)


def _run_startup_migrations(sync_conn):
    """Lightweight schema upgrades for SQLite deployments without Alembic."""
    db_url = get_config().infra.database_url
    if "postgres" in db_url:
        return
        
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    migrations = []

    if "tickets" not in tables:
        migrations.append(
            """
            CREATE TABLE tickets (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                ticket_id VARCHAR(32) NOT NULL,
                call_id INTEGER,
                caller_number VARCHAR(50) NOT NULL,
                intent VARCHAR(255) NOT NULL,
                issue TEXT NOT NULL,
                priority VARCHAR(50) NOT NULL,
                transcript_summary TEXT NOT NULL,
                sentiment VARCHAR(50),
                created_at DATETIME NOT NULL,
                FOREIGN KEY(call_id) REFERENCES calls (id) ON DELETE SET NULL
            )
            """
        )
        migrations.append("CREATE UNIQUE INDEX IF NOT EXISTS ix_tickets_ticket_id ON tickets (ticket_id)")
        migrations.append("CREATE INDEX IF NOT EXISTS ix_tickets_call_id ON tickets (call_id)")
        migrations.append("CREATE INDEX IF NOT EXISTS ix_tickets_caller_number ON tickets (caller_number)")
        migrations.append("CREATE INDEX IF NOT EXISTS ix_tickets_created_at ON tickets (created_at)")

    if "workflows" in tables:
        wf_columns = {column["name"] for column in inspector.get_columns("workflows")}
        if "settings_json" not in wf_columns:
            migrations.append("ALTER TABLE workflows ADD COLUMN settings_json JSON")

    if "calls" in tables:
        calls_columns = {column["name"] for column in inspector.get_columns("calls")}
        call_schema_map = {
            "conversation_id": "VARCHAR(255)",
            "call_sid": "VARCHAR(255)",
            "caller_number": "VARCHAR(50)",
            "called_number": "VARCHAR(50)",
            "agent_id": "VARCHAR(255)",
            "status": "VARCHAR(20)",
            "live_status": "VARCHAR(32)",
            "source": "VARCHAR(32)",
            "started_at": "DATETIME",
            "ended_at": "DATETIME",
            "last_event_at": "DATETIME",
            "duration_seconds": "FLOAT",
            "end_reason": "VARCHAR(100)",
            "is_escalated": "BOOLEAN DEFAULT 0",
            "transferred_to_human": "BOOLEAN DEFAULT 0",
            "current_agent": "VARCHAR(255)",
            "sentiment": "VARCHAR(50)",
            "intent": "VARCHAR(255)",
            "summary": "TEXT",
            "metadata_json": "JSON",
            "security_json": "JSON",
            "api_client_id": "INTEGER REFERENCES api_clients(id)",
            "created_at": "DATETIME",
            "updated_at": "DATETIME",
        }
        for col_name, col_type in call_schema_map.items():
            if col_name not in calls_columns:
                migrations.append(f"ALTER TABLE calls ADD COLUMN {col_name} {col_type}")

    if "api_clients" in tables:
        api_clients_cols = {column["name"] for column in inspector.get_columns("api_clients")}
        api_clients_schema_map = {
            "email": "VARCHAR(255)",
            "plan": "VARCHAR(32) DEFAULT 'free'",
        }
        for col_name, col_type in api_clients_schema_map.items():
            if col_name not in api_clients_cols:
                migrations.append(f"ALTER TABLE api_clients ADD COLUMN {col_name} {col_type}")

    # Multi-tenancy: add a nullable tenant_id to each tenant-owned table. Nullable + backfilled to
    # the system (STL) tenant, so existing rows and single-tenant behaviour are unaffected.
    for tbl in ("calls", "workflows", "kyc_sessions", "appointments", "tickets",
                "api_clients", "knowledge_documents"):
        if tbl in tables:
            cols = {column["name"] for column in inspector.get_columns(tbl)}
            if "tenant_id" not in cols:
                migrations.append(f"ALTER TABLE {tbl} ADD COLUMN tenant_id INTEGER")
                migrations.append(f"CREATE INDEX IF NOT EXISTS ix_{tbl}_tenant_id ON {tbl} (tenant_id)")

    # Sanjeevani: a meeting is also a booked appointment, and a follow-up call can follow a visit.
    care_columns = {
        "doctor_meeting_requests": {
            "source": "VARCHAR(16) DEFAULT 'followup'", "specialty": "VARCHAR(80)", "slot_start": "DATETIME",
            "patient_name": "VARCHAR(120)", "phone": "VARCHAR(32)", "insurer": "VARCHAR(80)", "fee_amount": "FLOAT",
            "copay_amount": "FLOAT", "payment_status": "VARCHAR(32)", "completed_at": "DATETIME"},
        "follow_up_tasks": {"meeting_id": "INTEGER REFERENCES doctor_meeting_requests(id)"},
        "medicine_feedback": {"meeting_id": "INTEGER REFERENCES doctor_meeting_requests(id)"},
    }
    for tbl, wanted in care_columns.items():
        if tbl in tables:
            have = {column["name"] for column in inspector.get_columns(tbl)}
            for col_name, col_type in wanted.items():
                if col_name not in have:
                    migrations.append(f"ALTER TABLE {tbl} ADD COLUMN {col_name} {col_type}")

    for statement in migrations:
        logger.info("Applying startup migration: %s", statement)
        try:
            sync_conn.exec_driver_sql(statement)
        except Exception as e:
            logger.warning("Migration statement failed: %s (%s)", statement, e)


async def get_db() -> AsyncSession:
    """FastAPI Dependency Injection."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()

