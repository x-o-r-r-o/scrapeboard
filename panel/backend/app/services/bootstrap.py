from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.models import (
    BillingSettings,
    BotCommand,
    BotSettings,
    BotWorkflow,
    Package,
    SecuritySettings,
    User,
)
from app.bot.demos import DEMO_COMMANDS, DEMO_WORKFLOWS
from app.services.captcha_settings import ensure_captcha_settings
from app.services.scraper_settings import ensure_scraper_settings
from app.services.scrape_profiles import (
    ensure_default_profile,
    ensure_workers_have_default_profile,
)
from app.services.worker_config import build_package_scrape_defaults
from app.services.scraper_registry import DEFAULT_ALLOWED_SOURCES, normalize_source_list


async def bootstrap(db: AsyncSession) -> None:
    settings = get_settings()

    if not await db.get(SecuritySettings, 1):
        db.add(SecuritySettings(id=1))
    if not await db.get(BillingSettings, 1):
        db.add(BillingSettings(id=1))
    await db.flush()
    await ensure_default_profile(db)
    await ensure_captcha_settings(db)
    await ensure_scraper_settings(db)
    if not await db.get(BotSettings, 1):
        db.add(BotSettings(id=1))

    admin = (
        await db.execute(select(User).where(User.username == settings.bootstrap_admin_username))
    ).scalar_one_or_none()
    if not admin:
        db.add(
            User(
                username=settings.bootstrap_admin_username,
                email=settings.bootstrap_admin_email,
                password_hash=hash_password(settings.bootstrap_admin_password),
                role="admin",
                must_change_password=True,
                totp_enabled=False,
                perms={},
            )
        )

    pkg_count = (await db.execute(select(Package))).scalars().first()
    if not pkg_count:
        for slug, name, tier, price, threads, upload in (
            ("basic", "Basic", 1, 10, 2, 2),
            ("pro", "Pro", 2, 25, 5, 10),
            ("max", "Max", 3, 60, 12, 50),
        ):
            db.add(
                Package(
                    slug=slug,
                    name=name,
                    tier=tier,
                    price_usdt=price,
                    duration_days=30,
                    threads=threads,
                    max_upload_mb=upload,
                    allowed_sources=list(DEFAULT_ALLOWED_SOURCES),
                    scrape_defaults=build_package_scrape_defaults(threads=threads),
                    chunk_size=500,
                )
            )
    else:
        # Backfill allowed_sources on existing packages (Phase A gmaps-only → B/C)
        for pkg in (await db.execute(select(Package))).scalars().all():
            current = list(getattr(pkg, "allowed_sources", None) or [])
            normalized = normalize_source_list(current)
            if not current or current == ["gmaps"]:
                normalized = list(DEFAULT_ALLOWED_SOURCES)
            else:
                extras: list[str] = []
                if "email_validate" not in normalized and "email_harvest" in normalized:
                    extras.append("email_validate")
                if any(
                    s in normalized
                    for s in (
                        "tiktok_shop",
                        "google_search",
                        "email_harvest",
                        "email_validate",
                        "youtube",
                    )
                ):
                    for sid in (
                        "youtube",
                        "reddit",
                        "pinterest",
                        "tiktok",
                        "facebook_pages",
                        "facebook_groups",
                        "facebook_posts",
                        "facebook_comments",
                        "instagram",
                        "linkedin",
                        "twitter",
                    ):
                        if sid not in normalized and sid not in extras:
                            extras.append(sid)
                if extras:
                    normalized = list(normalized) + extras
            if list(getattr(pkg, "allowed_sources", None) or []) != normalized:
                pkg.allowed_sources = normalized

    bot_settings = await db.get(BotSettings, 1)
    if bot_settings and (bot_settings.welcome_text or "").strip() in (
        "",
        "Welcome to the GMaps Scraper bot.",
        "Welcome!",
    ):
        bot_settings.welcome_text = (
            "Welcome to Scrapeboard — Maps, search, email & social scrapers."
        )

    SYNC_MENU_KEYS = frozenset(
        {
            "start",
            "help",
            "formats",
            "scrapers",
            "whoami",
            "packages",
            "buy",
            "upgrade",
            "paid",
            "subscription",
            "run",
            "status",
            "jobs",
            "stats",
            "stop",
            "support",
            "admin",
        }
    )
    existing_cmds = {c.key: c for c in (await db.execute(select(BotCommand))).scalars().all()}
    for cmd in DEMO_COMMANDS:
        if cmd["key"] not in existing_cmds:
            db.add(BotCommand(**cmd))
        else:
            row = existing_cmds[cmd["key"]]
            # Keep BotFather menu titles/descriptions in sync for built-in user commands.
            if cmd["key"] in SYNC_MENU_KEYS:
                row.title = cmd.get("title", row.title)
                row.description = cmd.get("description", row.description)
                row.command = cmd.get("command", row.command)
                row.audience = cmd.get("audience", row.audience)
                row.sort_order = cmd.get("sort_order", row.sort_order)
                if "enabled" in cmd:
                    row.enabled = bool(cmd["enabled"])
                if cmd.get("response_text"):
                    row.response_text = cmd["response_text"]

    existing_wf = {w.key for w in (await db.execute(select(BotWorkflow))).scalars().all()}
    for i, wf in enumerate(DEMO_WORKFLOWS):
        if wf["key"] not in existing_wf:
            db.add(BotWorkflow(**{**wf, "sort_order": wf.get("sort_order", (i + 1) * 10)}))
        else:
            # Refresh demo workflow descriptions that document the Telegram menu.
            if wf["key"] in ("onboarding", "job_progress", "job_stop", "job_run"):
                row = (
                    await db.execute(select(BotWorkflow).where(BotWorkflow.key == wf["key"]))
                ).scalar_one()
                row.description = wf.get("description", row.description)
                row.name = wf.get("name", row.name)
                row.definition = wf.get("definition", row.definition)

    await db.commit()
    await ensure_workers_have_default_profile(db)
