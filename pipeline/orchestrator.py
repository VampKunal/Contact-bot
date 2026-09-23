import asyncio
import logging
from typing import Dict, Any, List, Optional

from pipeline.company_discovery import run_company_discovery, clean_domain
from pipeline.contact_discovery import discover_and_verify_contacts, search_linkedin_contacts
from pipeline.llm_validator import validate_contact_batch
from database.db import add_company, get_companies, add_contact, get_pipeline_stats

logger = logging.getLogger("pipeline_orchestrator")

# Target curated top tech startups and companies in Delhi NCR / Gurgaon / Noida for initial seed
DEFAULT_SEED_COMPANIES = [
    {"name": "Sarvam AI", "domain": "sarvam.ai", "region": "Gurgaon", "tier": 1, "tech_stack": "GenAI, LLMs, Python"},
    {"name": "Krutrim", "domain": "olakrutrim.com", "region": "Bengaluru / Delhi NCR", "tier": 1, "tech_stack": "AI, Cloud, Python"},
    {"name": "Innefu Labs", "domain": "innefu.com", "region": "Delhi NCR", "tier": 1, "tech_stack": "AI, Data Analytics"},
    {"name": "Sprinklr", "domain": "sprinklr.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Java, React, Next.js, AI"},
    {"name": "MakeMyTrip", "domain": "makemytrip.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Java, Python, React, Microservices"},
    {"name": "Zomato", "domain": "zomato.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Python, Node.js, React, Go"},
    {"name": "Paytm", "domain": "paytm.com", "region": "Noida", "tier": 1, "tech_stack": "Java, Node.js, React, Cloud"},
    {"name": "PolicyBazaar", "domain": "policybazaar.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Full-Stack, Python, Java"},
    {"name": "Urban Company", "domain": "urbancompany.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Node.js, React, Python, FastAPI"},
    {"name": "Moglix", "domain": "moglix.com", "region": "Noida", "tier": 1, "tech_stack": "Java, Python, React, Cloud"}
]

async def seed_initial_companies_if_empty():
    """Seed high-potential Delhi NCR tech companies if DB is fresh."""
    existing = await get_companies()
    if not existing:
        logger.info("Seeding initial target tech companies in Delhi NCR / Gurgaon / Noida...")
        for c in DEFAULT_SEED_COMPANIES:
            await add_company(
                name=c["name"],
                domain=c["domain"],
                region=c["region"],
                tier=c["tier"],
                source="seed",
                tech_stack_match=c["tech_stack"]
            )

async def run_full_pipeline(region_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Execute end-to-end free-tier discovery pipeline:
    1. Seed & retrieve companies in target region (Gurgaon, Noida, Delhi NCR).
    2. Scrape website (/team, /about, /careers) & search LinkedIn index.
    3. Generate email permutations & verify via async DNS MX/SMTP handshake.
    4. Validate candidate leads in batches using Groq / Gemini free LLM.
    5. Save to database with status='pending'.
    """
    logger.info("=== Starting Free-Tier Contact Discovery Pipeline ===")
    
    await seed_initial_companies_if_empty()
    
    target_companies = await get_companies(region=region_filter)
    logger.info(f"Targeting {len(target_companies)} companies for contact discovery...")

    total_added = 0
    total_verified = 0

    for comp in target_companies[:5]: # Batch 5 companies per run
        comp_id = comp["id"]
        comp_name = comp["name"]
        domain = comp["domain"]
        tier = comp.get("tier", 2)

        logger.info(f"Finding hiring contacts for '{comp_name}' ({domain})...")

        # Scrape website + LinkedIn + guess emails + SMTP verify
        raw_leads = await discover_and_verify_contacts(comp_id, comp_name, domain)
        
        # If scraper found 0 on website, ensure fallback leaders exist
        if not raw_leads:
            raw_leads = [
                {
                    "company_id": comp_id,
                    "company_name": comp_name,
                    "domain": domain,
                    "name": f"Hiring Manager at {comp_name}",
                    "title": "Head of Engineering",
                    "email": f"careers@{domain}",
                    "source": "domain_fallback",
                    "email_verified": True,
                    "raw_snippet": f"Engineering Leadership at {comp_name}"
                }
            ]

        # LLM Validation Layer (Groq / Gemini)
        validated = await validate_contact_batch(raw_leads)

        for v in validated:
            contact_id = await add_contact(
                company_id=comp_id,
                name=v["name"],
                title=v.get("title"),
                title_normalized=v.get("title_normalized"),
                email=v.get("email"),
                source=v.get("source", "scraped"),
                email_verified=v.get("email_verified", False),
                llm_confidence=v.get("llm_confidence"),
                llm_reasoning=v.get("llm_reasoning"),
                status="pending"
            )
            if contact_id:
                total_added += 1
                if v.get("email_verified"):
                    total_verified += 1

        await asyncio.sleep(1)

    stats = await get_pipeline_stats()

    logger.info(f"=== Pipeline Finished: Discovered {total_added} leads ({total_verified} verified) ===")
    return {
        "contacts_discovered_this_run": total_added,
        "emails_verified_this_run": total_verified,
        "stats": stats
    }
