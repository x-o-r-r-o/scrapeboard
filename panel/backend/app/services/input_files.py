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
    """User-facing format guide for /help and /formats."""
    exts = ", ".join(allowed_extensions(extensions))
    size = f"\n• Size limit: {max_upload_mb} MB (plan may be lower)" if max_upload_mb else ""
    return (
        "📁 How to upload inputs\n"
        "\n"
        "Send two documents (Telegram) or upload two files (panel):\n"
        "1) Keywords — caption or name must include “keywords”\n"
        "2) Locations — caption or name must include “locations”\n"
        "Then send /run (or Queue job in the panel).\n"
        "\n"
        f"Accepted types: {exts}\n"
        "Encoding: UTF-8\n"
        f"{size}"
        "\n"
        "TXT (and CSV without a header column):\n"
        "• One entry per line\n"
        "• Blank lines and # comments are ignored\n"
        "\n"
        "Keywords example:\n"
        "dentist\n"
        "plumber\n"
        "coffee shop\n"
        "\n"
        "Locations example (city,state,country):\n"
        "Austin,Texas,USA\n"
        "Miami,Florida,USA\n"
        "\n"
        "CSV (optional):\n"
        "• Keywords: header column named keyword (or query / search)\n"
        "• Locations: header column named location — or same one-per-line "
        "city,state,country format as TXT\n"
        "\n"
        "Jobs are not started if a file is empty, unreadable, wrong type, "
        "or has no queries/locations."
    )
