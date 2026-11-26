import httpx
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from config import settings
from db import db_manager

async def push_prescription_to_voicebot(
    prescription_id: str,
    payload: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Pushes a parsed prescription and verified patient data to the Voice Bot platform
    at POST /api/v1/prescriptions with server-to-server API Key authentication.
    Updates MongoDB push_status accordingly ('PUSHED' or 'FAILED').
    """
    endpoint_url = settings.VOICE_BOT_ENDPOINT_URL
    api_key = settings.VOICE_BOT_API_KEY

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Sanjeevani-HealthOS/1.0"
    }

    print("\n" + "="*80)
    print(f" [VOICE BOT PUSH] Dispatching prescription '{prescription_id}' to: {endpoint_url}")
    print("="*80)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(endpoint_url, json=payload, headers=headers)
            
            if response.is_success:
                resp_json = response.json() if "application/json" in response.headers.get("content-type", "") else {"text": response.text}
                print(f" [VOICE BOT PUSH SUCCESS] Status {response.status_code} | Response: {resp_json}")
                
                # Update status in MongoDB
                db_manager.update_prescription_push_status(
                    prescription_id=prescription_id,
                    status="PUSHED",
                    response_data=resp_json
                )
                return {
                    "success": True,
                    "status_code": response.status_code,
                    "endpoint": endpoint_url,
                    "response": resp_json
                }
            else:
                error_detail = f"HTTP {response.status_code}: {response.text}"
                print(f" [!] [VOICE BOT PUSH FAILED] {error_detail}")
                db_manager.update_prescription_push_status(
                    prescription_id=prescription_id,
                    status="FAILED",
                    error_msg=error_detail
                )
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "endpoint": endpoint_url,
                    "error": error_detail
                }

    except Exception as e:
        error_msg = f"Network/Connection error pushing to {endpoint_url}: {str(e)}"
        print(f" [!] [VOICE BOT PUSH ERROR] {error_msg}")
        db_manager.update_prescription_push_status(
            prescription_id=prescription_id,
            status="FAILED",
            error_msg=error_msg
        )
        return {
            "success": False,
            "status_code": 500,
            "endpoint": endpoint_url,
            "error": error_msg
        }
