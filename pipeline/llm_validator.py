import json
import logging
import re
import asyncio
from typing import List, Dict, Any, Optional
from config import config

logger = logging.getLogger("llm_validator")

SYSTEM_PROMPT = """
You are an expert recruitment and corporate-intelligence researcher.
Your job is to evaluate candidate technical leadership contacts for tech companies in India.

For each contact in the batch, you must verify:
1. Is this contact a relevant engineering / technical hiring leader (CTO, VP Eng, Engineering Manager, Founder, Co-Founder, Tech Lead, Head of Engineering, HR / Talent Acquisition Lead)?
2. Mark valid leaders as is_relevant_decision_maker: true.
3. Flag STALE / FORMER employees (e.g. snippets mentioning "ex-", "former", "past:").
4. Flag TITLE MISMATCHES (e.g. Sales, Marketing, Customer Support, Legal).

Respond ONLY with a valid JSON array:
[
  {
    "id": 0,
    "name": "Exact Name",
    "is_relevant_decision_maker": true,
    "is_stale_or_former": false,
    "llm_score": 0.85,
    "title_normalized": "Normalized Clean Title (e.g. VP of Engineering)",
    "red_flags": [],
    "reasoning": "Brief 1-sentence assessment rationale"
  }
]
"""

_llm_semaphore = asyncio.Semaphore(1)

async def call_groq_llm(prompt: str, system_prompt: Optional[str] = None, max_retries: int = 3) -> Optional[str]:
    """Invoke Groq free-tier LLM with concurrency pacing and retry handling."""
    if not config.GROQ_API_KEY:
        logger.debug("GROQ_API_KEY is not set in configuration.")
        return None
    
    from groq import AsyncGroq
    client = AsyncGroq(api_key=config.GROQ_API_KEY)
    sys_prompt = system_prompt or SYSTEM_PROMPT

    models_to_try = [config.GROQ_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    
    async with _llm_semaphore:
        for attempt in range(max_retries):
            model = models_to_try[attempt % len(models_to_try)]
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=900,
                    timeout=15
                )
                msg = response.choices[0].message
                content = msg.content or getattr(msg, "reasoning", None)
                if content and not content.lower().startswith("i'm sorry") and not content.lower().startswith("i cannot"):
                    await asyncio.sleep(0.5)  # Smooth rate pacing
                    return content
            except Exception as e:
                logger.warning(f"Groq API attempt {attempt+1} with {model} failed: {e}")
                await asyncio.sleep(1.0 * (attempt + 1))

    return None

async def call_unified_llm(prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
    """Unified router directing LLM queries with pacing."""
    return await call_groq_llm(prompt, system_prompt=system_prompt)

def clean_json_response(raw_text: str) -> List[Dict[str, Any]]:
    """Extract and parse clean JSON list from LLM output with auto-repair for trailing partial objects."""
    if not raw_text:
        return []
    try:
        text = raw_text.strip()
        # Remove any reasoning/thinking tags
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        
        # Strip markdown code blocks
        if "```json" in text:
            match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        elif "```" in text:
            match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        # Find first JSON array or object
        json_match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if json_match:
            text = json_match.group(1).strip()

        try:
            parsed = json.loads(text)
        except Exception:
            # Attempt repair on truncated JSON list
            if text.startswith("[") and not text.endswith("]"):
                last_brace = text.rfind("}")
                if last_brace != -1:
                    repaired = text[:last_brace+1] + "]"
                    parsed = json.loads(repaired)
                else:
                    return []
            else:
                return []

        if isinstance(parsed, list):
            return parsed
        elif isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return v
            return [parsed]
    except Exception as e:
        logger.debug(f"Failed to parse LLM JSON response: {e}")
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
    Fast, cheap LLM filter executed in small chunks (10 candidates) to prevent token limit overflows.
    Evaluates candidate relevance, title normalization, and stale status.
    """
    if not candidates:
        return []

    CHUNK_SIZE = 10
    enriched_candidates = []

    for chunk_start in range(0, len(candidates), CHUNK_SIZE):
        chunk = candidates[chunk_start:chunk_start + CHUNK_SIZE]
        batch_summary = []
        for idx, c in enumerate(chunk):
            batch_summary.append({
                "id": idx,
                "name": c.get("name"),
                "company": c.get("company_name"),
                "domain": c.get("domain"),
                "scraped_title": c.get("title"),
                "source": c.get("source", "scraped"),
                "snippet": c.get("raw_snippet", "")
            })

        prompt = f"Candidate Batch to Validate (India Tech Startups):\n{json.dumps(batch_summary, indent=2)}"

        raw_response = await call_unified_llm(prompt)
        validated_results = clean_json_response(raw_response) if raw_response else []

        for idx, original in enumerate(chunk):
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
                raw_llm_score = float(llm_match.get("llm_score", 0.75))
                
                # Pass if deemed relevant OR if score is >= 0.40 and not explicitly a stale/former employee
                passed = (is_relevant or raw_llm_score >= 0.40) and not is_stale

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
                    "is_llm_passed": passed,
                    "raw_llm_score": raw_llm_score,
                    "red_flags": red_flags,
                    "llm_reasoning": reasoning,
                    "status": "pending"
                }
            else:
                # Heuristic fallback if LLM chunk temporarily timed out
                title_lower = (original.get("title") or "").lower()
                from pipeline.contact_discovery import is_hiring_relevant_title
                passed = is_hiring_relevant_title(title_lower)
                enriched = {
                    **original,
                    "title_normalized": original.get("title"),
                    "is_llm_passed": passed,
                    "raw_llm_score": 0.6 if passed else 0.3,
                    "red_flags": [] if passed else ["title relevance unverified"],
                    "llm_reasoning": "Heuristic title match",
                    "status": "pending"
                }

            enriched_candidates.append(enriched)

    return enriched_candidates
