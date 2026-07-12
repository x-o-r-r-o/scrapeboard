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
    ScrapeSettings,
    SecuritySettings,
    User,
)
from app.bot.demos import DEMO_COMMANDS, DEMO_WORKFLOWS


async def bootstrap(db: AsyncSession) -> None:
    settings = get_settings()

    if not await db.get(SecuritySettings, 1):
        db.add(SecuritySettings(id=1))
    if not await db.get(BillingSettings, 1):
        db.add(BillingSettings(id=1))
    if not await db.get(ScrapeSettings, 1):
        db.add(ScrapeSettings(id=1))
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
        for p in (
            Package(slug="basic", name="Basic", tier=1, price_usdt=10, duration_days=30, threads=2, max_upload_mb=2),
            Package(slug="pro", name="Pro", tier=2, price_usdt=25, duration_days=30, threads=5, max_upload_mb=10),
            Package(slug="max", name="Max", tier=3, price_usdt=60, duration_days=30, threads=12, max_upload_mb=50),
        ):
            db.add(p)

    existing_cmds = {c.key for c in (await db.execute(select(BotCommand))).scalars().all()}
    for cmd in DEMO_COMMANDS:
        if cmd["key"] not in existing_cmds:
            db.add(BotCommand(**cmd))

    existing_wf = {w.key for w in (await db.execute(select(BotWorkflow))).scalars().all()}
    for wf in DEMO_WORKFLOWS:
        if wf["key"] not in existing_wf:
            db.add(BotWorkflow(**wf))

    await db.commit()
