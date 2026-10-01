import aiohttp
import asyncio
import logging
import re
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from config import config
from database.db import add_company, get_company_by_domain, get_regions

logger = logging.getLogger("company_discovery")

# List of search queries that dynamically find real tech startups in target regions
AUTONOMOUS_SEARCH_PROMPTS = [
    "top AI startups in Gurgaon 2026",
    "GenAI tech companies Noida Gurgaon",
    "best tech startups in Delhi NCR",
    "fast growing SaaS software companies Gurgaon",
    "Y Combinator tech startups Delhi NCR",
    "Series A funded AI startups India Gurgaon Noida",
    "deep tech startups Delhi NCR",
    "product based software companies in Noida Sector 62",
    "fastest growing tech startups Cyber City Gurgaon"
]

IGNORED_DOMAINS = {
    "facebook.com", "linkedin.com", "instagram.com", "twitter.com", "x.com",
    "google.com", "maps.google.com", "youtube.com", "medium.com", "github.com",
    "glassdoor.co.in", "glassdoor.com", "indeed.com", "naukri.com", "ambitionbox.com",
    "wikipedia.org", "techcrunch.com", "inc42.com", "yourstory.com", "economictimes.indiatimes.com",
    "quora.com", "reddit.com", "f6s.com", "crunchbase.com", "wellfound.com", "angellist.com",
    "seedtable.com", "designrush.com", "topdevelopers.co", "builtin.com", "builtindelhi.in",
    "failory.com", "techlist.ai", "nobroker.in", "akoode.com", "clutch.co", "g2.com", "capterra.com",
    "themanifest.com", "goodfirms.co", "upwork.com", "fiverr.com", "marketmystique.com", "appfutura.com"
}

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
        netloc = netloc.split(":")[0]
        if netloc in IGNORED_DOMAINS or not netloc or "." not in netloc or len(netloc) < 4:
            return None
        return netloc
    except Exception as e:
        logger.debug(f"Error parsing domain from '{url}': {e}")
        return None

def infer_tier_and_stack(name: str, snippet: str = "") -> tuple[int, str]:
    """Determine priority tier and relevant stack match based on keywords."""
    combined = f"{name} {snippet}".lower()
    tier = 2
    tech_stack = []

    ai_keywords = ["ai", "genai", "llm", "langgraph", "neural", "deep learning", "machine learning", "agent", "intelligence"]
    if any(k in combined for k in ai_keywords):
        tier = 1
        tech_stack.append("GenAI / AI Systems")

    dev_keywords = ["software", "tech", "cloud", "fastapi", "react", "next.js", "node.js", "saas", "platform"]
    if any(k in combined for k in dev_keywords):
        tech_stack.append("Full-Stack / Backend")

    stack_str = ", ".join(tech_stack) if tech_stack else "Software Engineering"
    return tier, stack_str

async def scrape_companies_via_web_search(query: str, region_name: str) -> List[Dict[str, Any]]:
    """
    Autonomously discover tech companies from free web search without requiring any API keys or credit cards.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    data = {"q": query}
    discovered = []

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(url, data=data, timeout=12) as resp:
                if resp.status != 200:
                    logger.debug(f"Web search HTTP {resp.status} for query '{query}'")
                    return []
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")

                # Parse search result links
                for result in soup.find_all("div", class_=re.compile(r"result__body|results_links")):
                    title_elem = result.find("a", class_=re.compile(r"result__title|result__a"))
                    snippet_elem = result.find("a", class_=re.compile(r"result__snippet"))
                    
                    if not title_elem:
                        continue

                    raw_title = title_elem.get_text().strip()
                    raw_snippet = snippet_elem.get_text().strip() if snippet_elem else ""
                    raw_href = title_elem.get("href", "")

                    # DuckDuckGo wraps target URLs in /l/?kh=-1&uddg=<encoded_url>
                    actual_url = raw_href
                    if "uddg=" in raw_href:
                        match = re.search(r"uddg=([^&]+)", raw_href)
                        if match:
                            actual_url = unquote(match.group(1))

                    domain = clean_domain(actual_url)
                    if not domain:
                        continue

                    # Clean company name
                    comp_name = raw_title.split("-")[0].split("|")[0].split(":")[0].strip()
                    junk_keywords = ["top ", "best ", "list of", "companies in", "fastest growing", "it companies", "startups in", "review"]
                    if any(jk in comp_name.lower() for jk in junk_keywords) or len(comp_name) < 2 or len(comp_name) > 30:
                        comp_name = domain.split(".")[0].replace("-", " ").capitalize()

                    # Deduplication check against SQLite DB
                    existing = await get_company_by_domain(domain)
                    if existing:
                        continue

                    tier, stack_match = infer_tier_and_stack(comp_name, raw_snippet)

                    # Insert new company into SQLite
                    company_id = await add_company(
                        name=comp_name,
                        domain=domain,
                        region=region_name,
                        tier=tier,
                        source="web_search_auto",
                        tech_stack_match=stack_match
                    )

                    if company_id:
                        discovered.append({
                            "id": company_id,
                            "name": comp_name,
                            "domain": domain,
                            "region": region_name,
                            "tier": tier,
                            "tech_stack_match": stack_match,
                            "source": "web_search_auto"
                        })
                        logger.info(f"Autonomously discovered company: {comp_name} ({domain}) in {region_name}")

        except Exception as e:
            logger.debug(f"Error scraping web search for query '{query}': {e}")

    return discovered

async def discover_companies_via_llm(region_name: str) -> List[Dict[str, Any]]:
    """
    Autonomously generate real tech companies, AI startups, and SaaS product companies for target region.
    """
    from pipeline.llm_validator import call_unified_llm, clean_json_response
    prompt = f"""
Generate a list of 10-15 REAL, ACTIVE tech companies and high-growth startups headquartered or having engineering offices in {region_name}, India (Gurgaon, Noida, Delhi NCR, Bengaluru).
Focus on: GenAI/LLM startups, AI Platforms, SaaS, FinTech, E-Commerce Tech, and Deep Tech product companies.

Respond ONLY with a valid JSON array in this exact format:
[
  {{
    "name": "Company Name",
    "domain": "company.com",
    "tech_stack": "GenAI, Python, React, AWS",
    "tier": 1
  }}
]
"""
    sys_prompt = (
        "You are an expert tech startup researcher for India tech hubs. "
        "Your task is to identify real, active tech startups and product companies with valid domains."
    )
    discovered = []
    try:
        raw_res = await call_unified_llm(prompt, system_prompt=sys_prompt)
        parsed = clean_json_response(raw_res) if raw_res else []
        for item in parsed:
            name = item.get("name", "").strip()
            domain = clean_domain(item.get("domain", ""))
            if not name or not domain:
                continue
            
            existing = await get_company_by_domain(domain)
            if existing:
                continue

            tier = item.get("tier", 1)
            stack = item.get("tech_stack", "GenAI / Software Engineering")
            
            comp_id = await add_company(
                name=name,
                domain=domain,
                region=region_name,
                tier=tier,
                source="llm_discovery",
                tech_stack_match=stack
            )
            if comp_id:
                discovered.append({
                    "id": comp_id,
                    "name": name,
                    "domain": domain,
                    "region": region_name,
                    "tier": tier,
                    "tech_stack_match": stack,
                    "source": "llm_discovery"
                })
                logger.info(f"LLM Discovered Company: {name} ({domain}) in {region_name}")
    except Exception as e:
        logger.error(f"Error discovering companies via LLM for {region_name}: {e}")
        
    return discovered

async def run_company_discovery(region_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Fast, concurrent autonomous company discovery across target regions combining Web search & LLM intelligence.
    """
    regions = await get_regions(active_only=True)
    if region_filter:
        regions = [r for r in regions if r["name"].lower() == region_filter.lower()]

    tasks = []
    for r in regions:
        region_name = r["name"]
        prompts = [
            f"top AI startups {region_name} 2026",
            f"fastest growing tech companies in {region_name}"
        ]
        for query in prompts:
            tasks.append(scrape_companies_via_web_search(query, region_name))
        
        # Parallel LLM company discovery
        tasks.append(discover_companies_via_llm(region_name))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_discovered = []
    for res in results:
        if isinstance(res, list):
            all_discovered.extend(res)

    return {
        "total_new_companies": len(all_discovered),
        "companies": all_discovered
    }
