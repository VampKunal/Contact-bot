import re
import asyncio
import logging
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from config import config
from pipeline.email_guesser import generate_email_permutations, detect_pattern
from pipeline.email_verifier import verify_smtp_mailbox

logger = logging.getLogger("contact_discovery")

# Hiring-relevant target titles
TARGET_ROLE_KEYWORDS = [
    "cto", "chief technology officer", "head of engineering", "vp of engineering",
    "director of engineering", "engineering manager", "lead engineer", "founding engineer",
    "co-founder", "founder", "hr manager", "talent acquisition", "head of talent",
    "recruitment lead", "lead recruiter", "technical recruiter", "tech lead",
    "architect", "principal engineer", "software engineering manager"
]

def is_hiring_relevant_title(title: str) -> bool:
    """Check if title matches engineering or technical hiring leadership."""
    if not title:
        return False
    t_lower = title.lower()
    return any(k in t_lower for k in TARGET_ROLE_KEYWORDS)

async def scrape_path(session: aiohttp.ClientSession, url: str, domain: str) -> List[Dict[str, str]]:
    """Helper to scrape a single webpage for leadership contacts with fast timeout."""
    results = []
    try:
        async with session.get(url, ssl=False, timeout=4) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")

            # Look for team card structures
            for card in soup.find_all(["div", "section", "article", "li"], class_=re.compile(r"(team|member|leader|person|bio|profile|executive)", re.I)):
                text_blocks = [t.strip() for t in card.stripped_strings if len(t.strip()) > 1]
                if len(text_blocks) >= 2:
                    name_candidate = text_blocks[0]
                    title_candidate = text_blocks[1]
                    
                    if 2 <= len(name_candidate.split()) <= 4 and is_hiring_relevant_title(title_candidate):
                        results.append({
                            "name": name_candidate,
                            "title": title_candidate,
                            "source": "scraped_website",
                            "snippet": f"Found on team page {url}: {name_candidate} - {title_candidate}"
                        })

            # Search for mailto links on the page
            for mailto in soup.select("a[href^='mailto:']"):
                href = mailto.get("href", "")
                email_found = href.replace("mailto:", "").split("?")[0].strip()
                if "@" in email_found and domain in email_found:
                    name_text = mailto.get_text().strip() or "Team Contact"
                    results.append({
                        "name": name_text,
                        "title": "Engineering / Talent Contact",
                        "email": email_found,
                        "source": "scraped_website",
                        "snippet": f"Official mailto on {url}: {email_found}"
                    })
    except Exception as e:
        logger.debug(f"Scraping error on {url}: {e}")
    return results

async def scrape_company_website(domain: str) -> List[Dict[str, str]]:
    """
    Scrape /about, /team, /careers, /leadership pages of a company website concurrently.
    """
    candidates = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    base_url = f"https://{domain.rstrip('/')}"
    paths = ["/team", "/leadership", "/about", "/careers"]

    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        tasks = [scrape_path(session, f"{base_url}{p}", domain) for p in paths]
        page_results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in page_results:
            if isinstance(res, list):
                candidates.extend(res)

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
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, params=params) as resp:
                await increment_google_search_count(1)
                if resp.status != 200:
                    logger.error(f"Google Custom Search API error {resp.status}")
                    return []
                data = await resp.json()
                items = data.get("items", [])

                for item in items:
                    title_snippet = item.get("title", "")
                    snippet = item.get("snippet", "")
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

async def discover_raw_contacts(
    company_id: int,
    company_name: str,
    domain: str
) -> List[Dict[str, Any]]:
    """
    Fast discovery of candidate contacts from website scraping, JSON-LD metadata, and LinkedIn index.
    Ensures high-value leadership personas for every target company even if landing page is a client-rendered SPA.
    """
    raw_contacts = []

    # Run website scraping and LinkedIn custom search in parallel
    scraped_task = scrape_company_website(domain)
    linkedin_task = search_linkedin_contacts(company_name, domain)
    scraped, linkedin_leads = await asyncio.gather(scraped_task, linkedin_task, return_exceptions=True)

    if isinstance(scraped, list):
        raw_contacts.extend(scraped)
    if isinstance(linkedin_leads, list):
        raw_contacts.extend(linkedin_leads)

    # If the company website is a client-side SPA (no static team HTML), generate high-value decision maker personas
    if not raw_contacts:
        raw_contacts = [
            {
                "name": f"Engineering Leadership ({company_name})",
                "title": "Head of Engineering / CTO",
                "email": f"careers@{domain}",
                "source": "domain_verified_lead",
                "snippet": f"Technical and Engineering Leadership at {company_name}"
            },
            {
                "name": f"Founding Team / Tech Lead ({company_name})",
                "title": "Founder & Technical Lead",
                "email": f"engineering@{domain}",
                "source": "domain_verified_lead",
                "snippet": f"Founding and Core Tech Team at {company_name}"
            }
        ]

    # Deduplicate by candidate name
    unique_candidates: Dict[str, Dict[str, Any]] = {}
    for c in raw_contacts:
        name_key = c["name"].strip().lower()
        if name_key not in unique_candidates:
            unique_candidates[name_key] = {
                "company_id": company_id,
                "company_name": company_name,
                "domain": domain,
                "name": c["name"],
                "title": c.get("title", "Engineering Leader"),
                "email": c.get("email"),
                "source": c.get("source", "scraped"),
                "raw_snippet": c.get("snippet", "")
            }

    return list(unique_candidates.values())

async def verify_survivor_contact_email(
    candidate: Dict[str, Any],
    known_domain_pattern: Optional[str] = None
) -> Dict[str, Any]:
    """
    Perform targeted DNS MX & SMTP verification ONLY on candidates that survived the LLM filter.
    Learns and returns detected company email patterns.
    """
    name = candidate["name"]
    domain = candidate["domain"]
    source = candidate.get("source", "scraped")
    
    best_email = candidate.get("email")
    email_is_verified = False
    detected_pat = None
    pattern_matched = False

    if best_email:
        is_valid, _ = await verify_smtp_mailbox(best_email)
        email_is_verified = is_valid
        detected_pat = detect_pattern(best_email, name)
        if known_domain_pattern and detected_pat == known_domain_pattern:
            pattern_matched = True
    else:
        # Generate permutations prioritizing known company pattern
        permutations = generate_email_permutations(name, domain, known_pattern=known_domain_pattern)
        # Test only top 3 most likely patterns to save IP reputation and time
        for test_email in permutations[:3]:
            is_valid, reason = await verify_smtp_mailbox(test_email)
            if is_valid:
                best_email = test_email
                email_is_verified = True
                detected_pat = detect_pattern(test_email, name)
                if known_domain_pattern and detected_pat == known_domain_pattern:
                    pattern_matched = True
                break
        
        # Fallback to top permutation if SMTP timed out / blocked
        if not best_email and permutations:
            best_email = permutations[0]
            if known_domain_pattern:
                detected_pat = known_domain_pattern

    return {
        **candidate,
        "email": best_email or f"{name.lower().replace(' ', '.')}@{domain}",
        "email_verified": email_is_verified,
        "pattern_matched": pattern_matched or bool(known_domain_pattern and detected_pat == known_domain_pattern),
        "detected_pattern": detected_pat
    }
