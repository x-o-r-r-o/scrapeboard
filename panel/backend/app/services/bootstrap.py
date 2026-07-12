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
from app.services.scrape_profiles import (
    ensure_default_profile,
    ensure_workers_have_default_profile,
)
from app.services.worker_config import build_package_scrape_defaults


async def bootstrap(db: AsyncSession) -> None:
    settings = get_settings()

    if not await db.get(SecuritySettings, 1):
        db.add(SecuritySettings(id=1))
    if not await db.get(BillingSettings, 1):
        db.add(BillingSettings(id=1))
    await db.flush()
    await ensure_default_profile(db)
    await ensure_captcha_settings(db)
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
                    scrape_defaults=build_package_scrape_defaults(threads=threads),
                    chunk_size=500,
                )
            )

    existing_cmds = {c.key for c in (await db.execute(select(BotCommand))).scalars().all()}
    for cmd in DEMO_COMMANDS:
        if cmd["key"] not in existing_cmds:
            db.add(BotCommand(**cmd))

    existing_wf = {w.key for w in (await db.execute(select(BotWorkflow))).scalars().all()}
    for i, wf in enumerate(DEMO_WORKFLOWS):
        if wf["key"] not in existing_wf:
            db.add(BotWorkflow(**{**wf, "sort_order": wf.get("sort_order", (i + 1) * 10)}))

    await db.commit()
    await ensure_workers_have_default_profile(db)
