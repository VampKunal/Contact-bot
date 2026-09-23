import asyncio
import os
import sys

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from config import config
from pipeline.company_discovery import clean_domain, infer_tier_and_stack
from pipeline.email_guesser import generate_email_permutations, clean_name_parts, detect_pattern
from pipeline.email_verifier import validate_email_syntax, get_mx_record
from pipeline.llm_validator import clean_json_response, validate_contact_batch
from pipeline.draft_generator import generate_outreach_draft
from pipeline.apollo_client import get_active_apollo_key_index
from database.db import init_db, increment_apollo_credits, get_apollo_pool_status

async def test_all_stages():
    print("==================================================")
    print("   RUNNING FULL PIPELINE MULTI-STAGE TEST SUITE   ")
    print("==================================================")

    # 1. Company Discovery: Domain Cleaning & Tier Inference
    print("\n--- Testing Stage 2: Company Discovery Utilities ---")
    assert clean_domain("https://www.sarvam.ai/about?ref=g") == "sarvam.ai", "Domain cleaner failed"
    assert clean_domain("http://deepmind.google.com:443/") == "deepmind.google.com", "Domain cleaner failed"
    tier, stack = infer_tier_and_stack("Neural Labs AI Gurgaon", ["point_of_interest"])
    assert tier == 1, "AI keyword should classify as Tier 1"
    print(f"[OK] Domain cleaning & Tier inference: Tier {tier}, Stack: '{stack}'")

    # 2. Email Pattern Guesser
    print("\n--- Testing Stage 3: Email Pattern Guesser ---")
    first, last = clean_name_parts("Dr. Kunal Rai, PhD")
    assert (first, last) == ("kunal", "rai"), f"Name clean failed: {(first, last)}"
    permutations = generate_email_permutations("Priya Sharma", "acmetech.ai")
    assert "priya.sharma@acmetech.ai" in permutations, "Permutation generator missing first.last"
    assert "priya@acmetech.ai" in permutations, "Permutation generator missing first"
    detected = detect_pattern("priya.sharma@acmetech.ai", "Priya Sharma")
    assert detected == "first.last", f"Pattern detection failed: {detected}"
    print(f"[OK] Email permutations generated ({len(permutations)} patterns): {permutations[:3]}...")

    # 3. Email Verifier: Syntax & MX lookup
    print("\n--- Testing Stage 3: Email Verifier & DNS Resolution ---")
    assert validate_email_syntax("kunal.rai@gmail.com") is True
    assert validate_email_syntax("invalid-email@") is False
    # Test real MX resolution on a standard domain
    mx_host = await get_mx_record("google.com")
    print(f"[OK] DNS MX Lookup for google.com -> {mx_host}")
    assert mx_host is not None, "MX resolution for google.com failed"

    # 4. LLM Validator: JSON parsing & batch evaluation
    print("\n--- Testing Stage 4: LLM Validation Parsing & Batch Enriched Logic ---")
    sample_json = """
    ```json
    [
      {
        "name": "Priya Sharma",
        "valid": true,
        "confidence": 0.92,
        "title_normalized": "Head of Engineering",
        "red_flags": [],
        "reasoning": "Direct hiring decision maker for technical roles"
      },
      {
        "name": "John Doe",
        "valid": false,
        "confidence": 0.35,
        "title_normalized": "Former Marketing Lead",
        "red_flags": ["Stale role", "Non-technical"],
        "reasoning": "Left company in 2022 and role is non-technical"
      }
    ]
    ```
    """
    parsed = clean_json_response(sample_json)
    assert len(parsed) == 2, "JSON parsing failed"
    assert parsed[0]["confidence"] == 0.92
    print(f"[OK] Clean JSON extraction parsed {len(parsed)} validated items successfully")

    # 5. Apollo Fallback & Multi-Key Credit Manager
    print("\n--- Testing Stage 5: Apollo Multi-Key Pool ---")
    await init_db()
    # Simulate usage
    await increment_apollo_credits(key_index=0, amount=5)
    pool = await get_apollo_pool_status()
    print(f"[OK] Apollo Pool Status: {pool['total_used']} credits logged across {pool['total_keys']} account(s)")

    # 6. Draft Generator & Mandatory Signature
    print("\n--- Testing Stage 6: Personalized Outreach Draft Generation ---")
    sample_contact = {
        "name": "Aditya Verma",
        "company_name": "Sarvam AI",
        "title": "CTO & Co-Founder",
        "title_normalized": "CTO",
        "tech_stack_match": "LLMs, GenAI, Python"
    }
    draft = await generate_outreach_draft(sample_contact)
    assert "subject" in draft and len(draft["subject"]) > 5, "Subject line missing"
    assert "body" in draft and len(draft["body"]) > 50, "Body content missing"
    # Verify exact mandatory signature exists in draft
    assert config.OUTREACH_FOOTER in draft["body"] or "kunalrai72899@gmail.com" in draft["body"], "Mandatory signature not present in draft!"
    safe_subject = draft['subject'].encode('ascii', errors='replace').decode('ascii')
    print(f"[OK] Subject Line: {safe_subject}")
    print(f"[OK] Draft generated with verified candidate signature.")


    print("\n==================================================")
    print("   ALL PIPELINE STAGES VALIDATED & PASSING 100%!  ")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(test_all_stages())
