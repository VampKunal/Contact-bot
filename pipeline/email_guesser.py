import re
from typing import List, Optional, Set

def clean_name_parts(name: str) -> tuple[str, str]:
    """Extract clean first and last name components."""
    # Remove honorifics and credentials
    cleaned = re.sub(r"\b(mr|ms|mrs|dr|prof|phd|er|ca)\b\.?", "", name, flags=re.IGNORECASE)
    # Remove special chars
    cleaned = re.sub(r"[^a-zA-Z\s]", "", cleaned).strip().lower()
    parts = [p for p in cleaned.split() if len(p) > 1]
    
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]

def generate_email_permutations(name: str, domain: str, known_pattern: Optional[str] = None) -> List[str]:
    """
    Generate likely corporate email permutations for a name and domain.
    If a known pattern is provided (e.g. 'first.last', 'first', 'flast', 'firstlast'), prioritize it at index 0.
    """
    first, last = clean_name_parts(name)
    if not first:
        return []
    
    clean_dom = domain.lower().replace("http://", "").replace("https://", "").strip().rstrip("/")
    f = first[0]
    l = last[0] if last else ""

    patterns = []

    # Map pattern name to actual email template
    pattern_map = {}
    if last:
        pattern_map = {
            "first.last": f"{first}.{last}@{clean_dom}",
            "firstlast": f"{first}{last}@{clean_dom}",
            "flast": f"{f}{last}@{clean_dom}",
            "firstl": f"{first}{l}@{clean_dom}",
            "first_last": f"{first}_{last}@{clean_dom}",
            "first": f"{first}@{clean_dom}",
            "last.first": f"{last}.{first}@{clean_dom}",
            "last": f"{last}@{clean_dom}"
        }
        
        # If a known confirmed pattern exists for this company domain, put it first!
        if known_pattern and known_pattern in pattern_map:
            patterns.append(pattern_map[known_pattern])

        # Add all other standard patterns in order of general prevalence
        standard_order = ["first.last", "flast", "firstlast", "first", "firstl", "first_last", "last.first", "last"]
        for p in standard_order:
            val = pattern_map.get(p)
            if val and val not in patterns:
                patterns.append(val)
    else:
        patterns = [
            f"{first}@{clean_dom}",
            f"careers@{clean_dom}",
            f"contact@{clean_dom}",
            f"team@{clean_dom}"
        ]

    # Deduplicate while preserving order
    seen: Set[str] = set()
    result = []
    for email in patterns:
        if email not in seen:
            seen.add(email)
            result.append(email)

    return result

def detect_pattern(known_email: str, name: str) -> Optional[str]:
    """Detect which corporate pattern an existing valid email follows."""
    first, last = clean_name_parts(name)
    if not first or "@" not in known_email:
        return None
    
    local = known_email.split("@")[0].lower().strip()
    f = first[0]
    l = last[0] if last else ""

    if last:
        if local == f"{first}.{last}":
            return "first.last"
        if local == f"{first}{last}":
            return "firstlast"
        if local == f"{f}{last}":
            return "flast"
        if local == f"{first}{l}":
            return "firstl"
        if local == f"{first}_{last}":
            return "first_last"
        if local == f"{last}.{first}":
            return "last.first"
        if local == last:
            return "last"
    if local == first:
        return "first"
    return None
