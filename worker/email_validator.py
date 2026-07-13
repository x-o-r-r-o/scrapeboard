"""Email list validator (Phase D) — syntax, disposable, role, MX.

No browser required. Optional SMTP probe is off by default (slow / often blocked).
"""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from email_extract import classify_email, normalize_email

# Common disposable / throwaway domains (non-exhaustive; extend over time)
DISPOSABLE_DOMAINS = frozenset(
    {
        "mailinator.com",
        "guerrillamail.com",
        "guerrillamail.de",
        "sharklasers.com",
        "grr.la",
        "yopmail.com",
        "tempmail.com",
        "temp-mail.org",
        "throwawaymail.com",
        "10minutemail.com",
        "trashmail.com",
        "discard.email",
        "getnada.com",
        "maildrop.cc",
        "tempail.com",
        "fakeinbox.com",
        "emailondeck.com",
        "mintemail.com",
        "moakt.com",
        "mailnesia.com",
        "dispostable.com",
        "mailcatch.com",
        "mytemp.email",
        "tmpmail.org",
        "tmpmail.net",
        "trash-mail.com",
    }
)

ROLE_LOCALS = frozenset(
    {
        "admin",
        "administrator",
        "info",
        "contact",
        "support",
        "sales",
        "hello",
        "help",
        "office",
        "team",
        "noreply",
        "no-reply",
        "donotreply",
        "marketing",
        "billing",
        "abuse",
        "postmaster",
        "webmaster",
        "hostmaster",
    }
)

CSV_FIELDS = [
    "email",
    "status",
    "reason",
    "email_type",
    "syntax_ok",
    "mx_ok",
    "is_disposable",
    "is_role",
    "smtp_ok",
]


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def check_mx(domain: str) -> tuple[bool, str]:
    """Return (ok, detail). Prefers dnspython MX; falls back to A/AAAA lookup."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return False, "bad_domain"
    try:
        import dns.resolver  # type: ignore

        try:
            answers = dns.resolver.resolve(domain, "MX")
            if answers:
                return True, "mx"
        except Exception as e:
            # Try A as weak signal
            try:
                dns.resolver.resolve(domain, "A")
                return True, "a_record"
            except Exception:
                return False, f"dns:{type(e).__name__}"
    except ImportError:
        pass
    try:
        socket.getaddrinfo(domain, None)
        return True, "a_fallback"
    except socket.gaierror:
        return False, "nxdomain"
    except OSError as e:
        return False, f"os:{type(e).__name__}"


def smtp_probe(email: str, timeout: float = 8.0) -> tuple[bool | None, str]:
    """Best-effort RCPT TO probe. Returns (True/False/None, detail). None = inconclusive."""
    domain = _domain_of(email)
    mx_host = domain
    try:
        import dns.resolver  # type: ignore

        try:
            answers = list(dns.resolver.resolve(domain, "MX"))
            answers.sort(key=lambda r: int(r.preference))
            if answers:
                mx_host = str(answers[0].exchange).rstrip(".")
        except Exception:
            pass
    except ImportError:
        pass
    try:
        with socket.create_connection((mx_host, 25), timeout=timeout) as sock:
            sock.settimeout(timeout)
            f = sock.makefile("rwb")

            def _read():
                return f.readline().decode("utf-8", errors="replace")

            def _cmd(line: str):
                f.write((line + "\r\n").encode("ascii", errors="ignore"))
                f.flush()
                return _read()

            _read()  # banner
            _cmd("EHLO scrapeboard.local")
            _cmd("MAIL FROM:<verify@scrapeboard.local>")
            resp = _cmd(f"RCPT TO:<{email}>")
            _cmd("QUIT")
            code = resp[:3] if resp else ""
            if code in ("250", "251"):
                return True, resp.strip()[:120]
            if code in ("550", "551", "552", "553", "554"):
                return False, resp.strip()[:120]
            return None, resp.strip()[:120] or "no_response"
    except OSError as e:
        return None, f"smtp_error:{type(e).__name__}"


def validate_one(
    raw: str,
    *,
    check_disposable: bool = True,
    check_mx_flag: bool = True,
    do_smtp: bool = False,
) -> dict[str, Any]:
    email = normalize_email(raw)
    base = {
        "email": (raw or "").strip(),
        "status": "invalid",
        "reason": "",
        "email_type": "",
        "syntax_ok": "no",
        "mx_ok": "",
        "is_disposable": "",
        "is_role": "",
        "smtp_ok": "",
    }
    if not email:
        base["reason"] = "invalid_syntax"
        return base
    base["email"] = email
    base["syntax_ok"] = "yes"
    base["email_type"] = classify_email(email)
    local = email.split("@", 1)[0]
    domain = _domain_of(email)
    is_role = local in ROLE_LOCALS
    base["is_role"] = "yes" if is_role else "no"
    is_disp = domain in DISPOSABLE_DOMAINS
    base["is_disposable"] = "yes" if is_disp else "no"
    if check_disposable and is_disp:
        base["status"] = "invalid"
        base["reason"] = "disposable_domain"
        return base
    if check_mx_flag:
        ok, detail = check_mx(domain)
        base["mx_ok"] = "yes" if ok else "no"
        if not ok:
            base["status"] = "invalid"
            base["reason"] = f"no_mx:{detail}"
            return base
    else:
        base["mx_ok"] = "skipped"
    if do_smtp:
        smtp_ok, detail = smtp_probe(email)
        if smtp_ok is True:
            base["smtp_ok"] = "yes"
        elif smtp_ok is False:
            base["smtp_ok"] = "no"
            base["status"] = "invalid"
            base["reason"] = f"smtp_reject:{detail}"
            return base
        else:
            base["smtp_ok"] = "unknown"
    else:
        base["smtp_ok"] = "skipped"
    if is_role:
        base["status"] = "valid_role"
        base["reason"] = "role_account"
    else:
        base["status"] = "valid"
        base["reason"] = "ok"
    return base


def validate_emails(
    emails: list[str],
    *,
    threads: int = 4,
    check_disposable: bool = True,
    check_mx_flag: bool = True,
    do_smtp: bool = False,
    stop_event: threading.Event | None = None,
    on_progress=None,
) -> list[dict[str, Any]]:
    """Validate a list of raw email strings; preserve order."""
    if not emails:
        return []
    threads = max(1, min(int(threads or 1), 32))
    stop = stop_event or threading.Event()
    results: list[dict[str, Any] | None] = [None] * len(emails)
    done = 0
    lock = threading.Lock()

    def _one(idx: int, raw: str) -> None:
        nonlocal done
        if stop.is_set():
            return
        row = validate_one(
            raw,
            check_disposable=check_disposable,
            check_mx_flag=check_mx_flag,
            do_smtp=do_smtp,
        )
        results[idx] = row
        with lock:
            done += 1
            if on_progress:
                try:
                    on_progress(done, done)
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futs = [pool.submit(_one, i, e) for i, e in enumerate(emails)]
        for fut in as_completed(futs):
            if stop.is_set():
                break
            try:
                fut.result()
            except Exception:
                pass
    return [r or validate_one("") for r in results]


def execute_index_batch(
    args,
    keywords,
    locations,
    start,
    end,
    out_dir,
    ts,
    stop_event,
    solver=None,
    log=print,
    on_progress=None,
) -> tuple[int, int]:
    """Treat ``keywords`` as the email list; ``locations`` ignored for work units.

    Index range [start, end) slices the email list (not keyword×location).
    """
    _ = solver, locations
    emails = [str(e).strip() for e in (keywords or []) if str(e).strip()]
    start = max(0, int(start))
    end = min(int(end), len(emails))
    slice_emails = emails[start:end]
    if not slice_emails:
        return 0, 0

    check_disp = str(getattr(args, "check_disposable", True)).lower() not in ("0", "false", "no")
    check_mx_flag = str(getattr(args, "check_mx", True)).lower() not in ("0", "false", "no")
    do_smtp = str(getattr(args, "smtp_probe", False)).lower() in ("1", "true", "yes")

    threads = max(1, int(getattr(args, "threads", 4) or 4))
    stop = stop_event if stop_event is not None else threading.Event()
    rows = validate_emails(
        slice_emails,
        threads=threads,
        check_disposable=check_disp,
        check_mx_flag=check_mx_flag,
        do_smtp=do_smtp,
        stop_event=stop,
        on_progress=on_progress,
    )
    from browser_scrape_lib import write_csv

    n = write_csv(Path(out_dir) / f"email_validate_{ts}.csv", CSV_FIELDS, rows)
    log(f"[email_validate] validated {n} emails")
    return n, 0


def enrich_harvest_rows(
    rows: list[dict[str, Any]],
    *,
    threads: int = 4,
    check_disposable: bool = True,
    check_mx_flag: bool = True,
    do_smtp: bool = False,
    stop_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Attach validation columns onto email_harvest result rows."""
    emails = [str(r.get("email") or "") for r in rows]
    validated = validate_emails(
        emails,
        threads=threads,
        check_disposable=check_disposable,
        check_mx_flag=check_mx_flag,
        do_smtp=do_smtp,
        stop_event=stop_event,
    )
    out = []
    for base, v in zip(rows, validated):
        merged = dict(base)
        for k in ("status", "reason", "syntax_ok", "mx_ok", "is_disposable", "is_role", "smtp_ok"):
            merged[k] = v.get(k, "")
        if v.get("email_type"):
            merged["email_type"] = v["email_type"]
        out.append(merged)
    return out
