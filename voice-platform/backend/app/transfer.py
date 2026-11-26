"""Where a call is handed to a person.

Each company sets its own handover number (Settings > Team & access). The platform's own supervisor line belongs to the
platform's own agents only: a company that has not set a number never reaches it, so a Vedanta caller who asks for a
person is never put through to STL. With no number the agent offers to take a message instead.
"""
import logging
import re
from typing import Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def clean_number(value: Optional[str]) -> Optional[str]:
    """A phone number in international form, or None when it is empty. Raises ValueError for rubbish."""
    raw = (value or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not raw:
        return None
    if not raw.startswith("+"):
        if raw.isdigit() and len(raw) == 10:
            raw = "+91" + raw                                   # DEFAULT_COUNTRY_CODE, as elsewhere in the platform
        elif raw.isdigit():
            raw = "+" + raw
    if not _E164.match(raw):
        raise ValueError("Enter the number with its country code, for example +919876543210.")
    return raw


async def resolve_transfer_number(tenant_id: Optional[int], node_number: Optional[str] = None) -> Optional[str]:
    """The number to put a caller through to, or None when this company has none (then take a message).

    1. A number set on the agent step itself wins.
    2. The platform's own agents (no tenant, or the platform tenant) use the platform supervisor line.
    3. Any other company uses its own handover number, if it has set one.
    """
    if node_number and str(node_number).strip():
        return str(node_number).strip()
    from .config import get_config
    platform_number = (get_config().telephony.supervisor_transfer_number or "").strip() or None
    if tenant_id is None:
        return platform_number
    try:
        from .database import async_session
        from .models import Tenant
        async with async_session() as db:
            tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    except Exception as exc:                                    # noqa: BLE001  a lookup problem must not route a call wrongly
        logger.warning("[Transfer] tenant lookup failed (%s); not transferring", exc)
        return None
    if tenant is None:
        return None
    if tenant.is_system:
        return platform_number
    own = ((tenant.settings_json or {}).get("handover_number") or "").strip()
    return own or None


def handover_instruction(number: Optional[str]) -> str:
    """The sentence in an agent's instructions about asking for a person, for either case."""
    if number:
        return f"If the caller asks for a human supervisor or live agent, output: TRANSFER_HUMAN: {number}"
    return ("You cannot transfer calls. If the caller asks for a human, politely take their name and a contact number, "
            "tell them a member of the team will call them back, and never output TRANSFER_HUMAN.")
