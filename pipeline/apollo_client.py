import aiohttp
import asyncio
import logging
from typing import List, Dict, Any, Optional
from config import config
from database.db import (
    add_company,
    get_company_by_domain,
    get_contact_by_email,
    get_apollo_credits_used,
    get_apollo_daily_credits_used,
    increment_apollo_credits
)
from pipeline.company_discovery import clean_domain, infer_tier_and_stack

logger = logging.getLogger("apollo_client")

APOLLO_SEARCH_URL = "https://api.apollo.io/v1/mixed_people/search"

# Standard hiring decision maker titles to search on Apollo
DEFAULT_APOLLO_TITLES = [
    "Chief Technology Officer", "CTO", "Head of Engineering",
    "VP of Engineering", "Vice President Engineering",
    "Director of Engineering", "Engineering Manager",
    "Founding Engineer", "Co-Founder", "Founder",
    "HR Manager", "Head of Talent", "Talent Acquisition Lead",
    "Technical Recruiter", "Lead Recruiter"
]

async def get_active_apollo_key_index() -> Optional[int]:
    """
    Find the first Apollo API key in the pool with available DAILY and WEEKLY credits.
    """
    keys = config.APOLLO_KEYS
    if not keys:
        return None

    weekly_cap = config.APOLLO_WEEKLY_CREDIT_CAP_PER_KEY
    daily_cap = config.APOLLO_DAILY_CREDIT_CAP_PER_KEY

    for idx, _ in enumerate(keys):
        daily_used = await get_apollo_daily_credits_used(key_index=idx)
        weekly_used = await get_apollo_credits_used(key_index=idx)
        
        if daily_used < daily_cap and weekly_used < weekly_cap:
            return idx
            
    return None

async def search_apollo_decision_makers(
    locations: Optional[List[str]] = None,
    target_titles: Optional[List[str]] = None,
    per_page: int = 10
) -> Dict[str, Any]:
    """
    Primary Apollo Discovery: Search for tech decision makers across Delhi/NCR/Noida/Gurgaon
    using rotating API keys adhering strictly to daily credit limits.
    """
    keys = config.APOLLO_KEYS
    if not keys:
        logger.warning("No Apollo API keys configured in APOLLO_API_KEYS / APOLLO_API_KEY.")
        return {"status": "no_keys", "contacts": []}

    target_locations = locations or config.DEFAULT_LOCATIONS
    titles = target_titles or DEFAULT_APOLLO_TITLES

    collected_leads = []

    # Iterate through available keys until daily quota is met
    for key_idx, api_key in enumerate(keys):
        daily_used = await get_apollo_daily_credits_used(key_index=key_idx)
        daily_cap = config.APOLLO_DAILY_CREDIT_CAP_PER_KEY
        weekly_used = await get_apollo_credits_used(key_index=key_idx)
        weekly_cap = config.APOLLO_WEEKLY_CREDIT_CAP_PER_KEY

        remaining_daily = daily_cap - daily_used
        if remaining_daily <= 0 or weekly_used >= weekly_cap:
            logger.info(f"Apollo Key #{key_idx+1} has reached its daily/weekly credit cap ({daily_used}/{daily_cap} today).")
            continue

        fetch_count = min(per_page, remaining_daily)
        
        # Location sharding: distribute locations evenly across keys
        assigned_location = target_locations[key_idx % len(target_locations)]
        from database.db import get_next_apollo_page, increment_apollo_page
        target_page = await get_next_apollo_page(assigned_location)

        logger.info(
            f"Querying Apollo with Key #{key_idx+1}: Location='{assigned_location}', "
            f"Page={target_page}, Limit={fetch_count}..."
        )

        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key
        }

        payload = {
            "person_locations": [assigned_location],
            "person_titles": titles,
            "page": target_page,
            "per_page": fetch_count
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(APOLLO_SEARCH_URL, json=payload, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        people = data.get("people", [])
                        logger.info(f"Apollo Key #{key_idx+1} (Page {target_page}) returned {len(people)} contacts.")

                        if people:
                            await increment_apollo_credits(key_index=key_idx, amount=len(people))
                            await increment_apollo_page(assigned_location, target_page)


                        for p in people:
                            name = p.get("name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                            title = p.get("title", "Engineering Leader")
                            org = p.get("organization") or {}
                            company_name = org.get("name", "Tech Company")
                            raw_website = org.get("primary_domain") or org.get("website_url") or ""
                            domain = clean_domain(raw_website)
                            email = p.get("email")

                            if not domain or not name:
                                continue

                            # Contact-level deduplication
                            if email:
                                existing_contact = await get_contact_by_email(email)
                                if existing_contact:
                                    continue

                            # Company-level deduplication & insertion
                            existing_comp = await get_company_by_domain(domain)
                            if existing_comp:
                                company_id = existing_comp["id"]
                                region = existing_comp.get("region", "Delhi NCR")
                            else:
                                tier, stack = infer_tier_and_stack(company_name, [])
                                region = p.get("city") or "Delhi NCR"
                                company_id = await add_company(
                                    name=company_name,
                                    domain=domain,
                                    region=region,
                                    tier=tier,
                                    source="apollo_discovery",
                                    tech_stack_match=stack
                                )

                            collected_leads.append({
                                "company_id": company_id,
                                "company_name": company_name,
                                "domain": domain,
                                "region": region,
                                "name": name,
                                "title": title,
                                "email": email or f"{name.lower().replace(' ', '.')}@{domain}",
                                "source": "apollo",
                                "email_verified": bool(email),
                                "raw_snippet": f"{title} at {company_name} ({p.get('headline', '')})"
                            })

                    elif resp.status in (429, 402, 403):
                        logger.warning(f"Apollo Key #{key_idx+1} rate limit / credit exhaustion (HTTP {resp.status})")
                        # Mark daily cap reached for this key
                        await increment_apollo_credits(key_index=key_idx, amount=remaining_daily)
                    else:
                        logger.error(f"Apollo API HTTP error {resp.status}: {await resp.text()}")

            except Exception as e:
                logger.error(f"Error querying Apollo with key #{key_idx+1}: {e}")

            await asyncio.sleep(1)

    return {
        "status": "success" if collected_leads else "exhausted",
        "contacts": collected_leads
    }

async def query_apollo_for_company(
    company_name: str,
    domain: str,
    target_titles: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Fallback search targeting a specific company."""
    key_idx = await get_active_apollo_key_index()
    if key_idx is None:
        return {"status": "exhausted", "contacts": []}

    api_key = config.APOLLO_KEYS[key_idx]
    titles = target_titles or DEFAULT_APOLLO_TITLES

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key
    }

    payload = {
        "q_organization_domains": domain.lower().strip(),
        "person_titles": titles,
        "page": 1,
        "per_page": 5
    }

    contacts_found = []
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(APOLLO_SEARCH_URL, json=payload, headers=headers, timeout=12) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    people = data.get("people", [])
                    if people:
                        await increment_apollo_credits(key_index=key_idx, amount=len(people))

                    for p in people:
                        name = p.get("name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
                        title = p.get("title", "Engineering Leader")
                        email = p.get("email")

                        contacts_found.append({
                            "name": name,
                            "title": title,
                            "title_normalized": title,
                            "email": email or f"{name.lower().replace(' ', '.')}@{domain}",
                            "source": "apollo",
                            "email_verified": bool(email),
                            "llm_confidence": 0.9 if email else 0.75,
                            "llm_reasoning": f"Discovered via Apollo.io fallback (Account #{key_idx+1})",
                            "status": "pending"
                        })
                    return {"status": "success", "contacts": contacts_found}
                elif resp.status in (429, 402, 403):
                    await increment_apollo_credits(key_index=key_idx, amount=5)
                    return await query_apollo_for_company(company_name, domain, target_titles)
        except Exception as e:
            logger.error(f"Error querying Apollo for {company_name}: {e}")

    return {"status": "error", "contacts": []}
