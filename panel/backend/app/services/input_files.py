"""Validate and parse keywords/locations uploads for panel + Telegram.

Matches worker `gmaps_scraper.load_lines` semantics: UTF-8 text, one entry per
line, blank lines and `#` comments ignored. `.csv` is allowed as an extension;
optional CSV headers use real column names only (no invented schema).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Literal

Kind = Literal["keywords", "locations"]

DEFAULT_ALLOWED_EXTENSIONS = (".txt", ".csv")

# Optional CSV header names (first matching column is used). Not required for .txt
# or for location lines written as city,state,country without a header.
KEYWORD_COLUMNS = ("keyword", "keywords", "query", "search")
LOCATION_COLUMNS = ("location", "locations", "query_location")

_KIND_LABEL = {
    "keywords": "search queries",
    "locations": "locations",
}


class InputFileError(ValueError):
    """User-facing validation failure; message is safe to show in Telegram/API."""


def allowed_extensions(configured: list[str] | None = None) -> list[str]:
    raw = configured if configured else list(DEFAULT_ALLOWED_EXTENSIONS)
    out: list[str] = []
    for e in raw:
        e = str(e).strip().lower()
        if not e:
            continue
        if not e.startswith("."):
            e = f".{e}"
        if e not in out:
            out.append(e)
    return out or list(DEFAULT_ALLOWED_EXTENSIONS)


def check_extension(filename: str | None, configured: list[str] | None = None) -> str:
    """Return normalized extension (e.g. '.txt') or raise InputFileError."""
    allowed = allowed_extensions(configured)
    name = (filename or "").strip()
    ext = Path(name).suffix.lower() if name else ""
    if not ext or ext not in allowed:
        pretty = ", ".join(allowed)
        raise InputFileError(f"File must be {pretty}")
    return ext


def decode_utf8(data: bytes) -> str:
    if data is None or len(data) == 0:
        raise InputFileError("File is empty")
    if not data.strip():
        raise InputFileError("File is empty")
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise InputFileError("File encoding must be UTF-8 (could not decode)")


def _columns_for(kind: Kind) -> tuple[str, ...]:
    return KEYWORD_COLUMNS if kind == "keywords" else LOCATION_COLUMNS


def _first_content_line(text: str) -> str | None:
    for raw in text.splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            return s
    return None


def _parse_line_mode(text: str, kind: Kind) -> list[str]:
    known = {c.lower() for c in _columns_for(kind)}
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    if out and out[0].lower() in known:
        out = out[1:]
    if not out:
        raise InputFileError(f"No {_KIND_LABEL[kind]} found")
    return out


def _parse_csv_column(text: str, kind: Kind) -> list[str]:
    known = _columns_for(kind)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise InputFileError(f"No {_KIND_LABEL[kind]} found")
    fields = {str(f).strip().lower(): f for f in reader.fieldnames if f and str(f).strip()}
    col_key = None
    for name in known:
        if name in fields:
            col_key = fields[name]
            break
    if col_key is None:
        hint = known[0]
        alts = " / ".join(known[1:])
        extra = f" (or {alts})" if alts else ""
        raise InputFileError(f"CSV needs a header column named {hint}{extra}")
    out: list[str] = []
    for row in reader:
        val = (row.get(col_key) or "").strip()
        if val and not val.startswith("#"):
            out.append(val)
    if not out:
        raise InputFileError(f"No {_KIND_LABEL[kind]} found")
    return out


def parse_entries(
    data: bytes,
    kind: Kind,
    *,
    filename: str | None = None,
    check_ext: bool = False,
    configured_extensions: list[str] | None = None,
) -> list[str]:
    """Parse upload bytes into entries the worker can consume.

    Raises InputFileError with a clear fix message on failure.
    """
    if kind not in ("keywords", "locations"):
        raise InputFileError("Internal error: unknown input kind")

    ext = ""
    if check_ext or filename:
        try:
            ext = check_extension(filename, configured_extensions)
        except InputFileError:
            if check_ext:
                raise
            ext = Path((filename or "").strip()).suffix.lower()

    text = decode_utf8(data)
    first = _first_content_line(text)
    if not first:
        raise InputFileError(f"No {_KIND_LABEL[kind]} found")

    known = {c.lower() for c in _columns_for(kind)}
    try:
        cells = [c.strip().lower() for c in next(csv.reader([first])) if c.strip()]
    except Exception:
        cells = [first.lower()]

    # Single-cell header (e.g. "keyword" or "location") → line mode so location
    # rows like Austin,Texas,USA are not split by DictReader.
    if len(cells) == 1 and cells[0] in known:
        return _parse_line_mode(text, kind)

    # Multi-column CSV with a recognized header column → extract that column.
    if len(cells) > 1 and any(c in known for c in cells):
        return _parse_csv_column(text, kind)

    # Keywords .csv with multiple columns but no keyword/query header → reject.
    # Locations often use city,state,country without a header — keep line mode.
    if ext == ".csv" and kind == "keywords" and len(cells) > 1:
        hint = KEYWORD_COLUMNS[0]
        alts = " / ".join(KEYWORD_COLUMNS[1:])
        raise InputFileError(f"CSV needs a header column named {hint} (or {alts})")

    return _parse_line_mode(text, kind)


def entries_to_bytes(entries: list[str]) -> bytes:
    return ("\n".join(entries) + "\n").encode("utf-8")


def parse_email_list(
    data: bytes,
    *,
    filename: str | None = None,
    check_ext: bool = False,
    configured_extensions: list[str] | None = None,
) -> list[str]:
    """Parse one-email-per-line or CSV with an email column."""
    if check_ext:
        check_extension(filename, configured_extensions)
    text = decode_utf8(data)
    emails: list[str] = []
    seen: set[str] = set()

    # CSV with email header?
    first = _first_content_line(text)
    if first and "," in first:
        lower = first.lower()
        if "email" in lower.split(",")[0] or any(
            h.strip().lower() in ("email", "e-mail", "mail") for h in first.split(",")
        ):
            try:
                reader = csv.DictReader(io.StringIO(text))
                col = None
                if reader.fieldnames:
                    for name in reader.fieldnames:
                        if name and name.strip().lower() in ("email", "e-mail", "mail", "emails"):
                            col = name
                            break
                if col:
                    for row in reader:
                        raw = (row.get(col) or "").strip()
                        if not raw or raw.startswith("#"):
                            continue
                        key = raw.lower()
                        if key not in seen:
                            seen.add(key)
                            emails.append(raw)
                    if emails:
                        return emails
            except csv.Error:
                pass

    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        # Take first cell if CSV-like without header
        if "," in s and "@" in s:
            s = s.split(",")[0].strip().strip('"')
        key = s.lower()
        if key not in seen:
            seen.add(key)
            emails.append(s)
    if not emails:
        raise InputFileError("Email file has no addresses")
    return emails


def validate_pair(
    kw_bytes: bytes,
    loc_bytes: bytes,
    *,
    keywords_name: str | None = None,
    locations_name: str | None = None,
    configured_extensions: list[str] | None = None,
    check_ext: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate both uploads; never returns empty lists."""
    keywords = parse_entries(
        kw_bytes,
        "keywords",
        filename=keywords_name,
        check_ext=check_ext,
        configured_extensions=configured_extensions,
    )
    locations = parse_entries(
        loc_bytes,
        "locations",
        filename=locations_name,
        check_ext=check_ext,
        configured_extensions=configured_extensions,
    )
    return keywords, locations


def formats_help_text(*, max_upload_mb: int | None = None, extensions: list[str] | None = None) -> str:
    """Short upload summary (legacy helper). Prefer /help + TELEGRAM_USERS.md attachment."""
    exts = ", ".join(allowed_extensions(extensions))
    size = f"\n• Size limit: {max_upload_mb} MB (plan may be lower)" if max_upload_mb else ""
    return (
        "📁 Upload inputs (Telegram or panel)\n"
        "\n"
        "1) Send a .txt/.csv document with a caption:\n"
        "   • keywords / dork — search queries\n"
        "   • locations / region — cities or regions\n"
        "   • emails — for email_validate only\n"
        "2) /run source=…   (default source=gmaps)\n"
        "3) /status · /stop\n"
        "\n"
        "Your allowed scrapers: /scrapers\n"
        "Full written guide: TELEGRAM_USERS.md (repo / admin)\n"
        "\n"
        "—— Scrapers (source=) ——\n"
        "Maps:     gmaps (default)\n"
        "Commerce: tiktok_shop\n"
        "Search:   google_search  (+ use_dork=yes for Google dorks)\n"
        "Email:    email_harvest  (+ validate_after=yes)\n"
        "          email_validate (emails file only)\n"
        "Facebook: facebook_pages | facebook_groups |\n"
        "          facebook_posts | facebook_comments\n"
        "Social:   youtube | reddit | pinterest | tiktok |\n"
        "          instagram | linkedin | twitter\n"
        "\n"
        "—— /run examples ——\n"
        "/run source=gmaps threads=2 scrape_websites=yes\n"
        "/run source=gmaps scrape_websites=no\n"
        "/run source=google_search use_dork=yes\n"
        "/run source=email_validate\n"
        "/run source=email_harvest validate_after=yes\n"
        "/run source=tiktok_shop\n"
        "/run source=youtube max_results=30\n"
        "\n"
        f"Accepted types: {exts} · UTF-8{size}\n"
        "\n"
        "TXT: one entry per line (# comments OK)\n"
        "\n"
        "Keywords:\n"
        "dentist\n"
        "plumber\n"
        "\n"
        "Locations (city,state,country):\n"
        "Austin,Texas,USA\n"
        "Miami,Florida,USA\n"
        "\n"
        "Google dorks (source=google_search use_dork=yes):\n"
        'site:linkedin.com/in "dentist" Austin\n'
        "filetype:pdf invoice 2024\n"
        "(locations optional — omit or use -)\n"
        "\n"
        "Emails (source=email_validate, caption emails):\n"
        "one@example.com\n"
        "two@example.com\n"
        "\n"
        "CSV headers (optional): keyword/query · location · email\n"
        "\n"
        "Jobs are rejected if a file is empty, unreadable, or wrong type."
    )
