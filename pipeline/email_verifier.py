import asyncio
import logging
import re
import dns.resolver
from typing import Tuple, Optional, Dict
from config import config

logger = logging.getLogger("email_verifier")

# Cache MX records and blocked port-25 hosts in memory per domain during a run
_mx_cache: Dict[str, Optional[str]] = {}
_blocked_mx_hosts: set = set()

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

def validate_email_syntax(email: str) -> bool:
    """Check basic RFC syntax format."""
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_REGEX.match(email.strip()))

async def get_mx_record(domain: str) -> Optional[str]:
    """Resolve highest priority MX host for a domain with caching."""
    clean_dom = domain.strip().lower().replace("http://", "").replace("https://", "").rstrip("/")
    if clean_dom in _mx_cache:
        return _mx_cache[clean_dom]

    try:
        # Run DNS resolution in a thread pool so it doesn't block asyncio event loop
        loop = asyncio.get_running_loop()
        answers = await loop.run_in_executor(
            None,
            lambda: dns.resolver.resolve(clean_dom, "MX", lifetime=3)
        )
        # Sort by MX preference (lowest number = highest priority)
        sorted_records = sorted(answers, key=lambda r: r.preference)
        if sorted_records:
            mx_host = str(sorted_records[0].exchange).rstrip(".")
            _mx_cache[clean_dom] = mx_host
            return mx_host
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.Timeout, Exception) as e:
        logger.debug(f"MX lookup failed for '{clean_dom}': {e}")
        _mx_cache[clean_dom] = None

    return None

async def verify_smtp_mailbox(
    email: str,
    timeout: int = None
) -> Tuple[bool, str]:
    """
    Verify if an email mailbox exists via DNS MX lookup and SMTP handshake with fast 1.5s fail-safe timeout.
    Returns: (is_valid: bool, reason: str)
    """
    timeout = timeout or 1.5
    sender_domain = config.SMTP_SENDER_DOMAIN or "gmail.com"
    sender_email = f"verify@{sender_domain}"

    if not validate_email_syntax(email):
        return False, "Invalid email syntax format"

    domain = email.split("@")[1]
    mx_host = await get_mx_record(domain)

    if not mx_host:
        return False, f"Domain '{domain}' has no valid MX records"

    # Fast-path if this MX host is known to block port 25 on the local network/ISP
    if mx_host in _blocked_mx_hosts:
        return True, f"Domain MX verified ({mx_host})"

    # Simulated SMTP handshake
    reader = None
    writer = None
    try:
        # Connect to MX host on standard port 25
        connect_task = asyncio.open_connection(mx_host, 25)
        reader, writer = await asyncio.wait_for(connect_task, timeout=timeout)

        # Read banner
        banner = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not banner.startswith(b"220"):
            return False, f"Unexpected SMTP banner: {banner.decode(errors='ignore').strip()}"

        # Send HELO
        writer.write(f"HELO {sender_domain}\r\n".encode())
        await writer.drain()
        helo_resp = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not helo_resp.startswith(b"250"):
            return False, f"HELO rejected: {helo_resp.decode(errors='ignore').strip()}"

        # Send MAIL FROM
        writer.write(f"MAIL FROM:<{sender_email}>\r\n".encode())
        await writer.drain()
        mail_resp = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not mail_resp.startswith(b"250"):
            return False, f"MAIL FROM rejected: {mail_resp.decode(errors='ignore').strip()}"

        # Send RCPT TO (the key verification test)
        writer.write(f"RCPT TO:<{email}>\r\n".encode())
        await writer.drain()
        rcpt_resp = await asyncio.wait_for(reader.readline(), timeout=timeout)
        rcpt_code = rcpt_resp[:3]

        # Send QUIT immediately
        try:
            writer.write(b"QUIT\r\n")
            await writer.drain()
        except Exception:
            pass

        if rcpt_code == b"250":
            return True, "Mailbox exists and accepted recipient (250 OK)"
        elif rcpt_code in (b"550", b"551", b"552", b"553", b"554"):
            return False, f"Mailbox does not exist (SMTP {rcpt_code.decode()} rejection)"
        elif rcpt_code in (b"450", b"451", b"452"):
            return True, "Mail server greylisted/busy (4xx code, domain valid)"
        else:
            return False, f"SMTP server replied with: {rcpt_resp.decode(errors='ignore').strip()}"

    except asyncio.TimeoutError:
        _blocked_mx_hosts.add(mx_host)
        return True, f"Domain MX valid ({mx_host}), port 25 timed out"
    except (ConnectionRefusedError, OSError) as e:
        _blocked_mx_hosts.add(mx_host)
        return True, f"Domain MX valid ({mx_host}), port 25 connection blocked"
    except Exception as e:
        logger.debug(f"SMTP error for {email}: {e}")
        return False, f"Verification error: {str(e)}"
    finally:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
