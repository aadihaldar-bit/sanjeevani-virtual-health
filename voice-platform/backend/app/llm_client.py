import os
import json
import logging
from typing import List
from pydantic import BaseModel, Field

from .ai_services import LLMEngineFactory

logger = logging.getLogger(__name__)

class CallAnalysis(BaseModel):
    sentiment: str = Field(description="positive | neutral | negative")
    sentiment_score: int = Field(ge=0, le=100)
    customer_emotion: str
    escalation_risk: str = Field(description="low | medium | high")
    intent: str
    key_issues: List[str]
    summary: str
    confidence: int = Field(ge=0, le=100)

class LLMClient:
    """Wrapper client using the modular LLMEngineFactory"""
    def __init__(self):
        # We don't initialize the engine until needed, to ensure env vars are loaded
        pass
        
    async def test_connection(self) -> dict:
        try:
            engine = LLMEngineFactory.get_engine()
            response = await engine.generate(
                prompt="Respond with a single word: working",
                history=[],
                system_prompt="You are a helpful assistant."
            )
            if response:
                return {"success": True, "error": None}
            return {"success": False, "error": "No response"}
        except Exception as e:
            return {"success": False, "error": str(e)}

llm_client = LLMClient()

def _normalize_analysis(raw: dict) -> dict:
    """Normalize LLM JSON output to match CallAnalysis field names regardless of casing/aliases."""
    # Map any alias the LLM might use → canonical field name
    aliases = {
        "sentimentscore": "sentiment_score",
        "sentiment_score": "sentiment_score",
        "customeremotion": "customer_emotion",
        "customer_emotion": "customer_emotion",
        "escalationrisk": "escalation_risk",
        "escalation_risk": "escalation_risk",
        "keyissues": "key_issues",
        "key_issues": "key_issues",
    }
    normalized = {}
    for k, v in raw.items():
        # strip underscores and lowercase to match aliases
        lookup = k.lower().replace("_", "").replace(" ", "")
        canonical = aliases.get(lookup, k)
        normalized[canonical] = v

    # Clamp integer scores to 0-100
    for score_field in ("sentiment_score", "confidence"):
        if score_field in normalized:
            try:
                normalized[score_field] = max(0, min(100, int(normalized[score_field])))
            except (ValueError, TypeError):
                normalized[score_field] = 50

    # Normalize sentiment to exact enum values
    sent = str(normalized.get("sentiment", "neutral")).lower()
    if "pos" in sent:
        normalized["sentiment"] = "positive"
    elif "neg" in sent:
        normalized["sentiment"] = "negative"
    else:
        normalized["sentiment"] = "neutral"

    # Normalize escalation_risk to exact enum values
    risk = str(normalized.get("escalation_risk", "low")).lower()
    if "high" in risk or "critical" in risk:
        normalized["escalation_risk"] = "high"
    elif "med" in risk:
        normalized["escalation_risk"] = "medium"
    else:
        normalized["escalation_risk"] = "low"

    # Ensure key_issues is always a list
    ki = normalized.get("key_issues", [])
    if isinstance(ki, str):
        normalized["key_issues"] = [ki] if ki else []
    elif not isinstance(ki, list):
        normalized["key_issues"] = []

    # Fill missing required fields with safe defaults
    normalized.setdefault("sentiment_score", 50)
    normalized.setdefault("customer_emotion", "neutral")
    normalized.setdefault("escalation_risk", "low")
    normalized.setdefault("intent", "general inquiry")
    normalized.setdefault("key_issues", [])
    normalized.setdefault("summary", "")
    normalized.setdefault("confidence", 50)

    return normalized


async def analyze_transcript(transcript_text: str) -> CallAnalysis:
    """
    Analyzes a call transcript using the configured LLM and returns structured JSON output.
    """
    engine = LLMEngineFactory.get_engine()

    system_prompt = """You are a call analytics bot. Analyze the customer service call transcript and return ONLY a valid JSON object with these exact keys:

{
  "sentiment": "positive" or "neutral" or "negative",
  "sentiment_score": integer 0-100,
  "customer_emotion": "string describing emotion e.g. frustrated, calm, anxious",
  "escalation_risk": "low" or "medium" or "high",
  "intent": "short phrase describing why the customer called",
  "key_issues": ["issue1", "issue2"],
  "summary": "1-2 sentence summary under 50 words",
  "confidence": integer 0-100
}

Rules:
- Use EXACTLY the key names shown above (snake_case with underscores).
- Base sentiment on the CUSTOMER tone only, not the agent.
- Return raw JSON only. No markdown, no explanation, no extra text."""

    try:
        response_text = await engine.generate(
            prompt=f"Transcript:\n{transcript_text}",
            history=[],
            system_prompt=system_prompt,
            max_tokens=800
        )

        logger.info(f"[AnalyzeTranscript] Raw LLM response: {response_text[:300]}")

        # Strip markdown fences
        if "```json" in response_text:
            response_text = response_text.split("```json", 1)[1].split("```")[0].strip()
        elif response_text.startswith("```"):
            response_text = response_text.replace("```", "").strip()

        # Extract just the JSON object in case of surrounding text
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start != -1 and end > start:
            response_text = response_text[start:end]

        parsed_json = json.loads(response_text)
        normalized = _normalize_analysis(parsed_json)
        return CallAnalysis(**normalized)

    except Exception as e:
        logger.error(f"Failed to analyze transcript: {e}")
        return CallAnalysis(
            sentiment="neutral",
            sentiment_score=50,
            customer_emotion="unknown",
            escalation_risk="low",
            intent="unknown",
            key_issues=["Analysis failed"],
            summary="Failed to analyze transcript due to an error.",
            confidence=0
        )
