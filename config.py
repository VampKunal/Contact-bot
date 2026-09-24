import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

class Config:
    # Discord
    DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
    DISCORD_GUILD_ID: str = os.getenv("DISCORD_GUILD_ID", "")
    DISCORD_NOTIFICATION_CHANNEL_ID: str = os.getenv("DISCORD_NOTIFICATION_CHANNEL_ID", "")

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "contact_bot.db")

    # Google APIs
    GOOGLE_PLACES_API_KEY: str = os.getenv("GOOGLE_PLACES_API_KEY", "")
    GOOGLE_CUSTOM_SEARCH_API_KEY: str = os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY", "")
    GOOGLE_CUSTOM_SEARCH_CX: str = os.getenv("GOOGLE_CUSTOM_SEARCH_CX", "")

    # LLM Settings
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq").lower()
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


    # Apollo Multi-Key Pool Settings
    APOLLO_WEEKLY_CREDIT_CAP_PER_KEY: int = int(os.getenv("APOLLO_WEEKLY_CREDIT_CAP_PER_KEY", os.getenv("APOLLO_WEEKLY_CREDIT_CAP", "50")))
    APOLLO_DAILY_CREDIT_CAP_PER_KEY: int = int(os.getenv("APOLLO_DAILY_CREDIT_CAP_PER_KEY", "10"))
    
    @property
    def APOLLO_KEYS(self) -> list[str]:
        """Return list of valid Apollo API keys from APOLLO_API_KEYS or APOLLO_API_KEY."""
        raw_keys = os.getenv("APOLLO_API_KEYS", "")
        single_key = os.getenv("APOLLO_API_KEY", "")
        
        keys = []
        if raw_keys:
            keys.extend([k.strip() for k in raw_keys.split(",") if k.strip()])
        elif single_key.strip():
            keys.append(single_key.strip())
        return keys

    # Default Target Locations for Apollo Discovery
    DEFAULT_LOCATIONS: list[str] = [
        "Gurgaon, Haryana, India",
        "Noida, Uttar Pradesh, India",
        "Delhi, India",
        "New Delhi, Delhi, India"
    ]



    # Email Verification
    SMTP_SENDER_DOMAIN: str = os.getenv("SMTP_SENDER_DOMAIN", "gmail.com")
    SMTP_TIMEOUT: int = int(os.getenv("SMTP_TIMEOUT", "10"))


    # Pipeline & Scheduling Settings
    SCHEDULE_CRON_HOUR: int = int(os.getenv("SCHEDULE_CRON_HOUR", "3"))  # 03:30 UTC = 9:00 AM IST
    SCHEDULE_CRON_MINUTE: int = int(os.getenv("SCHEDULE_CRON_MINUTE", "30"))

    # Deduplication & Exclusions
    @property
    def EXCLUDED_EMAILS(self) -> set[str]:
        """Set of emails to exclude from discovery (e.g. personal, previously emailed)."""
        raw = os.getenv("EXCLUDED_EMAILS", "")
        emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
        # Always exclude personal candidate email
        emails.add("kunalrai72899@gmail.com")
        emails.add("vampkunal@gmail.com")
        return emails

    # User Profile for Outreach
    CANDIDATE_NAME: str = "Kunal Rai"
    CANDIDATE_PROFILE: str = (
        "Final-year B.Tech CS student with hands-on full-stack experience "
        "(Next.js, React, Node.js, FastAPI, NestJS) and applied GenAI / LLM engineering "
        "(LangGraph multi-agent systems, Corrective RAG, autonomous workflows), plus strong DSA/C++ foundation."
    )
    OUTREACH_FOOTER: str = (
        "kunalrai72899@gmail.com (not vampkunal one) / "
        "Resume: https://drive.google.com/file/d/12eshsKSJb8mYVAj5y4KGm_GZx6Z_Qlvb/view?usp=sharing / "
        "GitHub: https://github.com/VampKunal / "
        "LinkedIn: https://www.linkedin.com/in/kunalrai-fullstackdev/ / "
        "+91 7289907531"
    )

config = Config()
