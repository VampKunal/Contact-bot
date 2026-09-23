import asyncio
import os
import sys

# Ensure project root in path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from config import config
from database.db import init_db, get_pipeline_stats, get_apollo_pool_status, get_pending_contacts
from pipeline.apollo_client import search_apollo_decision_makers
from pipeline.llm_validator import validate_contact_batch
from pipeline.draft_generator import generate_outreach_draft
from bot.cogs.review import infer_target_hiring_roles

async def test_live_pipeline():
    print("==========================================================")
    print("     LIVE PIPELINE TEST WITH YOUR ACTIVE CREDENTIALS      ")
    print("==========================================================")
    
    # 1. Initialize SQLite Database
    await init_db()
    print("[1/5] Database initialized successfully.")

    # 2. Test Live Apollo Discovery
    print(f"\n[2/5] Querying Apollo API using active key ({config.APOLLO_KEYS[0][:6]}...)...")
    res = await search_apollo_decision_makers(
        locations=["Gurgaon, Haryana, India", "Noida, Uttar Pradesh, India"],
        per_page=3 # Small test batch to preserve daily credits
    )
    
    contacts = res.get("contacts", [])
    print(f"Status: {res.get('status')} | Contacts Retrieved: {len(contacts)}")
    
    if not contacts:
        print("[WARNING] Apollo returned 0 contacts or key has credit limitations. Check Apollo dashboard.")
        return

    for idx, c in enumerate(contacts, 1):
        print(f"  {idx}. {c['name']} - {c.get('title')} @ {c.get('company_name')} ({c.get('domain')}) [Email: {c.get('email')}]")

    # 3. Test LLM Validation Layer (Groq openai/gpt-oss-120b)
    print(f"\n[3/5] Running LLM Validation with {config.LLM_PROVIDER.upper()} ({config.GROQ_MODEL})...")
    validated = await validate_contact_batch(contacts)
    for v in validated:
        conf = f"{(v.get('llm_confidence', 0) * 100):.0f}%"
        hiring_roles = infer_target_hiring_roles(v.get('title_normalized') or v.get('title'), v.get('tech_stack_match'))
        print(f"  - {v['name']}: Confidence={conf} | Normalized Title='{v.get('title_normalized')}'")
        print(f"    Reasoning: {v.get('llm_reasoning')}")
        print(f"    Hiring For: {hiring_roles}")

    # 4. Test Draft Generator
    first_lead = validated[0]
    print(f"\n[4/5] Generating personalized outreach draft for {first_lead['name']} @ {first_lead['company_name']}...")
    draft = await generate_outreach_draft(first_lead)
    
    safe_subject = draft.get('subject', '').encode('ascii', errors='replace').decode('ascii')
    safe_body = draft.get('body', '').encode('ascii', errors='replace').decode('ascii')
    
    print("\n------------------- GENERATED EMAIL DRAFT -------------------")
    print(f"SUBJECT: {safe_subject}")
    print(f"BODY:\n{safe_body}")
    print("-------------------------------------------------------------")

    # 5. Check Pool Status
    print("\n[5/5] Checking Credit Pool and Pipeline Stats...")
    pool = await get_apollo_pool_status()
    stats = await get_pipeline_stats()
    print(f"Apollo Pool: {pool['total_used']} / {pool['total_cap']} credits used this week.")
    print(f"Pipeline Stats: {stats}")

    print("\n==========================================================")
    print("      ALL LIVE INTEGRATION TESTS COMPLETED SUCCESSFULLY!  ")
    print("==========================================================")

if __name__ == "__main__":
    asyncio.run(test_live_pipeline())
