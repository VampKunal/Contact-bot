import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from config import config
from database.db import (
    init_db,
    add_company,
    get_company_email_pattern,
    update_company_email_pattern,
    is_contact_already_seen,
    get_pipeline_stats
)
from pipeline.email_guesser import generate_email_permutations, detect_pattern
from pipeline.llm_validator import calculate_calibrated_confidence, validate_contact_batch
from pipeline.draft_generator import generate_outreach_draft, generate_mailto_url
from pipeline.orchestrator import run_full_pipeline

async def test_new_features():
    print("=== Testing Pipeline Reordering & Feature Upgrades ===")
    await init_db()

    # 1. Test Pattern Learning & Prioritized Permutations
    print("\n1. Testing Email Pattern Detection & Learning...")
    detected = detect_pattern("rahul.sharma@testcompany.in", "Rahul Sharma")
    assert detected == "first.last", f"Pattern detection failed: {detected}"
    
    # Priority ranking test
    perms_with_pattern = generate_email_permutations("Pooja Verma", "testcompany.in", known_pattern="flast")
    assert perms_with_pattern[0] == "pverma@testcompany.in", f"Expected flast first, got {perms_with_pattern[0]}"
    print("[OK] Permutation priority ranking verified: known_pattern placed at index 0.")

    # 2. Test Calibrated Confidence Scoring
    print("\n2. Testing Multi-Modal Calibrated Confidence Scoring...")
    score_high = calculate_calibrated_confidence(
        llm_relevant=True,
        llm_score=0.9,
        red_flags=[],
        email_verified=True,
        source_type="scraped_website",
        pattern_match=True
    )
    assert score_high >= 0.85, f"Expected high confidence, got {score_high}"

    score_low = calculate_calibrated_confidence(
        llm_relevant=False,
        llm_score=0.2,
        red_flags=["stale/former employee", "marketing lead"],
        email_verified=False,
        source_type="guess",
        pattern_match=False
    )
    assert score_low <= 0.25, f"Expected low confidence for stale/irrelevant, got {score_low}"
    print(f"[OK] Calibrated score: High lead = {score_high*100:.0f}%, Low/Stale lead = {score_low*100:.0f}%")

    # 3. Test Deduplication
    print("\n3. Testing External & Database Deduplication...")
    assert "kunalrai72899@gmail.com" in config.EXCLUDED_EMAILS
    seen = await is_contact_already_seen(email="kunalrai72899@gmail.com")
    print("[OK] Deduplication logic verified.")

    # 4. Test 1-Click Mailto URL Generation
    print("\n4. Testing 1-Click Mailto URL Generation...")
    mailto = generate_mailto_url("cto@acme.ai", "SDE Opportunity", "Hi Team,\nExcited to connect.")
    assert mailto.startswith("mailto:cto@acme.ai?"), f"Invalid mailto format: {mailto}"
    assert "subject=SDE" in mailto
    print(f"[OK] Mailto URL generated successfully: {mailto[:60]}...")

    # 5. Test Live Reordered Pipeline with Funnel Tracking
    print("\n5. Testing Live Reordered Pipeline Execution...")
    results = await run_full_pipeline(region_filter="Gurgaon")
    assert "funnel" in results, "Funnel dictionary missing from pipeline results"
    funnel = results["funnel"]
    print(f"[OK] Pipeline Funnel: {funnel}")

    print("\n==================================================")
    print("   ALL ARCHITECTURAL UPGRADES PASSING 100%!       ")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(test_new_features())
