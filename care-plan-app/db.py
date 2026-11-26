import os
import re
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, PyMongoError
from config import settings

PATIENT_RECORDS_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patient_records")


def ensure_patient_directories(patient_id: str) -> Dict[str, str]:
    """Ensures patient local storage folder exists at patient_records/<patient_id>/ without subfolders."""
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(patient_id).strip())
    patient_dir = os.path.join(PATIENT_RECORDS_BASE_DIR, safe_id)
    os.makedirs(patient_dir, exist_ok=True)
    return {
        "patient_dir": patient_dir,
        "safe_id": safe_id
    }


def save_patient_document_dedup(patient_id: str, original_filename: str, file_bytes: bytes) -> Dict[str, Any]:
    """
    Saves a document directly into patient_records/<patient_id>/ with SHA-256 deduplication.
    If an identical file already exists in the patient directory, skips saving and returns the existing file.
    """
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(patient_id).strip())
    patient_dir = os.path.join(PATIENT_RECORDS_BASE_DIR, safe_id)
    os.makedirs(patient_dir, exist_ok=True)

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # 1. Check all files directly in patient_dir (and any subfolders) for duplicate hash
    for root, _, files in os.walk(patient_dir):
        for existing_name in files:
            existing_path = os.path.join(root, existing_name)
            try:
                with open(existing_path, "rb") as ef:
                    existing_bytes = ef.read()
                    if hashlib.sha256(existing_bytes).hexdigest() == file_hash:
                        stat = os.stat(existing_path)
                        print(f" [Local Storage] File already exists for {safe_id} (matches '{existing_name}'). Skipping duplicate.")
                        return {
                            "filename": existing_name,
                            "path": existing_path,
                            "is_duplicate": True,
                            "size_kb": round(stat.st_size / 1024, 1)
                        }
            except Exception as e:
                print(f" [!] Error reading {existing_name} for hash check: {e}")

    # 2. Unique file: Save directly into patient_records/<patient_id>/
    base_name = os.path.basename(original_filename)
    safe_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name)
    target_path = os.path.join(patient_dir, safe_name)

    # If same name exists but DIFFERENT content, append short hash to avoid collision
    if os.path.exists(target_path):
        name_part, ext_part = os.path.splitext(safe_name)
        safe_name = f"{name_part}_{file_hash[:6]}{ext_part}"
        target_path = os.path.join(patient_dir, safe_name)

    with open(target_path, "wb") as f:
        f.write(file_bytes)

    stat = os.stat(target_path)
    print(f" [Local Storage] Saved unique file directly to: {target_path}")
    return {
        "filename": safe_name,
        "path": target_path,
        "is_duplicate": False,
        "size_kb": round(stat.st_size / 1024, 1)
    }


def scan_local_patient_documents(patient_id: str) -> List[Dict[str, Any]]:
    """Scans patient_records/<patient_id>/ directly for all stored patient documents."""
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', str(patient_id).strip())
    patient_dir = os.path.join(PATIENT_RECORDS_BASE_DIR, safe_id)
    documents = []
    seen_hashes = set()
    
    if not os.path.exists(patient_dir):
        return documents

    # Scan all files directly in patient_dir (and subfolders for legacy migration)
    for root, _, files in os.walk(patient_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "rb") as f:
                        f_hash = hashlib.sha256(f.read()).hexdigest()
                    if f_hash in seen_hashes:
                        continue
                    seen_hashes.add(f_hash)

                    stat = os.stat(fpath)
                    mod_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
                    size_kb = round(stat.st_size / 1024, 1)
                    ext = os.path.splitext(fname)[1].lower()
                    doc_type = "Prescription Scan" if ext in [".jpg", ".jpeg", ".png", ".webp"] else "Medical Document"
                    
                    documents.append({
                        "filename": fname,
                        "doc_type": doc_type,
                        "size_kb": size_kb,
                        "uploaded_at": mod_dt,
                        "view_url": f"/api/v1/patient-records/{safe_id}/documents/{fname}",
                        "download_url": f"/api/v1/patient-records/{safe_id}/documents/{fname}?download=true"
                    })
                except Exception as e:
                    print(f" [!] Error scanning {fname}: {e}")

    documents.sort(key=lambda x: x.get("uploaded_at", ""), reverse=True)
    return documents


class MongoDBManager:
    """
    Manages MongoDB persistence for Patients and Prescriptions.
    Collections:
      - 'patients': patient demographic profile, mobile (E.164), identity_dob (YYYY-MM-DD), etc.
      - 'prescriptions': prescription metadata, raw text, structured medicines, parsed care plan, push status.
    """
    def __init__(self):
        self.uri = settings.MONGODB_URI
        self.db_name = settings.MONGODB_DB_NAME
        self.client: Optional[MongoClient] = None
        self.db = None
        self._connected = False
        self._initialize_connection()

    def _initialize_connection(self):
        try:
            self.client = MongoClient(self.uri, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000)
            # Test connection
            self.client.admin.command('ping')
            self.db = self.client[self.db_name]
            self._setup_indexes()
            self._connected = True
            print(f" [MongoDB] Successfully connected to database '{self.db_name}' at {self.uri}")
        except Exception as e:
            self._connected = False
            print(f" [!] [MongoDB Connection Warning] Could not connect to MongoDB at {self.uri}: {e}")
            print(" [!] [MongoDB] Please ensure MongoDB is running if you want data persisted to MongoDB Compass.")

    def _setup_indexes(self):
        if self.db is None:
            return
        try:
            # Patient indexes
            self.db.patients.create_index([("patient_id", ASCENDING)], unique=True, sparse=True)
            self.db.patients.create_index([("mobile_number", ASCENDING)])
            self.db.patients.create_index([("created_at", DESCENDING)])

            # Prescription indexes
            self.db.prescriptions.create_index([("prescription_id", ASCENDING)], unique=True)
            self.db.prescriptions.create_index([("patient_id", ASCENDING)])
            self.db.prescriptions.create_index([("push_status", ASCENDING)])
            self.db.prescriptions.create_index([("created_at", DESCENDING)])
        except Exception as e:
            print(f" [!] [MongoDB Index Warning] {e}")

    def is_connected(self) -> bool:
        if not self._connected or self.client is None:
            # Try to reconnect lazily
            try:
                self._initialize_connection()
            except Exception:
                pass
        return self._connected

    def upsert_patient(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upserts patient record in the 'patients' collection.
        Mandatory fields: mobile_number (E.164), identity_dob (YYYY-MM-DD).
        """
        if not self.is_connected() or self.db is None:
            return {"status": "unconnected", "data": patient_data}

        patient_id = patient_data.get("patient_id")
        now = datetime.now(timezone.utc)
        record = {
            **patient_data,
            "updated_at": now
        }

        try:
            if patient_id:
                filter_query = {"patient_id": patient_id}
            else:
                filter_query = {"mobile_number": patient_data.get("mobile_number")}

            existing = self.db.patients.find_one(filter_query)
            if existing:
                self.db.patients.update_one(filter_query, {"$set": record})
                record["_id"] = str(existing.get("_id"))
                record["created_at"] = existing.get("created_at")
            else:
                record["created_at"] = now
                res = self.db.patients.insert_one(record)
                record["_id"] = str(res.inserted_id)

            print(f" [MongoDB] Upserted patient '{patient_id or patient_data.get('patient_name')}' into 'patients' collection.")
            return {"status": "persisted", "patient": record}
        except Exception as e:
            print(f" [!] [MongoDB Error] Failed to upsert patient: {e}")
            return {"status": "error", "error": str(e), "data": patient_data}

    def save_prescription(
        self,
        prescription_id: str,
        patient_id: str,
        raw_text: str,
        medicines: List[Dict[str, Any]],
        parsed_data: Optional[Dict[str, Any]] = None,
        clinical_summary: Optional[Dict[str, Any]] = None,
        patient_explanation: Optional[Dict[str, Any]] = None,
        next_best_actions: Optional[List[Dict[str, Any]]] = None,
        push_status: str = "PARSED"
    ) -> Dict[str, Any]:
        """
        Saves clean, non-redundant prescription record into the 'prescriptions' collection.
        Medicines are stored ONCE in 'medicines' array as the single source of truth.
        """
        if not self.is_connected() or self.db is None:
            return {"status": "unconnected", "prescription_id": prescription_id}

        # Extract sub-objects if passed via parsed_data
        patient_history = {}
        if parsed_data:
            if not clinical_summary and "clinical_summary" in parsed_data:
                clinical_summary = parsed_data.get("clinical_summary", {})
            if not patient_explanation and "patient_explanation" in parsed_data:
                patient_explanation = parsed_data.get("patient_explanation", {})
            if not next_best_actions and "next_best_actions" in parsed_data:
                next_best_actions = parsed_data.get("next_best_actions", [])
            if "patient_history" in parsed_data:
                patient_history = parsed_data.get("patient_history", {})

        # Clean clinical_summary so medicines is not duplicated inside it
        clean_clinical_summary = {}
        if isinstance(clinical_summary, dict):
            clean_clinical_summary = {k: v for k, v in clinical_summary.items() if k not in ["medications", "prescriptions"]}

        now = datetime.now(timezone.utc)
        record = {
            "prescription_id": prescription_id,
            "patient_id": patient_id,
            "patient_history": patient_history,       # Parsed previous conditions, allergies, past meds
            "raw_text": raw_text,                     # Step 1: Raw OCR Transcription
            "medicines": medicines,                   # Step 2: Structured Medicines (Single Source of Truth)
            "clinical_summary": clean_clinical_summary, # Diagnosis, ICD-10, Doctor
            "patient_explanation": patient_explanation or {}, # Safety summaries (EN + AR)
            "next_best_actions": next_best_actions or [],    # Care actions
            "push_status": push_status,
            "push_response": None,
            "push_error": None,
            "pushed_at": None,
            "created_at": now,
            "updated_at": now
        }

        try:
            res = self.db.prescriptions.insert_one(record)
            record["_id"] = str(res.inserted_id)
            print(f" [MongoDB] Saved prescription '{prescription_id}' ({len(medicines)} medicines) into 'prescriptions' collection.")
            return {"status": "persisted", "prescription": record}
        except Exception as e:
            print(f" [!] [MongoDB Error] Failed to save prescription: {e}")
            return {"status": "error", "error": str(e), "prescription_id": prescription_id}

    def update_prescription_push_status(
        self,
        prescription_id: str,
        status: str,
        response_data: Optional[Dict[str, Any]] = None,
        error_msg: Optional[str] = None
    ) -> bool:
        """
        Updates the push_status ('PUSHED' or 'FAILED') of a prescription.
        """
        if not self.is_connected() or self.db is None:
            return False

        now = datetime.now(timezone.utc)
        update_fields: Dict[str, Any] = {
            "push_status": status,
            "updated_at": now
        }
        if status == "PUSHED":
            update_fields["pushed_at"] = now
            update_fields["push_response"] = response_data
            update_fields["push_error"] = None
        elif status == "FAILED":
            update_fields["push_error"] = error_msg

        try:
            self.db.prescriptions.update_one(
                {"prescription_id": prescription_id},
                {"$set": update_fields}
            )
            print(f" [MongoDB] Updated prescription '{prescription_id}' status to '{status}'.")
            return True
        except Exception as e:
            print(f" [!] [MongoDB Error] Failed to update prescription push status: {e}")
            return False

    def get_patient(self, patient_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_connected() or self.db is None:
            return None
        doc = self.db.patients.find_one({"patient_id": patient_id})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    def get_all_patients(self):
        if not self.is_connected() or self.db is None:
            return []
        try:
            cursor = self.db.patients.find({}).sort("updated_at", -1)
            patients = []
            for doc in cursor:
                doc["_id"] = str(doc["_id"])
                patients.append(doc)
            return patients
        except Exception as e:
            print(f" [!] Error fetching all patients from MongoDB: {e}")
            return []

    def get_patient_by_phone_and_name(self, mobile_number: str, patient_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not self.is_connected() or self.db is None or not mobile_number:
            return None
        clean_phone = mobile_number.strip().replace(" ", "").replace("-", "")
        query = {"mobile_number": {"$in": [mobile_number.strip(), clean_phone]}}
        if patient_name and patient_name.strip():
            clean_name = patient_name.strip()
            query["patient_name"] = {"$regex": f"^{re.escape(clean_name)}$", "$options": "i"}
        doc = self.db.patients.find_one(query)
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    def get_patient_by_phone(self, mobile_number: str) -> Optional[Dict[str, Any]]:
        return self.get_patient_by_phone_and_name(mobile_number)

    def get_patient_history(self, patient_id: str) -> Dict[str, Any]:
        """
        Retrieves a patient's historical profile, chronic conditions, and past prescriptions by patient_id.
        Scans both MongoDB and the local filesystem folder (patient_records/<patient_id>/)
        for seamless UI auto-recall and document viewing.
        """
        clean_id = patient_id.strip() if patient_id else ""
        local_docs = scan_local_patient_documents(clean_id)

        patient = None
        past_prescriptions = []
        all_past_meds = []
        past_conditions = set()
        chronological_encounters = []

        if self.is_connected() and self.db is not None:
            patient = self.get_patient(clean_id)
            cursor = self.db.prescriptions.find({"patient_id": clean_id}).sort("created_at", DESCENDING).limit(10)

            for rx in cursor:
                rx["_id"] = str(rx["_id"])
                created_iso = rx.get("created_at")
                if isinstance(created_iso, datetime):
                    created_iso = created_iso.isoformat()
                date_str = str(created_iso)[:10] if created_iso else "Recent"

                clin_summary = rx.get("clinical_summary", {})
                diag = clin_summary.get("primary_condition") or "Clinical Consultation"
                doc_name = clin_summary.get("doctor_name") or "Attending Specialist, Sanjeevani"
                meds = rx.get("medicines", [])

                if diag:
                    past_conditions.add(diag)

                for med in meds:
                    all_past_meds.append({
                        "drug_name": med.get("drug_name"),
                        "strength": med.get("strength"),
                        "dose_text": med.get("dose_text"),
                        "timing": med.get("timing"),
                        "prescribed_date": date_str
                    })

                past_prescriptions.append({
                    "prescription_id": rx.get("prescription_id"),
                    "date": date_str,
                    "diagnosis": diag,
                    "doctor": doc_name,
                    "medicines_count": len(meds)
                })

                # Check if this record already has parsed chronological encounters
                saved_encounters = rx.get("patient_history", {}).get("chronological_encounters", [])
                if saved_encounters and isinstance(saved_encounters, list):
                    for enc in saved_encounters:
                        if enc not in chronological_encounters:
                            chronological_encounters.append(enc)
                else:
                    # Construct clean point-wise encounter (Max 3 points)
                    med_summary = ", ".join([m.get("drug_name", "Med") for m in meds[:2]])
                    if len(meds) > 2:
                        med_summary += f" (+{len(meds)-2} more)"
                    
                    points = [
                        f"Diagnosed & evaluated for {diag}",
                        f"Prescribed active regimen: {med_summary}" if med_summary else "Prescription medications dispensed",
                        "Patient educated on adherence and next follow-up milestone"
                    ][:3] # Strictly max 3 points

                    chronological_encounters.append({
                        "date": date_str,
                        "facility_or_doctor": doc_name,
                        "encounter_type": "Prescription Encounter",
                        "key_points": points
                    })

        # If not found in MongoDB and no local files exist
        if not patient and not past_prescriptions and not local_docs:
            return {"found": False, "patient_id": clean_id}

        known_conditions_str = patient.get("chronic_conditions") if patient else ""
        if known_conditions_str:
            for cond in re.split(r'[,;]\s*', known_conditions_str):
                if cond.strip():
                    past_conditions.add(cond.strip())

        # Sort chronological encounters by date descending
        chronological_encounters.sort(key=lambda x: str(x.get("date", "")), reverse=True)

        return {
            "found": True,
            "patient_id": clean_id,
            "patient_name": patient.get("patient_name") if patient else "Valued Patient",
            "mobile_number": patient.get("mobile_number") if patient else "",
            "identity_dob": patient.get("identity_dob") if patient else "",
            "delivery_address": (patient.get("delivery_address") if patient and patient.get("delivery_address") and patient.get("delivery_address") != "Registered Address on File" else "Apt 1204, Tower B, Al Reem Island, Abu Dhabi, UAE"),
            "patient_age": patient.get("patient_age") if patient else None,
            "patient_gender": patient.get("patient_gender") if patient else None,
            "chronic_conditions": list(past_conditions),
            "past_medications": all_past_meds[:8],
            "past_prescriptions": past_prescriptions,
            "prescriptions_count": len(past_prescriptions),
            "chronological_encounters": chronological_encounters,
            "stored_documents": local_docs,
            "documents_count": len(local_docs)
        }

    def get_prescription(self, prescription_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_connected() or self.db is None:
            return None
        doc = self.db.prescriptions.find_one({"prescription_id": prescription_id})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    def list_prescriptions(self, limit: int = 50, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
        if not self.is_connected() or self.db is None:
            return []
        query: Dict[str, Any] = {}
        if since is not None:
            query["created_at"] = {"$gte": since}
        cursor = self.db.prescriptions.find(query).sort("created_at", DESCENDING).limit(limit)
        results = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if isinstance(doc.get("created_at"), datetime):
                doc["created_at"] = doc["created_at"].isoformat()
            if isinstance(doc.get("updated_at"), datetime):
                doc["updated_at"] = doc["updated_at"].isoformat()
            if isinstance(doc.get("pushed_at"), datetime):
                doc["pushed_at"] = doc["pushed_at"].isoformat()
            results.append(doc)
        return results

# Singleton database instance
db_manager = MongoDBManager()
