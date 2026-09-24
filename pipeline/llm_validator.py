import json
import logging
import re
import asyncio
from typing import List, Dict, Any, Optional
from config import config

logger = logging.getLogger("llm_validator")

SYSTEM_PROMPT = """
You are an expert recruitment and contact-intelligence validator.
Your job is to evaluate a batch of candidate hiring contacts found for tech companies in Delhi/NCR/Noida/Gurgaon.

For each contact in the batch, you must verify:
1. Is this person likely a CURRENT decision maker / hiring-relevant leader (CTO, VP Eng, Engineering Manager, Founder/Co-Founder, Tech Lead, HR / Talent Acquisition)?
2. Flag STALE / FORMER employees (e.g. snippets mentioning "ex-", "former", "previously at", "left in 2023", "past:").
3. Flag TITLE MISMATCHES (e.g. Sales, Marketing, Customer Support, Legal, Finance, or Interns matched as CTO).
4. Flag NAME COLLISIONS (common names associated with completely different companies).
5. Assess whether the candidate's snippet/title indicates active involvement with the target company.

You MUST respond ONLY with a valid JSON list containing one object per candidate in this exact format:
[
  {
    "id": 0,
    "name": "Exact Name",
    "is_relevant_decision_maker": true,
    "is_stale_or_former": false,
    "llm_score": 0.85,
    "title_normalized": "Normalized Clean Title (e.g. VP of Engineering)",
    "red_flags": ["list of any red flags, or empty list"],
    "reasoning": "Brief 1-sentence assessment rationale"
  }
]
"""

async def call_groq_llm(prompt: str, max_retries: int = 3) -> Optional[str]:
    """Invoke Groq free-tier LLM with exponential backoff retries."""
    if not config.GROQ_API_KEY:
        logger.debug("GROQ_API_KEY is not set in configuration.")
        return None
    
    from groq import AsyncGroq
    client = AsyncGroq(api_key=config.GROQ_API_KEY)
    
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"} if "llama-3" in config.GROQ_MODEL else None,
                temperature=0.1,
                timeout=20
            )
            return response.choices[0].message.content
        except Exception as e:
            wait_sec = (2 ** attempt) + 0.5
            logger.warning(f"Groq API call attempt {attempt+1}/{max_retries} failed: {e}. Retrying in {wait_sec}s...")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_sec)
            else:
                logger.error(f"Groq API call permanently failed after {max_retries} attempts: {e}")
    return None

async def call_gemini_llm(prompt: str, max_retries: int = 3) -> Optional[str]:
    """Invoke Google Gemini free-tier LLM with exponential backoff retries."""
    if not config.GEMINI_API_KEY:
        logger.debug("GEMINI_API_KEY is not set in configuration.")
        return None
    
    from google import genai
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    full_prompt = f"{SYSTEM_PROMPT}\n\nCandidate Batch to Validate:\n{prompt}"

    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=full_prompt
                )
            )
            return response.text
        except Exception as e:
            wait_sec = (2 ** attempt) + 0.5
            logger.warning(f"Gemini API attempt {attempt+1}/{max_retries} failed: {e}. Retrying in {wait_sec}s...")
            if attempt < max_retries - 1:
                await asyncio.sleep(wait_sec)
            else:
                logger.error(f"Gemini API call failed after {max_retries} attempts: {e}")
    return None

def clean_json_response(raw_text: str) -> List[Dict[str, Any]]:
    """Extract and parse clean JSON list from LLM output."""
    if not raw_text:
        return []
    try:
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
            for v in parsed.values():
                if isinstance(v, list):
                    return v
            return [parsed]
    except Exception as e:
        logger.error(f"Failed to parse LLM JSON response: {e}. Raw content: {raw_text[:200]}")
    return []

def calculate_calibrated_confidence(
    llm_relevant: bool,
    llm_score: float,
    red_flags: List[str],
    email_verified: bool,
    source_type: str,
    pattern_match: bool = False
) -> float:
    """
    Compute a calibrated composite confidence score (0.0 to 1.0) combining:
    1. Deterministic email verification (SMTP / MX)
    2. Corporate domain pattern match
    3. Source signal credibility (Team page vs LinkedIn snippet vs Apollo)
    4. LLM relevance, recency, and absence of red flags
    """
    if not llm_relevant:
        return 0.15

    score = 0.0

    # 1. SMTP / Mailbox Verification Signal (Max 0.35)
    if email_verified:
        score += 0.35
    else:
        score += 0.10

    # 2. Company Domain Email Pattern Match (Max 0.20)
    if pattern_match:
        score += 0.20
    else:
        score += 0.05

    # 3. Source Credibility Signal (Max 0.20)
    s = (source_type or "").lower()
    if "scraped_website" in s or "team" in s:
        score += 0.20
    elif "apollo" in s:
        score += 0.18
    elif "linkedin" in s or "google" in s:
        score += 0.14
    else:
        score += 0.08

    # 4. LLM Judged Relevance & Recency (Max 0.25)
    llm_clean = max(0.0, min(1.0, llm_score))
    score += (llm_clean * 0.25)

    # Red flag penalties
    if red_flags:
        score -= min(0.30, len(red_flags) * 0.15)

    return round(max(0.10, min(0.99, score)), 2)

async def validate_contact_batch(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fast, cheap LLM filter executed FIRST before any slow/risky SMTP checks.
    Evaluates candidate relevance, title normalization, and stale status.
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
            "source": c.get("source", "scraped"),
            "snippet": c.get("raw_snippet", "")
        })

    prompt = f"Candidate Batch to Validate (Delhi NCR Tech Startups):\n{json.dumps(batch_summary, indent=2)}"

    raw_response = None
    if provider == "groq":
        raw_response = await call_groq_llm(prompt)
    elif provider == "gemini":
        raw_response = await call_gemini_llm(prompt)
    else:
        raw_response = await call_groq_llm(prompt) or await call_gemini_llm(prompt)

    validated_results = clean_json_response(raw_response) if raw_response else []

    enriched_candidates = []
    for idx, original in enumerate(candidates):
        llm_match = None
        if idx < len(validated_results):
            llm_match = validated_results[idx]
        else:
            for item in validated_results:
                if item.get("name", "").lower() == original.get("name", "").lower():
                    llm_match = item
                    break

        if llm_match:
            is_relevant = llm_match.get("is_relevant_decision_maker", True)
            is_stale = llm_match.get("is_stale_or_former", False)
            if is_stale:
                is_relevant = False

            raw_llm_score = float(llm_match.get("llm_score", 0.75))
            normalized_title = llm_match.get("title_normalized") or original.get("title")
            red_flags = llm_match.get("red_flags", [])
            if is_stale and "stale/former employee" not in red_flags:
                red_flags.append("stale/former employee")

            reasoning = llm_match.get("reasoning", "")
            if red_flags:
                reasoning = f"Flags: {', '.join(red_flags)}. {reasoning}".strip()

            enriched = {
                **original,
                "title_normalized": normalized_title,
                "is_llm_passed": is_relevant and not is_stale,
                "raw_llm_score": raw_llm_score,
                "red_flags": red_flags,
                "llm_reasoning": reasoning,
                "status": "pending"
            }
        else:
            # Heuristic fallback if LLM is temporarily unreachable
            title_lower = (original.get("title") or "").lower()
            from pipeline.contact_discovery import is_hiring_relevant_title
            passed = is_hiring_relevant_title(title_lower)
            enriched = {
                **original,
                "title_normalized": original.get("title"),
                "is_llm_passed": passed,
                "raw_llm_score": 0.6 if passed else 0.3,
                "red_flags": [] if passed else ["title relevance unverified"],
                "llm_reasoning": "Heuristic title match (LLM unavailable)",
                "status": "pending"
            }

        enriched_candidates.append(enriched)

    return enriched_candidates
