"""
License Manager Stub for Stld Voice Bot Backend.
Prevents startup errors when external license server is disabled or not present.
"""
import logging
from typing import Optional, Dict, Any, Tuple, List

logger = logging.getLogger(__name__)

class LicenseManager:
    def __init__(self):
        self.is_active = True
        self.max_calls = 10000
        self.consumed_calls = 24
        self.max_hours = 500.0
        self.license_key = "STL-DEV-KEY-PERPETUAL"
        self.company = "Brother International / STL Digital"
        self.email = "admin@stldigitaltech.com"

    def check_and_consume_call(self, agent_id: Optional[str] = None) -> Tuple[bool, str]:
        """Check if call volume is within allowed limits."""
        if not self.is_active:
            return False, "License is inactive"
        if self.consumed_calls >= self.max_calls:
            return False, "Maximum call quota reached"
        self.consumed_calls += 1
        return True, "Call quota check passed"

    async def activate(self, license_key: str, email: str) -> Dict[str, Any]:
        """Activate a license key."""
        self.license_key = license_key
        self.email = email
        self.is_active = True
        return self.get_status()

    async def register_client(
        self,
        name: str,
        company: str,
        email: str,
        product: str = "STL Voice Bot",
        project_id: str = "VOICE-BOT-PROD",
        license_type: str = "MACHINE",
        machine_node: str = "N/A",
        machine_sn: str = "N/A"
    ) -> Dict[str, Any]:
        """Register a client."""
        self.company = company
        self.email = email
        return {
            "status": "APPROVED",
            "request_id": "REQ-MOCK-001",
            "license_key": "STL-DEV-KEY-PERPETUAL",
            "message": "Registration auto-approved"
        }

    async def check_registration_status(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Check registration status."""
        return {
            "status": "APPROVED",
            "request_id": request_id or "REQ-MOCK-001",
            "license_key": "STL-DEV-KEY-PERPETUAL"
        }

    async def refresh_from_server(self) -> None:
        """Refresh license data."""
        pass

    def get_status(self) -> Dict[str, Any]:
        """Return status dictionary."""
        return {
            "active": self.is_active,
            "license_key": self.license_key,
            "company": self.company,
            "email": self.email,
            "consumed_calls": self.consumed_calls,
            "max_calls": self.max_calls,
            "remaining_calls": max(0, self.max_calls - self.consumed_calls),
            "max_hours": self.max_hours,
            "status": "VALID" if self.is_active else "EXPIRED"
        }

    def deactivate(self) -> Dict[str, Any]:
        """Deactivate license."""
        self.is_active = False
        return {"status": "ok", "message": "License deactivated"}

    def reset_usage(self) -> Dict[str, Any]:
        """Reset call counter."""
        self.consumed_calls = 0
        return {"status": "ok", "message": "Call counter reset"}

    async def update_constraints(
        self,
        max_calls: Optional[int] = None,
        max_hours: Optional[float] = None,
        expiry: Optional[str] = None,
        allowed_agents: Optional[List[str]] = None,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update constraints."""
        if max_calls is not None:
            self.max_calls = max_calls
        if max_hours is not None:
            self.max_hours = max_hours
        if status is not None:
            self.is_active = (status.upper() == "VALID" or status.upper() == "ACTIVE")
        return self.get_status()

license_manager = LicenseManager()
