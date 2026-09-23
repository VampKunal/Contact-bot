import re
import asyncio
import logging
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from config import config
from pipeline.email_guesser import generate_email_permutations
from pipeline.email_verifier import verify_smtp_mailbox

logger = logging.getLogger("contact_discovery")

# Hiring-relevant target titles
TARGET_ROLE_KEYWORDS = [
    "cto", "chief technology officer", "head of engineering", "vp of engineering",
    "director of engineering", "engineering manager", "lead engineer", "founding engineer",
    "co-founder", "founder", "hr manager", "talent acquisition", "head of talent",
    "recruitment lead", "lead recruiter", "technical recruiter", "tech lead"
]

def is_hiring_relevant_title(title: str) -> bool:
    """Check if title matches engineering or technical hiring leadership."""
    if not title:
        return False
    t_lower = title.lower()
    return any(k in t_lower for k in TARGET_ROLE_KEYWORDS)

async def scrape_company_website(domain: str) -> List[Dict[str, str]]:
    """
    Scrape /about, /team, /careers, /leadership pages of a company website.
    Extracts candidate names and titles using BeautifulSoup.
    """
    candidates = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    base_url = f"https://{domain.rstrip('/')}"
    paths = ["", "/about", "/team", "/about-us", "/people", "/leadership", "/careers"]

    async with aiohttp.ClientSession(headers=headers) as session:
        for path in paths:
            target_url = f"{base_url}{path}"
            try:
                async with session.get(target_url, timeout=8, ssl=False) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")

                    # Look for team card structures
                    for card in soup.find_all(["div", "section", "article", "li"], class_=re.compile(r"(team|member|leader|person|bio|profile)", re.I)):
                        text_blocks = [t.strip() for t in card.stripped_strings if len(t.strip()) > 1]
                        if len(text_blocks) >= 2:
                            name_candidate = text_blocks[0]
                            title_candidate = text_blocks[1]
                            
                            # Clean and validate name & title
                            if 2 <= len(name_candidate.split()) <= 4 and is_hiring_relevant_title(title_candidate):
                                candidates.append({
                                    "name": name_candidate,
                                    "title": title_candidate,
                                    "source": "scraped_website"
                                })

                    # Also search for mailto links on the page
                    for mailto in soup.select("a[href^='mailto:']"):
                        href = mailto.get("href", "")
                        email_found = href.replace("mailto:", "").split("?")[0].strip()
                        if "@" in email_found and domain in email_found:
                            name_text = mailto.get_text().strip() or "Team Contact"
                            candidates.append({
                                "name": name_text,
                                "title": "Team Member",
                                "email": email_found,
                                "source": "scraped_website"
                            })

            except Exception as e:
                logger.debug(f"Scraping error on {target_url}: {e}")
            
            await asyncio.sleep(0.5)

    return candidates

async def search_linkedin_contacts(
    company_name: str,
    domain: str,
    limit: int = 5
) -> List[Dict[str, str]]:
    """
    Search Google Custom Search JSON API for LinkedIn profiles:
    site:linkedin.com/in "{company name}" "CTO" OR "HR" OR "Engineering Manager"
    Enforces a strict 90 queries/day limit to protect the 100 queries/day free tier.
    """
    api_key = config.GOOGLE_CUSTOM_SEARCH_API_KEY
    cx = config.GOOGLE_CUSTOM_SEARCH_CX

    if not api_key or not cx:
        logger.debug("Google Custom Search credentials not provided. Skipping LinkedIn search.")
        return []

    # Check daily query quota
    from database.db import get_google_search_count_today, increment_google_search_count
    used_today = await get_google_search_count_today()
    if used_today >= 90:
        logger.warning(f"Google Custom Search daily free quota reached ({used_today}/100). Skipping search.")
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    query_roles = '"CTO" OR "Head of Engineering" OR "Engineering Manager" OR "Founder" OR "HR" OR "Talent Acquisition"'
    q = f'site:linkedin.com/in "{company_name}" {query_roles}'

    params = {
        "key": api_key,
        "cx": cx,
        "q": q,
        "num": limit
    }

    results = []
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=10) as resp:
                await increment_google_search_count(1)
                if resp.status != 200:
                    logger.error(f"Google Custom Search API error {resp.status}")
                    return []
                data = await resp.json()
                items = data.get("items", [])

                for item in items:
                    title_snippet = item.get("title", "")
                    snippet = item.get("snippet", "")
                    # LinkedIn titles usually look like: "John Doe - CTO - Acme Corp | LinkedIn"
                    title_clean = title_snippet.replace(" | LinkedIn", "").replace(" - LinkedIn", "")
                    parts = [p.strip() for p in title_clean.split("-")]
                    
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        role = parts[1].strip()
                        if 2 <= len(name.split()) <= 4:
                            results.append({
                                "name": name,
                                "title": role,
                                "snippet": snippet,
                                "source": "google_search_linkedin"
                            })
        except Exception as e:
            logger.error(f"Error querying Google Custom Search for {company_name}: {e}")

    return results


async def discover_and_verify_contacts(
    company_id: int,
    company_name: str,
    domain: str
) -> List[Dict[str, Any]]:
    """
    Run website scrape + LinkedIn search, guess email permutations,
    and verify candidates via SMTP/MX check.
    """
    raw_contacts = []

    # 1. Scrape Website
    scraped = await scrape_company_website(domain)
    raw_contacts.extend(scraped)

    # 2. LinkedIn Search via Google Custom Search
    linkedin_leads = await search_linkedin_contacts(company_name, domain)
    raw_contacts.extend(linkedin_leads)

    # Deduplicate by candidate name
    unique_candidates: Dict[str, Dict[str, Any]] = {}
    for c in raw_contacts:
        name_key = c["name"].strip().lower()
        if name_key not in unique_candidates:
            unique_candidates[name_key] = c

    verified_leads = []

    for name_key, candidate in unique_candidates.items():
        name = candidate["name"]
        title = candidate.get("title", "Leader")
        source = candidate.get("source", "scraped")
        snippet = candidate.get("snippet", "")

        # Candidate email exists or generate permutations
        target_email = candidate.get("email")
        best_email = target_email
        email_is_verified = False

        if not best_email:
            # Generate permutations and test via SMTP
            permutations = generate_email_permutations(name, domain)
            for test_email in permutations[:3]: # Test top 3 most common patterns
                is_valid, reason = await verify_smtp_mailbox(test_email)
                if is_valid:
                    best_email = test_email
                    email_is_verified = True
                    break
        else:
            is_valid, _ = await verify_smtp_mailbox(best_email)
            email_is_verified = is_valid

        # Add candidate lead for LLM validation stage
        verified_leads.append({
            "company_id": company_id,
            "company_name": company_name,
            "domain": domain,
            "name": name,
            "title": title,
            "email": best_email or f"{name.lower().replace(' ', '.')}@{domain}",
            "source": source if not email_is_verified else "pattern-guess" if not candidate.get("email") else source,
            "email_verified": email_is_verified,
            "raw_snippet": snippet
        })

    return verified_leads
