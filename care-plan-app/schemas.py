import re
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

def normalize_and_validate_e164(phone: str) -> str:
    """
    Validates and standardizes a phone number into strict E.164 format (e.g. +971501234567).
    Must begin with a '+' followed by country code and subscriber digits (total 8-15 digits).
    """
    if not phone or not phone.strip():
        raise ValueError("Mobile number is mandatory for voice bot outbound calling.")
    
    # Strip spaces, hyphens, parentheses
    cleaned = re.sub(r"[\s\-\(\)]", "", phone.strip())
    
    if not cleaned.startswith("+"):
        # If user forgot '+', handle if it starts with digits or country code
        if cleaned.startswith("00"):
            cleaned = "+" + cleaned[2:]
        elif cleaned.startswith("971"):
            cleaned = "+" + cleaned
        elif cleaned.startswith("05"):
            cleaned = "+971" + cleaned[1:]
        else:
            cleaned = "+" + cleaned

    # E.164 standard regex: '+' followed by 8 to 15 digits
    if not re.match(r"^\+[1-9]\d{7,14}$", cleaned):
        raise ValueError(
            f"Invalid phone number format: '{phone}'. Must be in valid E.164 format (e.g. +971501234567 or +919876543210)."
        )
    return cleaned

def validate_iso_dob(dob_str: str) -> str:
    """
    Validates date of birth string in YYYY-MM-DD format and ensures it is a plausible past date.
    """
    if not dob_str or not dob_str.strip():
        raise ValueError("Patient Date of Birth (identity_dob) is mandatory (YYYY-MM-DD) for voice verification.")
    
    cleaned = dob_str.strip()
    try:
        parsed_date = datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Invalid Date of Birth: '{dob_str}'. Must match format YYYY-MM-DD (e.g. 1990-05-14).")
    
    today = date.today()
    if parsed_date >= today:
        raise ValueError(f"Date of Birth must be in the past: {dob_str}")
    if (today.year - parsed_date.year) > 130:
        raise ValueError(f"Date of Birth is out of valid range: {dob_str}")
    
    return cleaned

def calculate_end_date(start_date_str: str, duration_days: int) -> str:
    """
    Calculates end_date = start_date + duration_days in YYYY-MM-DD format.
    """
    try:
        start_d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except Exception:
        start_d = date.today()
    
    duration = max(1, int(duration_days if duration_days else 1))
    end_d = start_d + timedelta(days=duration)
    return end_d.strftime("%Y-%m-%d")


class MedicineItem(BaseModel):
    drug_name: str = Field(..., description="Name of the medicine/molecule (e.g. Metformin)")
    strength: str = Field("Standard", description="Strength / concentration (e.g. 500mg, 10mg)")
    form: str = Field("Tablet", description="Pharmaceutical dosage form (e.g. Tablet, Capsule, Syrup, Inhaler)")
    dose_text: str = Field("1 tablet", description="Human-readable dose amount (e.g. '1 tablet', '4 ml')")
    frequency_per_day: int = Field(1, ge=1, le=12, description="Number of times per day to take (integer)")
    timing: str = Field("As directed", description="Instruction timing (e.g. 'morning and evening after meals')")
    duration_days: int = Field(30, ge=1, description="Treatment course in days (integer)")
    start_date: str = Field(default_factory=lambda: date.today().strftime("%Y-%m-%d"), description="Start date (YYYY-MM-DD)")
    end_date: str = Field(default="", description="Calculated end date (start_date + duration_days)")

    def model_post_init(self, __context: Any) -> None:
        if not self.end_date:
            self.end_date = calculate_end_date(self.start_date, self.duration_days)


class PatientProfile(BaseModel):
    patient_id: str = Field("SNJ-PATIENT", description="Unique Patient Identifier / MRN")
    patient_name: str = Field("Valued Patient", description="Full name of patient")
    mobile_number: str = Field(..., description="E.164 phone number e.g. +971501234567")
    identity_dob: str = Field(..., description="Date of birth YYYY-MM-DD for on-call identity verification")
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    email: Optional[str] = None
    delivery_address: Optional[str] = None
    chronic_conditions: Optional[str] = None
    blood_pressure: Optional[str] = None
    weight_kg: Optional[float] = None
    insurance_tier: Optional[str] = None

    @field_validator("mobile_number")
    @classmethod
    def check_mobile(cls, v: str) -> str:
        return normalize_and_validate_e164(v)

    @field_validator("identity_dob")
    @classmethod
    def check_dob(cls, v: str) -> str:
        return validate_iso_dob(v)


class VoiceBotPushPayload(BaseModel):
    prescription_id: str = Field(..., description="Unique ID for this prescription record")
    patient: PatientProfile = Field(..., description="Patient details with verified E.164 phone and identity_dob")
    patient_history: Optional[Dict[str, Any]] = Field(default=None, description="Patient previous medical conditions, allergies, and past meds")
    medicines: List[MedicineItem] = Field(..., description="Structured list of medicines with calculated end dates")
    clinical_summary: Optional[Dict[str, Any]] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
