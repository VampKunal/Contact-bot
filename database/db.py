import aiosqlite
import datetime
from typing import List, Dict, Any, Optional
from config import config

DB_PATH = config.DATABASE_PATH

async def get_db_connection():
    """Helper context manager / connection setup."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON;")
    return db

async def init_db():
    """Initialize database tables according to schema."""
    db = await get_db_connection()
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS regions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                query_template TEXT,
                active BOOLEAN DEFAULT 1
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domain TEXT UNIQUE NOT NULL,
                region TEXT,
                tier INTEGER DEFAULT 2,
                source TEXT DEFAULT 'manual',
                tech_stack_match TEXT,
                email_pattern TEXT,
                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migration: ensure email_pattern column exists if table was created previously
        try:
            await db.execute("ALTER TABLE companies ADD COLUMN email_pattern TEXT;")
        except Exception:
            pass  # Column already exists

        await db.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                title TEXT,
                title_normalized TEXT,
                email TEXT,
                source TEXT DEFAULT 'scraped',
                email_verified BOOLEAN DEFAULT 0,
                llm_confidence REAL,
                llm_reasoning TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Performance indices
        await db.execute("CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_contacts_company_id ON contacts(company_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS apollo_credit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_index INTEGER NOT NULL DEFAULT 0,
                week_start DATE NOT NULL,
                credits_used INTEGER DEFAULT 0,
                last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(key_index, week_start)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS apollo_credit_daily_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_index INTEGER NOT NULL DEFAULT 0,
                log_date DATE NOT NULL,
                credits_used INTEGER DEFAULT 0,
                last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(key_index, log_date)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS apollo_page_tracker (
                location TEXT PRIMARY KEY,
                last_page INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS google_search_daily_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date DATE NOT NULL UNIQUE,
                query_count INTEGER DEFAULT 0,
                last_queried_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Insert default target regions if empty
        cursor = await db.execute("SELECT COUNT(*) as count FROM regions")
        row = await cursor.fetchone()
        if row and row["count"] == 0:
            default_regions = [
                ("Gurgaon", "software company Gurgaon, IT startup Gurgaon"),
                ("Noida", "software company Noida, tech startup Noida"),
                ("Delhi NCR", "IT company Delhi NCR, tech startup Delhi"),
            ]
            await db.executemany(
                "INSERT INTO regions (name, query_template) VALUES (?, ?)",
                default_regions
            )

        await db.commit()
    finally:
        await db.close()

# --- Region Operations ---
async def add_region(name: str, query_template: Optional[str] = None) -> bool:
    db = await get_db_connection()
    try:
        await db.execute(
            "INSERT INTO regions (name, query_template) VALUES (?, ?)",
            (name.strip(), query_template or f"tech company {name}")
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False
    finally:
        await db.close()

async def get_regions(active_only: bool = True) -> List[Dict[str, Any]]:
    db = await get_db_connection()
    try:
        query = "SELECT * FROM regions WHERE active = 1" if active_only else "SELECT * FROM regions"
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

# --- Company Operations ---
async def add_company(
    name: str,
    domain: str,
    region: Optional[str] = None,
    tier: int = 2,
    source: str = "manual",
    tech_stack_match: Optional[str] = None
) -> Optional[int]:
    """Add a new company or return existing ID if duplicate domain."""
    domain_clean = domain.strip().lower().replace("http://", "").replace("https://", "").rstrip("/")
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            """
            INSERT INTO companies (name, domain, region, tier, source, tech_stack_match)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), domain_clean, region, tier, source, tech_stack_match)
        )
        await db.commit()
        return cursor.lastrowid
    except aiosqlite.IntegrityError:
        cursor = await db.execute("SELECT id FROM companies WHERE domain = ?", (domain_clean,))
        row = await cursor.fetchone()
        return row["id"] if row else None
    finally:
        await db.close()

async def get_company_by_domain(domain: str) -> Optional[Dict[str, Any]]:
    domain_clean = domain.strip().lower().replace("http://", "").replace("https://", "").rstrip("/")
    db = await get_db_connection()
    try:
        cursor = await db.execute("SELECT * FROM companies WHERE domain = ?", (domain_clean,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()

async def get_company_by_id(company_id: int) -> Optional[Dict[str, Any]]:
    db = await get_db_connection()
    try:
        cursor = await db.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()

async def get_uncontacted_companies(limit: int = 10, region: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch companies that currently have zero contacts, or least recently contacted."""
    db = await get_db_connection()
    try:
        query = """
            SELECT comp.*, (SELECT COUNT(*) FROM contacts WHERE company_id = comp.id) as contact_count
            FROM companies comp
            WHERE 1=1
        """
        params = []
        if region:
            query += " AND comp.region LIKE ?"
            params.append(f"%{region}%")
        
        query += " ORDER BY contact_count ASC, comp.tier ASC, comp.id DESC LIMIT ?"
        params.append(limit)
        
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

async def get_companies(region: Optional[str] = None, tier: Optional[int] = None) -> List[Dict[str, Any]]:
    db = await get_db_connection()
    try:
        query = "SELECT * FROM companies WHERE 1=1"
        params = []
        if region:
            query += " AND region = ?"
            params.append(region)
        if tier is not None:
            query += " AND tier = ?"
            params.append(tier)
        query += " ORDER BY discovered_at DESC"
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

# --- Contact Operations ---
async def add_contact(
    company_id: int,
    name: str,
    title: Optional[str] = None,
    title_normalized: Optional[str] = None,
    email: Optional[str] = None,
    source: str = "scraped",
    email_verified: bool = False,
    llm_confidence: Optional[float] = None,
    llm_reasoning: Optional[str] = None,
    status: str = "pending"
) -> Optional[int]:
    """Add a new contact for a company."""
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            """
            INSERT INTO contacts (
                company_id, name, title, title_normalized, email, 
                source, email_verified, llm_confidence, llm_reasoning, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id, name.strip(), title, title_normalized, email,
                source, 1 if email_verified else 0, llm_confidence, llm_reasoning, status
            )
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()

async def get_pending_contacts(limit: int = 10, offset: int = 0) -> List[Dict[str, Any]]:
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            """
            SELECT c.*, comp.name as company_name, comp.domain as company_domain, comp.region as company_region
            FROM contacts c
            JOIN companies comp ON c.company_id = comp.id
            WHERE c.status = 'pending'
            ORDER BY c.llm_confidence DESC NULLS LAST, c.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()

async def update_company_email_pattern(company_id: int, pattern: str) -> bool:
    """Store or update the confirmed corporate email pattern for a company."""
    if not pattern:
        return False
    db = await get_db_connection()
    try:
        await db.execute(
            "UPDATE companies SET email_pattern = ? WHERE id = ?",
            (pattern.strip().lower(), company_id)
        )
        await db.commit()
        return True
    finally:
        await db.close()

async def get_company_email_pattern(company_id: int) -> Optional[str]:
    """Retrieve the confirmed corporate email pattern for a company if known."""
    db = await get_db_connection()
    try:
        cursor = await db.execute("SELECT email_pattern FROM companies WHERE id = ?", (company_id,))
        row = await cursor.fetchone()
        return row["email_pattern"] if row and row["email_pattern"] else None
    finally:
        await db.close()

async def is_contact_already_seen(email: Optional[str], name: Optional[str] = None, company_id: Optional[int] = None) -> bool:
    """Comprehensive check to ensure contact has not already been discovered, contacted, approved, or rejected."""
    if not email and not (name and company_id):
        return False
    db = await get_db_connection()
    try:
        if email:
            cursor = await db.execute("SELECT id FROM contacts WHERE LOWER(email) = ?", (email.strip().lower(),))
            if await cursor.fetchone():
                return True
        if name and company_id:
            cursor = await db.execute("SELECT id FROM contacts WHERE LOWER(name) = ? AND company_id = ?", (name.strip().lower(), company_id))
            if await cursor.fetchone():
                return True
        return False
    finally:
        await db.close()

async def get_contact_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Check if contact with email already exists in DB for deduplication."""
    if not email:
        return None
    db = await get_db_connection()
    try:
        cursor = await db.execute("SELECT * FROM contacts WHERE LOWER(email) = ?", (email.strip().lower(),))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()

async def get_contact_by_id(contact_id: int) -> Optional[Dict[str, Any]]:

    db = await get_db_connection()
    try:
        cursor = await db.execute(
            """
            SELECT c.*, comp.name as company_name, comp.domain as company_domain, 
                   comp.region as company_region, comp.tech_stack_match
            FROM contacts c
            JOIN companies comp ON c.company_id = comp.id
            WHERE c.id = ?
            """,
            (contact_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()

async def update_contact_status(contact_id: int, status: str) -> bool:
    """Update contact status to 'approved' or 'rejected'."""
    if status not in ("pending", "approved", "rejected"):
        return False
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "UPDATE contacts SET status = ? WHERE id = ?",
            (status, contact_id)
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()

# --- Analytics / Status ---
async def get_pipeline_stats() -> Dict[str, Any]:
    db = await get_db_connection()
    try:
        companies_count = (await (await db.execute("SELECT COUNT(*) FROM companies")).fetchone())[0]
        contacts_total = (await (await db.execute("SELECT COUNT(*) FROM contacts")).fetchone())[0]
        contacts_pending = (await (await db.execute("SELECT COUNT(*) FROM contacts WHERE status = 'pending'")).fetchone())[0]
        contacts_approved = (await (await db.execute("SELECT COUNT(*) FROM contacts WHERE status = 'approved'")).fetchone())[0]
        contacts_rejected = (await (await db.execute("SELECT COUNT(*) FROM contacts WHERE status = 'rejected'")).fetchone())[0]
        emails_verified = (await (await db.execute("SELECT COUNT(*) FROM contacts WHERE email_verified = 1")).fetchone())[0]
        
        return {
            "companies": companies_count,
            "contacts_total": contacts_total,
            "contacts_pending": contacts_pending,
            "contacts_approved": contacts_approved,
            "contacts_rejected": contacts_rejected,
            "emails_verified": emails_verified
        }
    finally:
        await db.close()

# --- Apollo Multi-Key Credits Tracker ---
def get_current_week_start() -> str:
    today = datetime.date.today()
    start_of_week = today - datetime.timedelta(days=today.weekday())
    return start_of_week.isoformat()

async def get_apollo_credits_used(key_index: int = 0, week_start: Optional[str] = None) -> int:
    if not week_start:
        week_start = get_current_week_start()
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "SELECT credits_used FROM apollo_credit_log WHERE key_index = ? AND week_start = ?",
            (key_index, week_start)
        )
        row = await cursor.fetchone()
        return row["credits_used"] if row else 0
    finally:
        await db.close()

async def get_apollo_pool_status(week_start: Optional[str] = None) -> Dict[str, Any]:
    """Get usage statistics for all configured Apollo keys and any logged keys."""
    if not week_start:
        week_start = get_current_week_start()
    keys = config.APOLLO_KEYS
    cap_per_key = config.APOLLO_WEEKLY_CREDIT_CAP_PER_KEY
    
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "SELECT key_index, credits_used FROM apollo_credit_log WHERE week_start = ?",
            (week_start,)
        )
        rows = await cursor.fetchall()
        db_usage = {r["key_index"]: r["credits_used"] for r in rows}
    finally:
        await db.close()

    # Determine all key indices (from configured keys and database records)
    all_indices = set(range(len(keys))) if keys else {0}
    all_indices.update(db_usage.keys())
    
    key_statuses = []
    total_used = 0
    
    for i in sorted(all_indices):
        used = db_usage.get(i, 0)
        total_used += used
        key_statuses.append({
            "key_index": i,
            "credits_used": used,
            "cap": cap_per_key,
            "exhausted": used >= cap_per_key,
            "configured": i < len(keys) if keys else (i == 0)
        })
        
    total_keys = max(len(keys), len(all_indices))
    return {
        "week_start": week_start,
        "total_keys": total_keys,
        "total_used": total_used,
        "total_cap": total_keys * cap_per_key,
        "cap_per_key": cap_per_key,
        "keys": key_statuses
    }


async def get_apollo_daily_credits_used(key_index: int = 0, log_date: Optional[str] = None) -> int:
    if not log_date:
        log_date = datetime.date.today().isoformat()
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "SELECT credits_used FROM apollo_credit_daily_log WHERE key_index = ? AND log_date = ?",
            (key_index, log_date)
        )
        row = await cursor.fetchone()
        return row["credits_used"] if row else 0
    finally:
        await db.close()

async def increment_apollo_daily_credits(key_index: int = 0, log_date: Optional[str] = None, amount: int = 1) -> int:
    if not log_date:
        log_date = datetime.date.today().isoformat()
    db = await get_db_connection()
    try:
        await db.execute(
            """
            INSERT INTO apollo_credit_daily_log (key_index, log_date, credits_used, last_used_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key_index, log_date) DO UPDATE SET
                credits_used = credits_used + excluded.credits_used,
                last_used_at = CURRENT_TIMESTAMP
            """,
            (key_index, log_date, amount)
        )
        await db.commit()
    finally:
        await db.close()
    return await get_apollo_daily_credits_used(key_index=key_index, log_date=log_date)

async def increment_apollo_credits(key_index: int = 0, week_start: Optional[str] = None, amount: int = 1) -> int:
    if not week_start:
        week_start = get_current_week_start()
    db = await get_db_connection()
    try:
        await db.execute(
            """
            INSERT INTO apollo_credit_log (key_index, week_start, credits_used, last_used_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key_index, week_start) DO UPDATE SET
                credits_used = credits_used + excluded.credits_used,
                last_used_at = CURRENT_TIMESTAMP
            """,
            (key_index, week_start, amount)
        )
        await db.commit()
    finally:
        await db.close()
    # Also record in daily log
    await increment_apollo_daily_credits(key_index=key_index, amount=amount)
    return await get_apollo_credits_used(key_index=key_index, week_start=week_start)


async def get_next_apollo_page(location: str) -> int:
    """Get the next un-searched page number for a location to prevent duplicate fetches across keys."""
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "SELECT last_page FROM apollo_page_tracker WHERE location = ?",
            (location.strip().lower(),)
        )
        row = await cursor.fetchone()
        last_page = row["last_page"] if row else 0
        return last_page + 1
    finally:
        await db.close()

async def increment_apollo_page(location: str, page_fetched: int):
    """Update the last fetched page for a location in SQLite."""
    db = await get_db_connection()
    try:
        await db.execute(
            """
            INSERT INTO apollo_page_tracker (location, last_page, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(location) DO UPDATE SET
                last_page = MAX(excluded.last_page, apollo_page_tracker.last_page),
                updated_at = CURRENT_TIMESTAMP
            """,
            (location.strip().lower(), page_fetched)
        )
        await db.commit()
    finally:
        await db.close()

# --- Google Custom Search Daily Quota Tracker (100 free queries/day limit) ---

async def get_google_search_count_today() -> int:
    today_str = datetime.date.today().isoformat()
    db = await get_db_connection()
    try:
        cursor = await db.execute(
            "SELECT query_count FROM google_search_daily_log WHERE log_date = ?",
            (today_str,)
        )
        row = await cursor.fetchone()
        return row["query_count"] if row else 0
    finally:
        await db.close()

async def increment_google_search_count(amount: int = 1) -> int:
    today_str = datetime.date.today().isoformat()
    db = await get_db_connection()
    try:
        await db.execute(
            """
            INSERT INTO google_search_daily_log (log_date, query_count, last_queried_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(log_date) DO UPDATE SET
                query_count = query_count + excluded.query_count,
                last_queried_at = CURRENT_TIMESTAMP
            """,
            (today_str, amount)
        )
        await db.commit()
    finally:
        await db.close()
    return await get_google_search_count_today()


