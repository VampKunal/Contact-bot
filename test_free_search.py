import asyncio
import aiohttp
import re
from urllib.parse import unquote
from bs4 import BeautifulSoup

async def find_linkedin(company):
    q = f'site:linkedin.com/in "{company}" (CTO OR "Head of Engineering" OR Founder OR "Engineering Manager")'
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    data = {"q": q}
    leads = []
    async with aiohttp.ClientSession(headers=headers) as s:
        try:
            async with s.post(url, data=data, timeout=8) as r:
                if r.status != 200:
                    return []
                soup = BeautifulSoup(await r.text(), "html.parser")
                for res in soup.find_all("div", class_=re.compile(r"result__body")):
                    t = res.find("a", class_=re.compile(r"result__title|result__a"))
                    snip = res.find("a", class_=re.compile(r"result__snippet"))
                    if t:
                        raw_title = t.get_text().strip()
                        raw_snippet = snip.get_text().strip() if snip else ""
                        title_clean = raw_title.replace(" | LinkedIn", "").replace(" - LinkedIn", "")
                        parts = [p.strip() for p in title_clean.split("-")]
                        if len(parts) >= 2:
                            name = parts[0]
                            role = parts[1]
                            if 2 <= len(name.split()) <= 4:
                                leads.append({"name": name, "title": role, "snippet": raw_snippet})
        except Exception as e:
            print("Error:", e)
    return leads

async def main():
    for comp in ["Sarvam AI", "Sprinklr", "MakeMyTrip", "Zomato"]:
        res = await find_linkedin(comp)
        print(f"=== {comp} returned {len(res)} LinkedIn leads ===")
        for r in res[:3]:
            print("  •", r["name"], "—", r["title"])

if __name__ == "__main__":
    asyncio.run(main())
