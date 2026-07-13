"""Shared email extraction helpers for email_harvest / page enrichment."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import unquote

# Practical email pattern (avoids most trailing punctuation)
EMAIL_RE = re.compile(
    r"(?i)\b([a-z0-9][a-z0-9._%+\-]*@[a-z0-9][a-z0-9.\-]*\.[a-z]{2,})\b"
)

# Common false positives from minified JS / assets
_SKIP_DOMAINS = (
    "example.com",
    "example.org",
    "domain.com",
    "email.com",
    "yourdomain.com",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "googleapis.com",
    "gstatic.com",
    "w3.org",
    "jquery.com",
    "github.com",
    "localhost",
)

_SKIP_LOCAL = (
    "noreply",
    "no-reply",
    "donotreply",
    "mailer-daemon",
    "postmaster",
)


def normalize_email(raw: str) -> str | None:
    if not raw:
        return None
    email = unescape(unquote(str(raw))).strip().strip(".,;:<>()[]{}\"' ").lower()
    if not EMAIL_RE.fullmatch(email):
        # Allow if pattern matches inside after cleanup
        m = EMAIL_RE.search(email)
        if not m:
            return None
        email = m.group(1).lower()
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return None
    if domain.endswith(_SKIP_DOMAINS) or domain in _SKIP_DOMAINS:
        return None
    if any(domain.endswith(d) for d in _SKIP_DOMAINS):
        return None
    if local in _SKIP_LOCAL or local.startswith("noreply"):
        return None
    if len(email) > 254:
        return None
    return email


def extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in EMAIL_RE.finditer(text):
        email = normalize_email(m.group(1))
        if email and email not in seen:
            seen.add(email)
            found.append(email)
    # mailto:
    for m in re.finditer(r"(?i)mailto:([^\s\"'<>]+)", text):
        email = normalize_email(m.group(1).split("?")[0])
        if email and email not in seen:
            seen.add(email)
            found.append(email)
    return found


def classify_email(email: str) -> str:
    """Rough B2B vs free-mail tag."""
    free = (
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "icloud.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "mail.com",
        "yandex.com",
        "gmx.com",
    )
    domain = email.rsplit("@", 1)[-1]
    if domain in free:
        return "free_mail"
    return "business"


# --- Phones -----------------------------------------------------------------

# Prefer tel: hrefs; then international / NANP-ish numbers in text.
_TEL_HREF_RE = re.compile(r"(?i)tel:([+\d][\d\-.\s()extEXT]{5,24})")
_PHONE_RE = re.compile(
    r"(?<![\w./])("
    r"(?:\+|00)\d{1,3}[\s\-.]*(?:\(?\d{1,4}\)?[\s\-.]*)?(?:\d[\s\-.]*){6,14}\d"
    r"|"
    r"\(?\d{2,4}\)?[\s\-.]?\d{3}[\s\-.]?\d{3,4}"
    r"|"
    r"\d{3}[\s\-.]?\d{3}[\s\-.]?\d{4}"
    r")(?![\w./])"
)

_PHONE_BAD = re.compile(
    r"(?i)\b(20\d{2}|19\d{2}|isbn|order|zip|postal|tracking|version)\b"
)


def normalize_phone(raw: str) -> str | None:
    if not raw:
        return None
    s = unescape(unquote(str(raw))).strip()
    s = re.sub(r"(?i)\s*(ext|extension|x)\.?\s*\d+\s*$", "", s).strip()
    s = s.strip(".,;:<>()[]{}\"' ")
    digits = re.sub(r"[^\d+]", "", s)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    core = digits.lstrip("+")
    if not core.isdigit():
        return None
    if len(core) < 7 or len(core) > 15:
        return None
    # Drop obvious non-phones (years, short codes alone)
    if len(core) == 4 or (len(core) == 8 and core.startswith("20")):
        return None
    if digits.startswith("+"):
        return "+" + core
    return core


def extract_phones_from_text(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str):
        phone = normalize_phone(raw)
        if not phone:
            return
        key = re.sub(r"\D", "", phone)
        if key in seen:
            return
        # Avoid matching pure years / ids buried in long text snippets
        if _PHONE_BAD.search(raw) and len(key) <= 8:
            return
        seen.add(key)
        found.append(phone)

    for m in _TEL_HREF_RE.finditer(text):
        _add(m.group(1))
    for m in _PHONE_RE.finditer(text):
        _add(m.group(1))
    return found


def contacts_from_text(text: str) -> dict[str, str]:
    """First email + phone found in free text (SERP snippet, bio, page body)."""
    emails = extract_emails_from_text(text)
    phones = extract_phones_from_text(text)
    return {
        "email": emails[0] if emails else "",
        "phone": phones[0] if phones else "",
        "emails": "; ".join(emails[:5]),
        "phones": "; ".join(phones[:5]),
    }


def guess_display_name(*candidates: str) -> str:
    """Pick a human-looking name from title / channel / handle candidates."""
    skip = {
        "",
        "home",
        "login",
        "log in",
        "sign up",
        "instagram",
        "facebook",
        "youtube",
        "reddit",
        "pinterest",
        "tiktok",
        "linkedin",
        "twitter",
        "x",
    }
    for raw in candidates:
        if not raw:
            continue
        name = re.sub(r"\s+", " ", str(raw)).strip()
        # Drop trailing site suffixes: "Foo - YouTube", "Foo | Instagram"
        name = re.split(r"\s*[\|\-–—]\s*(?:YouTube|Instagram|Facebook|TikTok|LinkedIn|X|Twitter|Reddit|Pinterest)\b", name, maxsplit=1, flags=re.I)[0]
        name = name.strip(" |-\t\n@")
        if not name or name.lower() in skip:
            continue
        if len(name) < 2 or len(name) > 120:
            continue
        return name
    return ""
