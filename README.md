# 🤖 Tech-Job Company & Contact Discovery Discord Bot

An automated Discord bot tailored for tech-job company & hiring contact discovery (Delhi/NCR/Noida/Gurgaon) using free-tier tools and LLMs.

---

## 🚀 Features

- **Company Discovery**: Search tech companies via Google Maps Places API and store/deduplicate domains in SQLite.
- **Contact Discovery**: Web scraper (Playwright + BeautifulSoup) for `/team`, `/about`, `/careers` + Google Custom Search (`site:linkedin.com/in`).
- **Pattern Guesser & SMTP Verifier**: Guesses email structures (`first.last@`, `firstname@`, etc.) and performs real DNS MX & SMTP verification without paid APIs.
- **LLM Validation Layer**: Groq (`llama-3.3-70b-versatile`) or Gemini (`gemini-2.0-flash`) batch validation for quality scoring & red flag filtering.
- **Apollo.io Fallback**: Capped fallback only for Tier-1 companies with 0 contacts found, tracking weekly credit limits.
- **Interactive Review & Drafts**: Review pending contacts in Discord (`/pending`, `/approve`, `/reject`) and generate personalized cold email drafts (`/draft <id>`).
- **Automated Scheduling**: APScheduler weekly pipeline runs with Discord summary announcements.

---

## 📋 Prerequisites

- **Python 3.10+** installed
- **Discord Developer Account** (Free)
- Free-tier API keys for:
  - **Google Maps Places API** (Google Cloud Console)
  - **Google Custom Search JSON API** (100 free queries/day)
  - **Groq API** or **Google Gemini API** (Free)
  - **Apollo.io API** (Free tier with limited credits, optional fallback)

---

## 🛠️ Setup Instructions

### 1. Clone & Install Dependencies

```bash
# Clone or navigate to the directory
cd d:/contact-bot

# Install dependencies
python -m pip install -r requirements.txt

# Install Playwright browser binaries (for web scraping)
playwright install chromium
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Open `.env` and fill in your values:

```env
# Discord
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_GUILD_ID=your_guild_id_here
DISCORD_NOTIFICATION_CHANNEL_ID=your_channel_id_here

# LLM Provider (Free tier)
LLM_PROVIDER=groq # or gemini
GROQ_API_KEY=gsk_your_groq_key
GEMINI_API_KEY=your_gemini_key

# Apollo.io Discovery Pool (Multi-Key Support)
# Provide comma-separated keys to multiply free credits:
APOLLO_API_KEYS=key1,key2,key3
APOLLO_DAILY_CREDIT_CAP_PER_KEY=10
APOLLO_WEEKLY_CREDIT_CAP_PER_KEY=50
```


---

## 🤖 Discord Bot Setup & Permissions

1. Visit the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and give it a name (e.g. `JobContactBot`).
3. Go to the **Bot** tab:
   - Click **Reset Token** and copy your **Bot Token** into `DISCORD_BOT_TOKEN` in `.env`.
   - Under **Privileged Gateway Intents**, enable:
     - **Message Content Intent**
     - **Server Members Intent**
4. Go to **OAuth2 -> URL Generator**:
   - **Scopes**: select `bot` and `applications.commands`.
   - **Bot Permissions**: select `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `Use Slash Commands`.
   - Copy the generated URL and open it in your browser to invite the bot to your Discord server.
5. In your Discord server:
   - Enable **Developer Mode** (User Settings -> Advanced -> Developer Mode).
   - Right-click your server icon -> **Copy Server ID** -> paste into `DISCORD_GUILD_ID` in `.env`.
   - Right-click the channel for announcements -> **Copy Channel ID** -> paste into `DISCORD_NOTIFICATION_CHANNEL_ID`.

---

## 💻 Running the Bot

```bash
python main.py
```

When connected, slash commands will sync to your server immediately if `DISCORD_GUILD_ID` is set!

---

## 🎮 Discord Slash Commands

| Command | Description |
|---|---|
| `/addregion <name> [query]` | Add a new target region (e.g. `Noida`, `Gurgaon`, `Delhi NCR`). |
| `/addcompany <name> <domain> [region] [tier]` | Register a company manually into the database. |
| `/status` | View pipeline stats (companies, contacts, verified emails, Apollo usage). |
| `/pending [page]` | View candidate contacts awaiting review with confidence scores. |
| `/approve <id>` | Approve a contact for outreach. |
| `/reject <id>` | Reject a contact. |
| `/draft <contact_id>` | Generate a personalized cold email draft tailored to your profile. |

---

## 📦 Project Structure

```
contact-bot/
├── config.py                 # Configuration loader & user profile
├── requirements.txt          # Dependencies
├── .env.example              # Environment variables template
├── main.py                   # Bot entrypoint & lifecycle
├── database/
│   ├── __init__.py
│   └── db.py                 # SQLite database initialization & CRUD
├── bot/
│   ├── __init__.py
│   └── cogs/
│       ├── admin.py          # /addregion, /addcompany, /status
│       └── review.py         # /pending, /approve, /reject, /draft
├── pipeline/                 # Core scraping, verification & LLM pipeline
│   ├── company_discovery.py
│   ├── contact_discovery.py
│   ├── email_guesser.py
│   ├── email_verifier.py
│   ├── llm_validator.py
│   ├── apollo_client.py
│   ├── draft_generator.py
│   └── scheduler.py
└── README.md
```
