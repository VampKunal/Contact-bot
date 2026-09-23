import json
import logging
import re
from typing import List, Dict, Any, Optional
from config import config

logger = logging.getLogger("llm_validator")

SYSTEM_PROMPT = """
You are an expert recruitment and contact-intelligence validator.
Your job is to evaluate a batch of candidate hiring contacts found for tech companies in Delhi/NCR/Noida/Gurgaon.

For each contact in the batch, you must verify:
1. Is this person likely a CURRENT decision maker / hiring-relevant leader (CTO, VP Eng, Engineering Manager, Founder, HR / Talent Acquisition)?
2. Flag STALE / FORMER employees (e.g. snippets mentioning "ex-", "former", "previously at", "left in 2023").
3. Flag TITLE MISMATCHES (e.g. Sales, Marketing, Customer Support, or Interns matched as CTO).
4. Flag NAME COLLISIONS (common names associated with completely different companies).
5. Assess EMAIL PATTERN plausibility.

You MUST respond ONLY with a valid JSON list containing one object per candidate in this exact format:
[
  {
    "name": "Exact Name",
    "valid": true,
    "confidence": 0.85,
    "title_normalized": "Normalized Clean Title (e.g. VP of Engineering)",
    "red_flags": ["list of any red flags, or empty list"],
    "reasoning": "Brief 1-sentence assessment rationale"
  }
]
"""

async def call_groq_llm(prompt: str) -> Optional[str]:
    """Invoke Groq free-tier LLM (Llama 3.3 70B)."""
    if not config.GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not set in configuration.")
        return None
    try:
        from groq import AsyncGroq
        client = AsyncGroq(api_key=config.GROQ_API_KEY)
        response = await client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"} if "llama-3" in config.GROQ_MODEL else None,
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error calling Groq API: {e}")
        return None

async def call_gemini_llm(prompt: str) -> Optional[str]:
    """Invoke Google Gemini free-tier LLM."""
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not set in configuration.")
        return None
    try:
        from google import genai
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        full_prompt = f"{SYSTEM_PROMPT}\n\nCandidate Batch to Validate:\n{prompt}"
        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=full_prompt
        )
        return response.text
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}")
        return None

def clean_json_response(raw_text: str) -> List[Dict[str, Any]]:
    """Extract and parse clean JSON list from LLM output."""
    if not raw_text:
        return []
    try:
        # Strip markdown code blocks if present
        text = raw_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            # If model wrapped list in a key like {"contacts": [...]} or {"candidates": [...]}
            for v in parsed.values():
                if isinstance(v, list):
                    return v
            return [parsed]
    except Exception as e:
        logger.error(f"Failed to parse LLM JSON response: {e}. Raw content: {raw_text[:200]}")
    return []

async def validate_contact_batch(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate a batch of candidate contacts using the configured LLM provider (Groq or Gemini).
    """
    if not candidates:
        return []

    provider = config.LLM_PROVIDER.lower()
    batch_summary = []
    for idx, c in enumerate(candidates):
        batch_summary.append({
            "id": idx,
            "name": c.get("name"),
            "company": c.get("company_name"),
            "domain": c.get("domain"),
            "scraped_title": c.get("title"),
            "email": c.get("email"),
            "email_verified": c.get("email_verified", False),
            "snippet": c.get("raw_snippet", "")
        })

    prompt = f"Candidate Batch to Validate:\n{json.dumps(batch_summary, indent=2)}"

    raw_response = None
    if provider == "groq":
        raw_response = await call_groq_llm(prompt)
    elif provider == "gemini":
        raw_response = await call_gemini_llm(prompt)
    else:
        # Fallback to Groq then Gemini
        raw_response = await call_groq_llm(prompt) or await call_gemini_llm(prompt)

    validated_results = clean_json_response(raw_response) if raw_response else []

    # Map LLM results back to original candidate objects
    enriched_candidates = []
    for idx, original in enumerate(candidates):
        llm_match = None
        # Match by name or index
        if idx < len(validated_results):
            llm_match = validated_results[idx]
        else:
            for item in validated_results:
                if item.get("name", "").lower() == original.get("name", "").lower():
                    llm_match = item
                    break

        if llm_match:
            confidence = float(llm_match.get("confidence", 0.7))
            normalized_title = llm_match.get("title_normalized") or original.get("title")
            red_flags = llm_match.get("red_flags", [])
            reasoning = llm_match.get("reasoning", "")
            if red_flags:
                reasoning = f"Flags: {', '.join(red_flags)}. {reasoning}".strip()

            enriched = {
                **original,
                "title_normalized": normalized_title,
                "llm_confidence": confidence,
                "llm_reasoning": reasoning,
                "status": "pending"  # Always pending for human review
            }
        else:
            # Default fallback if LLM is unavailable
            enriched = {
                **original,
                "title_normalized": original.get("title"),
                "llm_confidence": 0.6 if original.get("email_verified") else 0.4,
                "llm_reasoning": "Standard heuristics validation (LLM response unparsed)",
                "status": "pending"
            }

        enriched_candidates.append(enriched)

    return enriched_candidates
