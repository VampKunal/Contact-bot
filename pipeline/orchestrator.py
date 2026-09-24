import asyncio
import logging
import datetime
from typing import Dict, Any, List, Optional

from config import config
from pipeline.company_discovery import run_company_discovery
from pipeline.contact_discovery import discover_raw_contacts, verify_survivor_contact_email
from pipeline.llm_validator import validate_contact_batch, calculate_calibrated_confidence
from pipeline.apollo_client import search_apollo_decision_makers, query_apollo_for_company
from database.db import (
    get_companies,
    add_company,
    add_contact,
    get_pipeline_stats,
    get_company_email_pattern,
    update_company_email_pattern,
    is_contact_already_seen
)

logger = logging.getLogger("pipeline_orchestrator")

DEFAULT_SEED_COMPANIES = [
    {"name": "Sarvam AI", "domain": "sarvam.ai", "region": "Gurgaon", "tier": 1, "tech_stack": "GenAI, LLMs, Python"},
    {"name": "Krutrim", "domain": "olakrutrim.com", "region": "Delhi NCR / Bengaluru", "tier": 1, "tech_stack": "AI, Cloud, Python"},
    {"name": "Sprinklr", "domain": "sprinklr.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Java, React, Next.js, AI"},
    {"name": "Innefu Labs", "domain": "innefu.com", "region": "Delhi NCR", "tier": 1, "tech_stack": "AI, Data Analytics"},
    {"name": "MakeMyTrip", "domain": "makemytrip.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Java, Python, React, Microservices"},
    {"name": "Zomato", "domain": "zomato.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Python, Node.js, React, Go"},
    {"name": "Paytm", "domain": "paytm.com", "region": "Noida", "tier": 1, "tech_stack": "Java, Node.js, React, Cloud"},
    {"name": "Urban Company", "domain": "urbancompany.com", "region": "Gurgaon", "tier": 1, "tech_stack": "Node.js, React, Python, FastAPI"},
    {"name": "Moglix", "domain": "moglix.com", "region": "Noida", "tier": 1, "tech_stack": "Java, Python, React, Cloud"}
]

# Global run state for /health and /status
last_pipeline_run: Dict[str, Any] = {
    "timestamp": None,
    "funnel": {
        "companies_searched": 0,
        "raw_contacts_found": 0,
        "llm_passed": 0,
        "smtp_verified": 0,
        "leads_added": 0
    }
}

async def run_full_pipeline(region_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Optimized Autonomous End-to-End Pipeline:
    1. Parallel Sourcing: Web Crawler + Scraping + LinkedIn + Apollo Discovery concurrently.
    2. Deduplication: Excludes previously seen / external / blacklisted emails.
    3. Step 1 (LLM Filter FIRST): Cheap, fast batch validation to drop stale/irrelevant contacts.
    4. Step 2 (SMTP Check SECOND): DNS MX + async socket port 25 SMTP handshake ONLY on survivors.
    5. Learn Domain Pattern: Auto-detects & saves confirmed corporate email patterns to rank permutations.
    6. Token Efficiency: Cold email drafts generated on-demand at approval time, not batch.
    7. Funnel Metrics: Comprehensive stage-by-stage observability.
    """
    logger.info("=== Starting Autonomous Discovery Pipeline (Parallel Sourcing & Reordered Verification) ===")

    funnel = {
        "companies_searched": 0,
        "raw_contacts_found": 0,
        "llm_passed": 0,
        "smtp_verified": 0,
        "leads_added": 0
    }

    # 1. Company Discovery & Apollo Discovery in Parallel
    discovery_task = run_company_discovery(region_filter=region_filter)
    apollo_task = search_apollo_decision_makers()

    discovery_res, apollo_res = await asyncio.gather(discovery_task, apollo_task, return_exceptions=True)

    new_comps = discovery_res.get("companies", []) if isinstance(discovery_res, dict) else []
    apollo_contacts = apollo_res.get("contacts", []) if isinstance(apollo_res, dict) else []

    logger.info(f"Discovered {len(new_comps)} new companies; Apollo returned {len(apollo_contacts)} parallel leads.")

    # Target active companies from DB
    target_companies = await get_companies(region=region_filter)
    if not target_companies:
        for c in DEFAULT_SEED_COMPANIES:
            await add_company(name=c["name"], domain=c["domain"], region=c["region"], tier=c["tier"], tech_stack_match=c["tech_stack"])
        target_companies = await get_companies(region=region_filter)

    funnel["companies_searched"] = min(len(target_companies), 6)
    
    # Process target companies
    all_raw_leads: List[Dict[str, Any]] = []

    # Include parallel Apollo leads first
    for ac in apollo_contacts:
        all_raw_leads.append(ac)

    # Scrape target companies in parallel batches
    scrape_tasks = []
    for comp in target_companies[:6]:
        scrape_tasks.append(discover_raw_contacts(comp["id"], comp["name"], comp["domain"]))

    scrape_results = await asyncio.gather(*scrape_tasks, return_exceptions=True)
    for res in scrape_results:
        if isinstance(res, list):
            all_raw_leads.extend(res)

    funnel["raw_contacts_found"] = len(all_raw_leads)
    logger.info(f"Total raw candidate leads collected: {len(all_raw_leads)}")

    # Deduplication step (against DB & excluded list)
    deduped_candidates = []
    seen_keys = set()
    excluded_set = config.EXCLUDED_EMAILS

    for c in all_raw_leads:
        email = (c.get("email") or "").strip().lower()
        name = (c.get("name") or "").strip().lower()
        comp_id = c.get("company_id")
        
        # Check exclusion list
        if email and email in excluded_set:
            continue

        dedupe_key = (name, comp_id)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        # Check existing database records
        if await is_contact_already_seen(email=email if email else None, name=name, company_id=comp_id):
            continue

        deduped_candidates.append(c)

    logger.info(f"Candidates remaining after deduplication: {len(deduped_candidates)}")

    # 3. Step 1: Cheap LLM Filter FIRST (evaluates relevance, stale status, clean title)
    validated_candidates = await validate_contact_batch(deduped_candidates)

    survivors = [c for c in validated_candidates if c.get("is_llm_passed", True)]
    funnel["llm_passed"] = len(survivors)
    logger.info(f"LLM Filter: {len(survivors)}/{len(validated_candidates)} candidates survived.")

    # 4. Step 2: Targeted SMTP Verification SECOND on Survivors ONLY
    for cand in survivors:
        comp_id = cand["company_id"]
        known_pattern = await get_company_email_pattern(comp_id)

        # Run SMTP / MX check on survivor
        verified_cand = await verify_survivor_contact_email(cand, known_domain_pattern=known_pattern)
        
        # If new pattern confirmed, store it for the company
        if verified_cand.get("detected_pattern") and verified_cand.get("email_verified"):
            await update_company_email_pattern(comp_id, verified_cand["detected_pattern"])

        if verified_cand.get("email_verified"):
            funnel["smtp_verified"] += 1

        # Calculate calibrated multi-signal confidence score
        confidence = calculate_calibrated_confidence(
            llm_relevant=verified_cand.get("is_llm_passed", True),
            llm_score=verified_cand.get("raw_llm_score", 0.75),
            red_flags=verified_cand.get("red_flags", []),
            email_verified=verified_cand.get("email_verified", False),
            source_type=verified_cand.get("source", "scraped"),
            pattern_match=verified_cand.get("pattern_matched", False)
        )

        contact_id = await add_contact(
            company_id=comp_id,
            name=verified_cand["name"],
            title=verified_cand.get("title"),
            title_normalized=verified_cand.get("title_normalized"),
            email=verified_cand.get("email"),
            source=verified_cand.get("source", "scraped"),
            email_verified=verified_cand.get("email_verified", False),
            llm_confidence=confidence,
            llm_reasoning=verified_cand.get("llm_reasoning"),
            status="pending"
        )
        if contact_id:
            funnel["leads_added"] += 1

    # Record last run metrics for /health
    last_pipeline_run["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    last_pipeline_run["funnel"] = funnel

    stats = await get_pipeline_stats()

    logger.info(
        f"=== Autonomous Pipeline Complete ===\n"
        f"Funnel: Searched={funnel['companies_searched']} -> Raw={funnel['raw_contacts_found']} -> "
        f"LLM_Passed={funnel['llm_passed']} -> SMTP_Verified={funnel['smtp_verified']} -> "
        f"Leads_Added={funnel['leads_added']}"
    )

    return {
        "new_companies_discovered": len(new_comps),
        "funnel": funnel,
        "stats": stats
    }
