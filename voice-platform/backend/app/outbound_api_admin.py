"""
Admin endpoints for managing API Clients and API Keys for the Outbound Voice API.
Prefix: /api/admin/api-clients
Authentication: Firebase Auth (internal dashboard admins)
"""
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import APIClient
from .outbound_api_auth import generate_api_key
from .outbound_api_models import CreateAPIClientRequest, APIClientResponse

logger = logging.getLogger(__name__)

outbound_admin_router = APIRouter(prefix="/api/admin/api-clients", tags=["admin-api-clients"])

@outbound_admin_router.post("", response_model=APIClientResponse, status_code=201)
async def create_api_client(
    payload: CreateAPIClientRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Creates a new API client and returns a plaintext API key.
    IMPORTANT: The API key is returned ONCE in the response and cannot be retrieved later.
    """
    plaintext_key, key_hash, key_prefix = generate_api_key()

    client = APIClient(
        client_name=payload.client_name,
        client_company=payload.client_company,
        api_key_hash=key_hash,
        key_prefix=key_prefix,
        rate_limit_per_minute=payload.rate_limit_per_minute or 5,
        daily_call_quota=payload.daily_call_quota or 100,
        monthly_call_quota=payload.monthly_call_quota or 3000,
        callback_url=payload.callback_url,
        is_active=True
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    resp = APIClientResponse(**client.to_dict())
    resp.api_key = plaintext_key
    logger.info(f"[AdminAPI] Created new API client '{client.client_name}' (ID: {client.id}, Prefix: {key_prefix})")
    return resp


@outbound_admin_router.get("", response_model=List[APIClientResponse])
async def list_api_clients(db: AsyncSession = Depends(get_db)):
    """List all registered API clients (without plaintext API keys)."""
    stmt = select(APIClient).order_by(APIClient.id.desc())
    result = await db.execute(stmt)
    clients = result.scalars().all()
    return [APIClientResponse(**c.to_dict()) for c in clients]


@outbound_admin_router.post("/{client_id}/rotate-key", response_model=APIClientResponse)
async def rotate_api_client_key(client_id: int, db: AsyncSession = Depends(get_db)):
    """Rotates the API key for a client. Revokes old key and returns new plaintext key."""
    stmt = select(APIClient).where(APIClient.id == client_id)
    result = await db.execute(stmt)
    client = result.scalars().first()

    if not client:
        raise HTTPException(status_code=404, detail=f"API client ID {client_id} not found")

    plaintext_key, key_hash, key_prefix = generate_api_key()
    client.api_key_hash = key_hash
    client.key_prefix = key_prefix
    await db.commit()
    await db.refresh(client)

    resp = APIClientResponse(**client.to_dict())
    resp.api_key = plaintext_key
    logger.info(f"[AdminAPI] Rotated API key for client '{client.client_name}' (ID: {client.id})")
    return resp


@outbound_admin_router.delete("/{client_id}", status_code=204)
async def delete_api_client(client_id: int, db: AsyncSession = Depends(get_db)):
    """Deletes / revokes an API client."""
    stmt = select(APIClient).where(APIClient.id == client_id)
    result = await db.execute(stmt)
    client = result.scalars().first()

    if not client:
        raise HTTPException(status_code=404, detail=f"API client ID {client_id} not found")

    await db.delete(client)
    await db.commit()
    logger.info(f"[AdminAPI] Deleted API client ID {client_id}")
