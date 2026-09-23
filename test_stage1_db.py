import asyncio
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from database.db import (
    init_db,
    add_region,
    get_regions,
    add_company,
    get_companies,
    add_contact,
    get_pending_contacts,
    update_contact_status,
    get_contact_by_id,
    get_pipeline_stats,
    get_apollo_credits_used,
    get_apollo_pool_status,
    increment_apollo_credits

)

async def test_all():
    print("Testing Stage 1: Database and Schema Initialisation...")
    await init_db()
    print("[OK] init_db passed")

    # 1. Regions
    regions = await get_regions()
    print(f"[OK] Default regions loaded: {[r['name'] for r in regions]}")
    added = await add_region("Bengaluru", "AI startups Bengaluru")
    print(f"[OK] Add new region: {added}")

    # 2. Companies
    cid1 = await add_company(
        name="Acme Tech Labs",
        domain="acmetech.ai",
        region="Gurgaon",
        tier=1,
        source="manual",
        tech_stack_match="FastAPI, Next.js, LangGraph"
    )
    print(f"[OK] Company added with ID: {cid1}")
    
    # Test deduplication
    cid2 = await add_company(
        name="Acme Tech Labs Duplicate",
        domain="https://acmetech.ai/",
        region="Gurgaon"
    )
    assert cid1 == cid2, "Deduplication failed!"
    print(f"[OK] Company deduplication verified: ID {cid2} matches {cid1}")

    # 3. Contacts
    contact_id = await add_contact(
        company_id=cid1,
        name="Priya Sharma",
        title="Head of Engineering",
        title_normalized="Engineering Lead",
        email="priya.sharma@acmetech.ai",
        source="pattern-guess",
        email_verified=True,
        llm_confidence=0.88,
        llm_reasoning="Title matches technical hiring decision maker; email passed MX check",
        status="pending"
    )
    print(f"[OK] Contact added with ID: {contact_id}")

    # 4. Pending & Review
    pending = await get_pending_contacts(limit=5)
    assert len(pending) > 0, "Pending contacts query returned empty"
    print(f"[OK] Pending contacts query: Found {len(pending)} items. First contact: {pending[0]['name']} at {pending[0]['company_name']}")

    # 5. Approve / Reject
    ok = await update_contact_status(contact_id, "approved")
    assert ok, "Update status failed"
    c_updated = await get_contact_by_id(contact_id)
    assert c_updated["status"] == "approved", "Contact status was not updated"
    print(f"[OK] Contact status successfully updated to: {c_updated['status']}")

    # 6. Apollo Credit Log & Multi-Key Pool
    used_key0 = await increment_apollo_credits(key_index=0, amount=2)
    used_key1 = await increment_apollo_credits(key_index=1, amount=3)
    pool_status = await get_apollo_pool_status()
    print(f"[OK] Multi-key Apollo pool verified: Key #1 = {used_key0}, Key #2 = {used_key1}, Total used = {pool_status['total_used']}")
    assert pool_status["total_used"] >= 5, "Pool total used mismatch"


    # 7. Pipeline Stats
    stats = await get_pipeline_stats()
    print(f"[OK] Pipeline stats: {stats}")
    print("\nALL STAGE 1 DB & LOGIC TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_all())
