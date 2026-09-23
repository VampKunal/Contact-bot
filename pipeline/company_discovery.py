import aiohttp
import asyncio
import logging
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from config import config
from database.db import add_company, get_company_by_domain, get_regions

logger = logging.getLogger("company_discovery")

def clean_domain(url: str) -> Optional[str]:
    """Extract and normalize clean root domain from a website URL."""
    if not url:
        return None
    url_str = url.strip()
    if not url_str.startswith("http://") and not url_str.startswith("https://"):
        url_str = "https://" + url_str
    try:
        parsed = urlparse(url_str)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # Strip trailing slashes, port or query
        netloc = netloc.split(":")[0]
        # Ignore common non-company domains (e.g. facebook.com, linkedin.com, google.com)
        ignored_domains = {"facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com", "google.com", "maps.google.com"}
        if netloc in ignored_domains or not netloc or "." not in netloc:
            return None
        return netloc
    except Exception as e:
        logger.debug(f"Error parsing domain from '{url}': {e}")
        return None

def infer_tier_and_stack(name: str, types: List[str]) -> tuple[int, Optional[str]]:
    """Determine priority tier and relevant stack match based on keywords."""
    name_lower = name.lower()
    tier = 2
    tech_stack = []

    ai_keywords = ["ai", "intelligence", "neural", "deep learning", "machine learning", "robotics", "data", "analytics"]
    if any(k in name_lower for k in ai_keywords):
        tier = 1
        tech_stack.append("GenAI / Machine Learning")

    dev_keywords = ["software", "tech", "technologies", "labs", "systems", "solutions", "cloud", "infotech"]
    if any(k in name_lower for k in dev_keywords):
        tech_stack.append("Full-Stack / Cloud")

    stack_str = ", ".join(tech_stack) if tech_stack else "Software Engineering"
    return tier, stack_str

async def fetch_place_details(session: aiohttp.ClientSession, place_id: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Fetch website and contact details from Google Place Details API."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "name,website,formatted_address,types,rating,user_ratings_total",
        "key": api_key
    }
    try:
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "OK":
                    return data.get("result", {})
    except Exception as e:
        logger.error(f"Error fetching place details for {place_id}: {e}")
    return None

async def discover_companies_for_query(
    query: str,
    region_name: str,
    api_key: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Query Google Places Text Search and return newly discovered companies."""
    key = api_key or config.GOOGLE_PLACES_API_KEY
    if not key:
        logger.warning("GOOGLE_PLACES_API_KEY is not configured! Skipping Google Places search.")
        return []

    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": query,
        "key": key
    }

    new_companies = []

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=15) as resp:
                if resp.status != 200:
                    logger.error(f"Places API HTTP error {resp.status}")
                    return []
                data = await resp.json()
                status = data.get("status")
                if status != "OK" and status != "ZERO_RESULTS":
                    logger.error(f"Places API error status: {status} - {data.get('error_message')}")
                    return []

                results = data.get("results", [])
                logger.info(f"Places search for '{query}' returned {len(results)} places.")

                for item in results:
                    place_id = item.get("place_id")
                    raw_name = item.get("name", "").strip()
                    if not place_id or not raw_name:
                        continue

                    # Fetch website via Place Details
                    details = await fetch_place_details(session, place_id, key)
                    website = details.get("website") if details else None
                    domain = clean_domain(website)

                    if not domain:
                        continue

                    # Check for duplicate domain in DB
                    existing = await get_company_by_domain(domain)
                    if existing:
                        continue

                    tier, stack_match = infer_tier_and_stack(raw_name, item.get("types", []))

                    # Insert new company
                    company_id = await add_company(
                        name=raw_name,
                        domain=domain,
                        region=region_name,
                        tier=tier,
                        source="google_places",
                        tech_stack_match=stack_match
                    )

                    if company_id:
                        new_companies.append({
                            "id": company_id,
                            "name": raw_name,
                            "domain": domain,
                            "region": region_name,
                            "tier": tier,
                            "tech_stack_match": stack_match,
                            "source": "google_places"
                        })
                        logger.info(f"Discovered new company: {raw_name} ({domain}) in {region_name}")

        except Exception as e:
            logger.error(f"Error querying Places API for '{query}': {e}", exc_info=True)

    return new_companies

async def run_company_discovery(region_filter: Optional[str] = None) -> Dict[str, Any]:
    """Run company discovery across all active regions."""
    regions = await get_regions(active_only=True)
    if region_filter:
        regions = [r for r in regions if r["name"].lower() == region_filter.lower()]

    all_new_companies = []
    
    for r in regions:
        region_name = r["name"]
        templates = r.get("query_template") or f"software company {region_name}"
        queries = [q.strip() for q in templates.split(",") if q.strip()]

        for query in queries:
            logger.info(f"Running discovery: '{query}' ({region_name})")
            found = await discover_companies_for_query(query, region_name)
            all_new_companies.extend(found)
            await asyncio.sleep(1) # Gentle delay between Google API requests

    return {
        "total_new_companies": len(all_new_companies),
        "companies": all_new_companies
    }
