"""
WebSocket Connection Manager
Handles dashboard WebSocket connections with broadcasting capabilities.
Simplified for voice agent architecture — only manages dashboard clients.
"""

import os
import json
import logging
import asyncio
from typing import Dict, Optional
from fastapi import WebSocket
import redis.asyncio as redis

logger = logging.getLogger(__name__)

from .tenancy import multi_tenant_enabled

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _message_tenant(message_text: str) -> Optional[int]:
    try:
        value = json.loads(message_text).get("tenant_id")
        return int(value) if value is not None else None
    except Exception:
        return None


async def _tenant_of_event(data: dict) -> Optional[int]:
    """Which tenant a live event belongs to: the one on its call, found by id or conversation id."""
    value = data.get("tenant_id") or (data.get("call") or {}).get("tenant_id")
    if value is not None:
        return value
    call_id, conversation_id = data.get("call_id"), data.get("conversation_id")
    if call_id is None and conversation_id is None:
        return None
    try:
        from sqlalchemy import select
        from .database import async_session
        from .models import Call
        async with async_session() as db:
            query = select(Call.tenant_id)
            query = query.where(Call.id == call_id) if call_id is not None else query.where(Call.conversation_id == conversation_id)
            return (await db.execute(query)).scalar()
    except Exception as exc:
        logger.debug("could not place live event on a tenant: %s", exc)
        return None


class ConnectionManager:
    """Manages WebSocket connections and uses Redis Pub/Sub for cross-worker broadcasting"""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_tenants: Dict[str, Optional[int]] = {}      # None = sees every tenant
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        self.pubsub = self.redis_client.pubsub()
        self.listen_task = None

    async def _listen_to_redis(self):
        """Background task to listen for messages from Redis if available."""
        while True:
            try:
                await self.pubsub.subscribe("dashboard_events")
                logger.info("Subscribed to Redis channel: dashboard_events")
                async for message in self.pubsub.listen():
                    if message["type"] == "message":
                        data = message["data"]
                        await self._send_to_local_connections(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Sleep longer if Redis is unavailable locally
                await asyncio.sleep(10)

    async def _send_to_local_connections(self, message_text: str):
        """Helper to send text payload to all locally connected WebSocket clients."""
        disconnected = []
        message_tenant = _message_tenant(message_text)
        for client_id, connection in list(self.active_connections.items()):
            wanted = self.connection_tenants.get(client_id)
            if wanted is not None and message_tenant != wanted:
                continue                                   # another tenant's call (or one we cannot place): not theirs
            try:
                await connection.send_text(message_text)
            except Exception as e:
                logger.warning(f"Failed to send to client {client_id}: {e}")
                disconnected.append(client_id)
        for client_id in disconnected:
            await self.disconnect(client_id)

    async def connect(self, websocket: WebSocket, client_id: str = None, tenant_id: Optional[int] = None):
        """Accept and store a WebSocket connection"""
        if client_id is None:
            client_id = str(id(websocket))

        self.active_connections[client_id] = websocket
        self.connection_tenants[client_id] = tenant_id
        logger.info(f"Dashboard client connected: {client_id} (local total: {len(self.active_connections)})")
        
        # Start listener task on first connection
        if self.listen_task is None:
            self.listen_task = asyncio.create_task(self._listen_to_redis())

    async def disconnect(self, client_id: str = None, websocket: WebSocket = None):
        """Remove a WebSocket connection"""
        if websocket and not client_id:
            client_id = str(id(websocket))

        if str(client_id) in self.active_connections:
            del self.active_connections[str(client_id)]
            self.connection_tenants.pop(str(client_id), None)
            logger.info(f"Dashboard client disconnected: {client_id} (local total: {len(self.active_connections)})")

    async def broadcast(self, data: dict):
        """Publish data to Redis and direct local WebSocket clients."""
        if multi_tenant_enabled() and "tenant_id" not in data:
            data = {**data, "tenant_id": await _tenant_of_event(data)}          # tag it so each viewer only gets their own
        message = json.dumps(data, default=str)
        # Always send directly to local active connections
        await self._send_to_local_connections(message)
        # Try publishing to Redis for cross-worker scaling
        try:
            await self.redis_client.publish("dashboard_events", message)
        except Exception:
            pass

    async def send_to(self, client_id: str, data: dict):
        """Send data to a specific local client (used sparingly)"""
        if str(client_id) in self.active_connections:
            try:
                message = json.dumps(data, default=str)
                await self.active_connections[str(client_id)].send_text(message)
            except Exception as e:
                logger.warning(f"Failed to send to client {client_id}: {e}")
                await self.disconnect(client_id)

    @property
    def client_count(self) -> int:
        return len(self.active_connections)


# Singleton instance used by webhooks and main app
dashboard_manager = ConnectionManager()
