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
