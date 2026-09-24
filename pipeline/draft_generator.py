import logging
import json
import urllib.parse
from typing import Dict, Any, Optional
from config import config

logger = logging.getLogger("draft_generator")

DRAFT_SYSTEM_PROMPT = f"""
You are an expert cold email copywriter helping a high-caliber candidate land an internship / fresher SDE / AI Engineer role.

Candidate Profile:
- Name: {config.CANDIDATE_NAME}
- Background: Final-year B.Tech Computer Science student
- Skills & Tech Stack: Full-Stack (Next.js, React, Node.js, FastAPI, NestJS) + Applied GenAI & LLM Systems (LangGraph multi-agent architectures, Corrective RAG, autonomous pipelines) + Strong DSA / C++
- Role target: SDE Intern, Fresher SDE, AI Engineer Intern

Writing Guidelines:
1. Subject Line: Must be attention-grabbing, clever, and relevant to their engineering/product domain (avoid generic lines like "Application for SDE" or "Quick Question").
2. Body:
   - 3-4 short, punchy paragraphs max (100-140 words).
   - Hook the recipient (CTO, VP Eng, Founder, HR) by referencing their company's tech/domain.
   - Highlight 1-2 relevant technical strengths (e.g. LangGraph agents, FastAPI backends, Next.js full-stack systems) and how the candidate can add immediate value without hand-holding.
   - Low-friction Call to Action (e.g. "Open to a 10-min chat next week?").
3. MANDATORY FOOTER: The email body MUST end with this EXACT signature:
{config.OUTREACH_FOOTER}

Respond strictly in valid JSON format:
{{
  "subject": "Attention-grabbing subject line",
  "body": "Complete email body including the exact mandatory footer"
}}
"""

def generate_mailto_url(to_email: str, subject: str, body: str) -> str:
    """Generate a 1-click mailto: URL with prefilled recipient, subject, and body."""
    params = {
        "subject": subject,
        "body": body
    }
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"mailto:{to_email}?{query}"

async def create_gmail_draft(to_email: str, subject: str, body: str) -> Optional[str]:
    """
    Optional Gmail API integration: Create a draft in user's Gmail mailbox if Google tokens exist.
    Returns draft ID if created, or None.
    """
    # Safe graceful fallback if Gmail OAuth token is not configured
    try:
        import os
        import base64
        from email.message import EmailMessage
        
        # Check if token exists in environment
        token = os.getenv("GMAIL_API_TOKEN")
        if not token:
            return None
            
        import requests
        msg = EmailMessage()
        msg.set_content(body)
        msg["To"] = to_email
        msg["Subject"] = subject
        
        raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        res = requests.post(url, json={"message": {"raw": raw_msg}}, headers=headers, timeout=5)
        if res.status_code in (200, 201):
            data = res.json()
            return data.get("id")
    except Exception as e:
        logger.debug(f"Gmail draft creation skipped/unavailable: {e}")
    return None

async def generate_outreach_draft(contact: Dict[str, Any]) -> Dict[str, str]:
    """Generate a tailored cold outreach email draft using LLM (on-demand on approval)."""
    name = contact.get("name", "Hiring Leader")
    company_name = contact.get("company_name", "your company")
    title = contact.get("title_normalized") or contact.get("title", "Engineering Lead")
    tech_stack = contact.get("tech_stack_match") or "Modern Software Stack"
    to_email = contact.get("email", "")

    user_prompt = f"""
Target Recipient:
- Name: {name}
- Title/Role: {title}
- Company: {company_name}
- Domain / Tech Stack: {tech_stack}

Write a personalized cold outreach email draft for this contact.
"""

    provider = config.LLM_PROVIDER.lower()
    raw_response = None

    if provider == "groq" and config.GROQ_API_KEY:
        try:
            from groq import AsyncGroq
            client = AsyncGroq(api_key=config.GROQ_API_KEY)
            resp = await client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"} if "llama-3" in config.GROQ_MODEL else None,
                temperature=0.7,
                timeout=20
            )
            raw_response = resp.choices[0].message.content
        except Exception as e:
            logger.error(f"Groq draft generation error: {e}")

    elif provider == "gemini" and config.GEMINI_API_KEY:
        try:
            import asyncio
            from google import genai
            client = genai.Client(api_key=config.GEMINI_API_KEY)
            full_prompt = f"{DRAFT_SYSTEM_PROMPT}\n\n{user_prompt}"
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=full_prompt
                )
            )
            raw_response = resp.text
        except Exception as e:
            logger.error(f"Gemini draft generation error: {e}")

    subject = None
    body = None

    if raw_response:
        try:
            text = raw_response.strip()
            if text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            data = json.loads(text.strip())
            subject = data.get("subject")
            body = data.get("body")
        except Exception as e:
            logger.error(f"Failed to parse draft JSON: {e}")

    if not subject or not body:
        # High quality template fallback
        subject = f"Next.js / LangGraph systems for {company_name} — SDE Intern"
        body = (
            f"Hi {name},\n\n"
            f"I've been following {company_name}'s engineering work and wanted to reach out directly. "
            f"As a final-year B.Tech CS student specializing in full-stack architecture (Next.js, FastAPI, Node.js) and "
            f"production GenAI systems (LangGraph multi-agent workflows, Corrective RAG), I'm keen to contribute to your team.\n\n"
            f"I've built autonomous agentic workflows and full-stack platforms from scratch with strong DSA/C++ foundations. "
            f"I'd love to explore internship or fresher SDE / AI Engineer roles with your engineering team.\n\n"
            f"Would you be open to a quick 10-minute chat sometime this week?\n\n"
            f"Best regards,\n"
            f"{config.CANDIDATE_NAME}\n\n"
            f"---\n"
            f"{config.OUTREACH_FOOTER}"
        )

    # Generate one-click mailto URL
    mailto_url = generate_mailto_url(to_email, subject, body) if to_email else ""

    return {
        "subject": subject,
        "body": body,
        "mailto_url": mailto_url
    }
