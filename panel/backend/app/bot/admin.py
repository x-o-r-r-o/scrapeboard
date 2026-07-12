"""Telegram admin command handlers — panel CRUD via bot for linked role=admin users."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.tg_auth import normalize_telegram_id
from app.core.security import generate_worker_token, hash_password, hash_worker_token
from app.models import (
    AuditLog,
    BotSettings,
    Job,
    JobChunk,
    Order,
    Package,
    ProxyPool,
    Subscription,
    User,
    UserWorker,
    WorkerNode,
)
from app.services import billing as billing_svc
from app.services import jobs as jobs_svc
from app.services.captcha_settings import get_captcha_settings
from app.services.perms import DEFAULT_USER_PERMS, normalize_perms
from app.services.worker_config import DEFAULT_WORKER_CONFIG, normalize_worker_config

PAGE = 12

# Slash commands gated by role=admin + admin_commands_enabled (runtime enforces).
ADMIN_COMMANDS = frozenset(
    {
        "/admin",
        "/users",
        "/userinfo",
        "/adduser",
        "/setname",
        "/settg",
        "/setperm",
        "/deluser",
        "/disable",
        "/enable",
        "/subs",
        "/grant",
        "/revoke",
        "/extend",
        "/pending",
        "/approve",
        "/reject",
        "/servers",
        "/workers",
        "/worker",
        "/addworker",
        "/editworker",
        "/workerdrain",
        "/workeron",
        "/workeroff",
        "/workertoken",
        "/adminpkgs",
        "/addpkg",
        "/editpkg",
        "/disablepkg",
        "/alljobs",
        "/job",
        "/adminstop",
        "/proxies",
        "/proxy",
        "/captcha",
        "/botstatus",
        "/boton",
        "/botoff",
    }
)

ADMIN_MENU = """🛠 Telegram admin

Users
  /users [page] — list
  /userinfo <tg|id|user> — details + perms
  /adduser <telegram_id> [name] [pkg_slug] [days]
  /setname <key> <name> · /settg <key> <tg_id>
  /setperm <key> <perm>=<value>
  /disable|/enable <key> · /deluser <key> confirm

Subscriptions
  /subs [page] · /grant <key> <slug> [days]
  /revoke <key> · /extend <key> <days>
  /pending · /approve <order_id> · /reject <order_id> [reason]

Workers
  /workers [page] · /worker <id|name>
  /addworker <name> [max_browsers]
  /editworker <id|name> name=… max_browsers=…
  /workerdrain <id|name> [on|off]
  /workeron|/workeroff <id|name>
  /workertoken <id|name> — regenerate (token DM’d privately)

Packages
  /adminpkgs · /addpkg <slug> <name> <price> <days> [threads] [upload]
  /editpkg <slug> key=value… · /disablepkg <slug>

Jobs
  /alljobs [page] · /job <public_id> · /adminstop <public_id>

Infra / settings
  /proxies · /proxy <id> on|off
  /captcha [off] · /botstatus · /boton|/botoff

Runtime always requires role=admin + Bot Builder “Admin commands”.
Heavy scrape/worker config & secrets stay in the panel."""


def _parse_kv(args: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in args:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip().replace("-", "_")] = v.strip()
    return out


def _worker_online(w: WorkerNode) -> bool:
    if not w.last_seen_at:
        return False
    ts = w.last_seen_at if w.last_seen_at.tzinfo else w.last_seen_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts < timedelta(seconds=90)


def _set_worker_token(w: WorkerNode, raw: str) -> None:
    w.token_hash = hash_password(raw)
    w.token_prefix = raw[:8]
    w.token_lookup = hash_worker_token(raw)


def _install_hint(token: str) -> str:
    return (
        "python agent.py --setup\n"
        f"# or: python agent.py --panel-url <PANEL_URL> --token {token}"
    )


async def _find_user(db: AsyncSession, key: str) -> User | None:
    key = key.strip()
    tid = normalize_telegram_id(key, allow_group=False)
    if tid:
        by_tg = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
        if by_tg:
            return by_tg
        by_id = await db.get(User, int(tid))
        if by_id:
            return by_id
    return (await db.execute(select(User).where(User.username == key))).scalar_one_or_none()


async def _find_worker(db: AsyncSession, key: str) -> WorkerNode | None:
    key = key.strip()
    if key.isdigit():
        w = await db.get(WorkerNode, int(key))
        if w:
            return w
    return (await db.execute(select(WorkerNode).where(WorkerNode.name == key))).scalar_one_or_none()


async def _find_job(db: AsyncSession, key: str) -> Job | None:
    key = key.strip()
    j = (await db.execute(select(Job).where(Job.public_id == key))).scalar_one_or_none()
    if j:
        return j
    if key.isdigit():
        return await db.get(Job, int(key))
    return None


async def _find_pkg(db: AsyncSession, key: str) -> Package | None:
    key = key.strip()
    if key.isdigit():
        return await db.get(Package, int(key))
    return (await db.execute(select(Package).where(Package.slug == key))).scalar_one_or_none()


async def _lease_counts(db: AsyncSession) -> dict[int, int]:
    rows = (
        await db.execute(
            select(JobChunk.worker_id, func.count())
            .where(JobChunk.state == "leased", JobChunk.worker_id.is_not(None))
            .group_by(JobChunk.worker_id)
        )
    ).all()
    return {int(wid): int(cnt) for wid, cnt in rows if wid is not None}


async def _send_token_privately(
    send,
    token: str,
    chat_id: int,
    chat_type: str,
    admin: User,
    summary: str,
    raw_token: str,
) -> None:
    """Never put full worker tokens in groups; DM admin when possible."""
    is_private = chat_type == "private"
    body = f"{summary}\n\n🔑 Token (once):\n`{raw_token}`\n\n{_install_hint(raw_token)}"
    if is_private:
        await send(token, chat_id, body)
        return
    await send(token, chat_id, f"{summary}\nToken sent to your private chat (not shown in groups).")
    if admin.telegram_id:
        await send(token, int(admin.telegram_id), body)
    else:
        await send(token, chat_id, "Could not DM you — link your telegram_id on the panel admin user.")


async def handle_admin(
    *,
    db: AsyncSession,
    token: str,
    chat_id: int,
    chat_type: str,
    admin: User,
    cmd: str,
    args: list[str],
    send,
) -> None:
    """Dispatch one admin slash command. Caller already verified role + flag."""
    if cmd == "/admin":
        await send(
            token,
            chat_id,
            ADMIN_MENU,
            reply_markup={
                "keyboard": [
                    [{"text": "/users"}, {"text": "/subs"}, {"text": "/workers"}],
                    [{"text": "/alljobs"}, {"text": "/adminpkgs"}, {"text": "/pending"}],
                    [{"text": "/proxies"}, {"text": "/captcha"}, {"text": "/botstatus"}],
                    [{"text": "/help"}],
                ],
                "resize_keyboard": True,
            },
        )
        return

    # --- users ---
    if cmd == "/users":
        page = int(args[0]) if args and args[0].isdigit() else 1
        page = max(1, page)
        users = (await db.execute(select(User).order_by(User.id))).scalars().all()
        total = len(users)
        start = (page - 1) * PAGE
        chunk = users[start : start + PAGE]
        if not chunk:
            await send(token, chat_id, f"No users on page {page} (total {total}).")
            return
        lines = [f"Users · page {page}/{(total + PAGE - 1) // PAGE} ({total} total)"]
        for u in chunk:
            sub = await billing_svc.active_subscription(db, u)
            flag = "ON" if u.is_active else "OFF"
            plan = sub.package_name if sub else "-"
            lines.append(
                f"• {u.id} {u.username} [{u.role}/{flag}] tg={u.telegram_id or '-'} plan={plan}"
            )
        if start + PAGE < total:
            lines.append(f"\nNext: /users {page + 1}")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/userinfo":
        if not args:
            await send(token, chat_id, "Usage: /userinfo <telegram_id|user_id|username>")
            return
        u = await _find_user(db, args[0])
        if not u:
            await send(token, chat_id, "User not found.")
            return
        sub = await billing_svc.active_subscription(db, u)
        wids = (
            await db.execute(select(UserWorker.worker_id).where(UserWorker.user_id == u.id))
        ).scalars().all()
        perms = normalize_perms(u.perms)
        lines = [
            f"User #{u.id} {u.username}",
            f"role={u.role} active={u.is_active} tg={u.telegram_id or '-'}",
            f"email={u.email}",
            f"workers={list(wids) or '-'}",
            f"plan={sub.package_name if sub else '-'} "
            f"until={sub.expires_at.date() if sub else '-'}",
            "perms:",
        ]
        for k, v in perms.items():
            lines.append(f"  {k}={v}")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/adduser":
        if not args:
            await send(
                token,
                chat_id,
                "Usage: /adduser <telegram_id> [display_name] [package_slug] [days]",
            )
            return
        tid = normalize_telegram_id(args[0], allow_group=False)
        if not tid:
            await send(token, chat_id, "telegram_id must be numeric.")
            return
        existing = (await db.execute(select(User).where(User.telegram_id == tid))).scalar_one_or_none()
        if existing:
            await send(token, chat_id, f"Already linked to {existing.username}.")
            return
        # Optional: [display_name] [package_slug] [days] — if args[1] is a slug, skip display name
        pkg_slug = None
        days = None
        display = None
        if len(args) > 1:
            maybe_pkg = (
                await db.execute(select(Package).where(Package.slug == args[1]))
            ).scalar_one_or_none()
            if maybe_pkg:
                pkg_slug = args[1]
                if len(args) > 2 and args[2].isdigit():
                    days = int(args[2])
            else:
                display = args[1]
                if len(args) > 2:
                    pkg_slug = args[2]
                if len(args) > 3 and args[3].isdigit():
                    days = int(args[3])
        username = (display or f"tg_{tid}").strip()
        if (await db.execute(select(User).where(User.username == username))).scalar_one_or_none():
            username = f"tg_{tid}"
        email = f"tg_{tid}@telegram.local"
        user = User(
            username=username,
            email=email,
            password_hash=hash_password(secrets.token_urlsafe(24)),
            role="user",
            telegram_id=tid,
            perms=normalize_perms({**DEFAULT_USER_PERMS, "telegram_user": True}),
            must_change_password=True,
            totp_enabled=False,
        )
        db.add(user)
        db.add(
            AuditLog(
                actor_id=admin.id,
                action="telegram_user.create",
                detail={"telegram_id": tid, "username": username, "via": "telegram_admin"},
            )
        )
        await db.commit()
        await db.refresh(user)
        msg = f"✅ Created {user.username} (id={user.id}) tg={tid}"
        if pkg_slug:
            pkg = (await db.execute(select(Package).where(Package.slug == pkg_slug))).scalar_one_or_none()
            if not pkg:
                await send(token, chat_id, f"{msg}\n⚠️ Package {pkg_slug} not found — grant later.")
                return
            sub = await billing_svc.activate_subscription(db, user, pkg, duration_days=days)
            msg += f"\nGranted {pkg.name} until {sub.expires_at.date()}"
            await send(token, int(tid), f"✅ Access granted: {pkg.name} until {sub.expires_at.date()}.")
        await send(token, chat_id, msg)
        return

    if cmd == "/setname":
        if len(args) < 2:
            await send(token, chat_id, "Usage: /setname <tg|id|user> <new_username>")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        new_name = args[1].strip()
        clash = (
            await db.execute(select(User).where(User.username == new_name, User.id != target.id))
        ).scalar_one_or_none()
        if clash:
            await send(token, chat_id, "Username already exists.")
            return
        target.username = new_name
        db.add(AuditLog(actor_id=admin.id, action="user.update", detail={"user_id": target.id, "username": new_name}))
        await db.commit()
        await send(token, chat_id, f"✅ Renamed → {new_name}")
        return

    if cmd == "/settg":
        if len(args) < 2:
            await send(token, chat_id, "Usage: /settg <id|username> <telegram_id|none>")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        raw = args[1].strip().lower()
        if raw in ("none", "-", "unlink", "null"):
            target.telegram_id = None
        else:
            tid = normalize_telegram_id(args[1], allow_group=False)
            if not tid:
                await send(token, chat_id, "telegram_id must be numeric or 'none'.")
                return
            clash = (
                await db.execute(select(User).where(User.telegram_id == tid, User.id != target.id))
            ).scalar_one_or_none()
            if clash:
                await send(token, chat_id, f"Already linked to {clash.username}.")
                return
            target.telegram_id = tid
        db.add(AuditLog(actor_id=admin.id, action="user.update", detail={"user_id": target.id, "telegram_id": target.telegram_id}))
        await db.commit()
        await send(token, chat_id, f"✅ {target.username} tg={target.telegram_id or 'unlinked'}")
        return

    if cmd == "/setperm":
        if len(args) < 2 or "=" not in args[1]:
            await send(
                token,
                chat_id,
                "Usage: /setperm <key> <perm>=<value>\n"
                "perms: can_run, can_stop, can_upload_inputs, can_download, max_threads, max_upload_mb",
            )
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        if target.role == "admin":
            await send(token, chat_id, "Admins use full perms; edit panel users instead.")
            return
        k, v = args[1].split("=", 1)
        k = k.strip()
        v = v.strip()
        perms = dict(normalize_perms(target.perms))
        if k in ("can_run", "can_stop", "can_upload_inputs", "can_download", "telegram_user"):
            perms[k] = v.lower() in ("1", "true", "yes", "on")
        elif k in ("max_threads", "max_upload_mb"):
            if not v.isdigit():
                await send(token, chat_id, f"{k} must be a number.")
                return
            perms[k] = int(v)
        elif k == "allowed_engines":
            perms[k] = "all" if v == "all" else [x.strip() for x in v.split(",") if x.strip()]
        else:
            await send(token, chat_id, f"Unknown perm '{k}'.")
            return
        if target.perms and target.perms.get("telegram_user"):
            perms["telegram_user"] = True
        target.perms = normalize_perms(perms)
        db.add(AuditLog(actor_id=admin.id, action="user.update", detail={"user_id": target.id, "perm": k}))
        await db.commit()
        await send(token, chat_id, f"✅ {target.username} {k}={target.perms.get(k)}")
        return

    if cmd == "/deluser":
        if not args:
            await send(token, chat_id, "Usage: /deluser <tg|id|user> confirm")
            return
        if len(args) < 2 or args[-1].lower() != "confirm":
            key = args[0]
            await send(
                token,
                chat_id,
                f"This permanently deletes the user.\nConfirm: /deluser {key} confirm",
            )
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        if target.id == admin.id:
            await send(token, chat_id, "Cannot delete yourself.")
            return
        if target.role == "admin":
            await send(token, chat_id, "Cannot delete an admin via Telegram. Use the panel.")
            return
        uid = target.id
        uname = target.username
        subs = (await db.execute(select(Subscription).where(Subscription.user_id == uid))).scalars().all()
        for s in subs:
            s.is_active = False
        await db.delete(target)
        db.add(AuditLog(actor_id=admin.id, action="user.delete", detail={"user_id": uid, "via": "telegram_admin"}))
        await db.commit()
        await send(token, chat_id, f"🗑 Deleted {uname} (id={uid}).")
        return

    if cmd == "/disable":
        if not args:
            await send(token, chat_id, "Usage: /disable <telegram_id|username>")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        if target.role == "admin":
            await send(token, chat_id, "Cannot disable an admin.")
            return
        target.is_active = False
        await db.commit()
        await send(token, chat_id, f"Disabled {target.username}.")
        return

    if cmd == "/enable":
        if not args:
            await send(token, chat_id, "Usage: /enable <telegram_id|username>")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        target.is_active = True
        await db.commit()
        await send(token, chat_id, f"Enabled {target.username}.")
        return

    # --- subscriptions ---
    if cmd == "/subs":
        page = int(args[0]) if args and args[0].isdigit() else 1
        page = max(1, page)
        now = datetime.now(timezone.utc)
        subs = (
            await db.execute(
                select(Subscription)
                .where(Subscription.is_active == True, Subscription.expires_at > now)  # noqa: E712
                .order_by(Subscription.expires_at)
            )
        ).scalars().all()
        total = len(subs)
        start = (page - 1) * PAGE
        chunk = subs[start : start + PAGE]
        if not chunk:
            await send(token, chat_id, f"No active subscriptions on page {page}.")
            return
        lines = [f"Active subs · page {page}/{(total + PAGE - 1) // PAGE}"]
        for s in chunk:
            u = await db.get(User, s.user_id)
            lines.append(
                f"• #{s.id} {u.username if u else s.user_id} · {s.package_name} "
                f"until {s.expires_at.date()} tg={u.telegram_id if u else '-'}"
            )
        if start + PAGE < total:
            lines.append(f"\nNext: /subs {page + 1}")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/grant":
        if len(args) < 2:
            await send(token, chat_id, "Usage: /grant <telegram_id|username> <package_slug> [days]")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        pkg = (await db.execute(select(Package).where(Package.slug == args[1]))).scalar_one_or_none()
        if not pkg:
            await send(token, chat_id, "Package not found.")
            return
        days = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
        sub = await billing_svc.activate_subscription(db, target, pkg, duration_days=days)
        await send(token, chat_id, f"✅ Granted {pkg.name} → {target.username} until {sub.expires_at.date()}")
        if target.telegram_id:
            await send(
                token,
                int(target.telegram_id),
                f"✅ Subscription {pkg.name} active until {sub.expires_at.date()}.",
            )
        return

    if cmd == "/revoke":
        if not args:
            await send(token, chat_id, "Usage: /revoke <telegram_id|username>")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        sub = await billing_svc.active_subscription(db, target)
        if not sub:
            await send(token, chat_id, "No active subscription.")
            return
        await billing_svc.revoke_subscription(db, sub)
        await send(token, chat_id, f"Revoked subscription for {target.username}.")
        if target.telegram_id:
            await send(token, int(target.telegram_id), "⚠️ Your subscription was revoked.")
        return

    if cmd == "/extend":
        if len(args) < 2 or not args[1].isdigit():
            await send(token, chat_id, "Usage: /extend <telegram_id|username> <days>")
            return
        target = await _find_user(db, args[0])
        if not target:
            await send(token, chat_id, "User not found.")
            return
        sub = await billing_svc.active_subscription(db, target)
        if not sub:
            sub = (
                await db.execute(
                    select(Subscription)
                    .where(Subscription.user_id == target.id)
                    .order_by(Subscription.expires_at.desc())
                )
            ).scalars().first()
        if not sub:
            await send(token, chat_id, "No subscription to extend. Use /grant first.")
            return
        sub = await billing_svc.extend_subscription(db, sub, int(args[1]))
        await send(token, chat_id, f"Extended {target.username} by {args[1]}d → {sub.expires_at.date()}")
        if target.telegram_id:
            await send(token, int(target.telegram_id), f"✅ Subscription extended until {sub.expires_at.date()}.")
        return

    if cmd == "/pending":
        orders = (
            await db.execute(select(Order).where(Order.status == "pending").order_by(Order.id.desc()))
        ).scalars().all()
        if not orders:
            await send(token, chat_id, "No pending orders.")
            return
        lines = []
        for o in orders:
            u = await db.get(User, o.user_id)
            p = await db.get(Package, o.package_id)
            lines.append(f"• order {o.id}: {u.username if u else o.user_id} → {p.slug if p else o.package_id}")
        lines.append("\n/approve <id> · /reject <id> [reason]")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/approve":
        if not args or not args[0].isdigit():
            await send(token, chat_id, "Usage: /approve <order_id>")
            return
        order = await db.get(Order, int(args[0]))
        if not order or order.status != "pending":
            await send(token, chat_id, "Pending order not found.")
            return
        user = await db.get(User, order.user_id)
        pkg = await db.get(Package, order.package_id)
        if not user or not pkg:
            await send(token, chat_id, "User/package missing.")
            return
        order.status = "approved"
        await db.commit()
        sub = await billing_svc.activate_subscription(db, user, pkg)
        await send(token, chat_id, f"✅ Approved order {order.id} → {user.username} until {sub.expires_at.date()}")
        if user.telegram_id:
            await send(token, int(user.telegram_id), f"✅ Subscription {pkg.name} active until {sub.expires_at.date()}.")
        return

    if cmd == "/reject":
        if not args or not args[0].isdigit():
            await send(token, chat_id, "Usage: /reject <order_id> [reason]")
            return
        order = await db.get(Order, int(args[0]))
        if not order or order.status != "pending":
            await send(token, chat_id, "Pending order not found.")
            return
        reason = " ".join(args[1:]).strip()
        order.status = "rejected"
        db.add(
            AuditLog(
                actor_id=admin.id,
                action="order.reject",
                detail={"order_id": order.id, "reason": reason},
            )
        )
        await db.commit()
        user = await db.get(User, order.user_id)
        await send(token, chat_id, f"Rejected order {order.id}.")
        if user and user.telegram_id:
            extra = f" Reason: {reason}" if reason else ""
            await send(token, int(user.telegram_id), f"❌ Your order #{order.id} was rejected.{extra}")
        return

    # --- workers ---
    if cmd in ("/servers", "/workers"):
        page = int(args[0]) if args and args[0].isdigit() else 1
        page = max(1, page)
        workers = (await db.execute(select(WorkerNode).order_by(WorkerNode.id))).scalars().all()
        leases = await _lease_counts(db)
        total = len(workers)
        start = (page - 1) * PAGE
        chunk = workers[start : start + PAGE]
        if not chunk:
            await send(token, chat_id, "No workers." if total == 0 else f"No workers on page {page}.")
            return
        lines = [f"Workers · page {page}/{(total + PAGE - 1) // PAGE}"]
        for w in chunk:
            online = "online" if _worker_online(w) else "offline"
            flags = []
            if w.is_draining:
                flags.append("drain")
            if not w.is_enabled:
                flags.append("disabled")
            flag_s = f" [{','.join(flags)}]" if flags else ""
            lines.append(
                f"• #{w.id} {w.name}: {online}{flag_s} "
                f"browsers={w.max_browsers} leases={leases.get(w.id, 0)} "
                f"cpu {w.cpu_percent:.0f}% mem {w.mem_percent:.0f}%"
            )
        if start + PAGE < total:
            lines.append(f"\nNext: /workers {page + 1}")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/worker":
        if not args:
            await send(token, chat_id, "Usage: /worker <id|name>")
            return
        w = await _find_worker(db, args[0])
        if not w:
            await send(token, chat_id, "Worker not found.")
            return
        leases = await _lease_counts(db)
        pool = await db.get(ProxyPool, w.proxy_pool_id) if w.proxy_pool_id else None
        online = "online" if _worker_online(w) else "offline"
        lines = [
            f"Worker #{w.id} {w.name}",
            f"status={online} enabled={w.is_enabled} draining={w.is_draining}",
            f"max_browsers={w.max_browsers} active_leases={leases.get(w.id, 0)}",
            f"token_prefix={w.token_prefix} pool={pool.name if pool else '-'}",
            f"host={w.hostname or '-'} os={w.host_os or '-'} ver={w.version or '-'}",
            f"last_seen={w.last_seen_at or 'never'}",
            f"cpu={w.cpu_percent:.0f}% mem={w.mem_percent:.0f}% disk={w.disk_percent:.0f}%",
        ]
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/addworker":
        if not args:
            await send(token, chat_id, "Usage: /addworker <name> [max_browsers]")
            return
        name = args[0].strip()
        if (await db.execute(select(WorkerNode).where(WorkerNode.name == name))).scalar_one_or_none():
            await send(token, chat_id, "Worker name already exists.")
            return
        max_b = int(args[1]) if len(args) > 1 and args[1].isdigit() else 2
        raw = generate_worker_token()
        w = WorkerNode(
            name=name,
            max_browsers=max_b,
            worker_config=normalize_worker_config(dict(DEFAULT_WORKER_CONFIG)),
        )
        _set_worker_token(w, raw)
        db.add(w)
        await db.commit()
        await db.refresh(w)
        await _send_token_privately(
            send,
            token,
            chat_id,
            chat_type,
            admin,
            f"✅ Worker #{w.id} {w.name} created (max_browsers={max_b}).",
            raw,
        )
        return

    if cmd == "/editworker":
        if len(args) < 2:
            await send(token, chat_id, "Usage: /editworker <id|name> name=… max_browsers=…")
            return
        w = await _find_worker(db, args[0])
        if not w:
            await send(token, chat_id, "Worker not found.")
            return
        kv = _parse_kv(args[1:])
        if not kv:
            await send(token, chat_id, "Provide name=… and/or max_browsers=…")
            return
        if "name" in kv:
            clash = (
                await db.execute(
                    select(WorkerNode).where(WorkerNode.name == kv["name"], WorkerNode.id != w.id)
                )
            ).scalar_one_or_none()
            if clash:
                await send(token, chat_id, "Name already taken.")
                return
            w.name = kv["name"]
        if "max_browsers" in kv:
            if not kv["max_browsers"].isdigit():
                await send(token, chat_id, "max_browsers must be a number.")
                return
            w.max_browsers = max(1, int(kv["max_browsers"]))
        await db.commit()
        await send(token, chat_id, f"✅ Updated worker #{w.id} {w.name} (max_browsers={w.max_browsers})")
        return

    if cmd == "/workerdrain":
        if not args:
            await send(token, chat_id, "Usage: /workerdrain <id|name> [on|off]")
            return
        w = await _find_worker(db, args[0])
        if not w:
            await send(token, chat_id, "Worker not found.")
            return
        if len(args) > 1:
            w.is_draining = args[1].lower() in ("on", "1", "true", "yes")
        else:
            w.is_draining = not w.is_draining
        await db.commit()
        await send(token, chat_id, f"Worker {w.name} draining={w.is_draining}")
        return

    if cmd == "/workeron":
        if not args:
            await send(token, chat_id, "Usage: /workeron <id|name>")
            return
        w = await _find_worker(db, args[0])
        if not w:
            await send(token, chat_id, "Worker not found.")
            return
        w.is_enabled = True
        await db.commit()
        await send(token, chat_id, f"Enabled worker {w.name}.")
        return

    if cmd == "/workeroff":
        if not args:
            await send(token, chat_id, "Usage: /workeroff <id|name>")
            return
        w = await _find_worker(db, args[0])
        if not w:
            await send(token, chat_id, "Worker not found.")
            return
        w.is_enabled = False
        await db.commit()
        await send(token, chat_id, f"Disabled worker {w.name}.")
        return

    if cmd == "/workertoken":
        if not args:
            await send(token, chat_id, "Usage: /workertoken <id|name>")
            return
        w = await _find_worker(db, args[0])
        if not w:
            await send(token, chat_id, "Worker not found.")
            return
        raw = generate_worker_token()
        _set_worker_token(w, raw)
        await db.commit()
        await _send_token_privately(
            send,
            token,
            chat_id,
            chat_type,
            admin,
            f"🔄 Token rotated for worker #{w.id} {w.name}.",
            raw,
        )
        return

    # --- packages ---
    if cmd == "/adminpkgs":
        pkgs = (await db.execute(select(Package).order_by(Package.tier, Package.id))).scalars().all()
        if not pkgs:
            await send(token, chat_id, "No packages.")
            return
        lines = ["Packages (admin):"]
        for p in pkgs:
            flag = "ON" if p.is_active else "OFF"
            lines.append(
                f"• {p.slug} [{flag}] {p.name} · {p.price_usdt} USDT / {p.duration_days}d · "
                f"threads={p.threads} upload={p.max_upload_mb}MB tier={p.tier}"
            )
        lines.append("\n/addpkg · /editpkg · /disablepkg · panel for scrape_defaults")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/addpkg":
        if len(args) < 4:
            await send(
                token,
                chat_id,
                "Usage: /addpkg <slug> <name> <price_usdt> <days> [threads] [upload_mb]",
            )
            return
        slug, name = args[0].strip(), args[1].strip()
        try:
            price = float(args[2])
            days = int(args[3])
        except ValueError:
            await send(token, chat_id, "price must be float, days must be int.")
            return
        threads = int(args[4]) if len(args) > 4 and args[4].isdigit() else 2
        upload = int(args[5]) if len(args) > 5 and args[5].isdigit() else 5
        if (await db.execute(select(Package).where(Package.slug == slug))).scalar_one_or_none():
            await send(token, chat_id, "Slug already exists.")
            return
        from app.services.worker_config import build_package_scrape_defaults

        pkg = Package(
            slug=slug,
            name=name,
            price_usdt=price,
            duration_days=days,
            threads=threads,
            max_upload_mb=upload,
            scrape_defaults=build_package_scrape_defaults(threads=threads),
            chunk_size=500,
            is_active=True,
        )
        db.add(pkg)
        await db.commit()
        await db.refresh(pkg)
        await send(token, chat_id, f"✅ Package {pkg.slug} created (id={pkg.id}).")
        return

    if cmd == "/editpkg":
        if len(args) < 2:
            await send(
                token,
                chat_id,
                "Usage: /editpkg <slug> name=… price_usdt=… duration_days=… threads=… "
                "max_upload_mb=… tier=… is_active=0|1",
            )
            return
        pkg = await _find_pkg(db, args[0])
        if not pkg:
            await send(token, chat_id, "Package not found.")
            return
        kv = _parse_kv(args[1:])
        if not kv:
            await send(token, chat_id, "Provide at least one key=value.")
            return
        allowed = {
            "name",
            "price_usdt",
            "duration_days",
            "threads",
            "max_upload_mb",
            "tier",
            "is_active",
            "description",
            "chunk_size",
        }
        for k, v in kv.items():
            if k not in allowed:
                await send(token, chat_id, f"Unknown field '{k}'. Allowed: {', '.join(sorted(allowed))}")
                return
            if k in ("duration_days", "threads", "max_upload_mb", "tier", "chunk_size"):
                if not v.isdigit():
                    await send(token, chat_id, f"{k} must be int.")
                    return
                setattr(pkg, k, int(v))
            elif k == "price_usdt":
                try:
                    pkg.price_usdt = float(v)
                except ValueError:
                    await send(token, chat_id, "price_usdt must be a number.")
                    return
            elif k == "is_active":
                pkg.is_active = v.lower() in ("1", "true", "yes", "on")
            else:
                setattr(pkg, k, v)
        if "threads" in kv:
            cfg = dict(pkg.scrape_defaults or {})
            cfg["threads"] = pkg.threads
            pkg.scrape_defaults = normalize_worker_config(cfg)
        await db.commit()
        await send(token, chat_id, f"✅ Updated package {pkg.slug}.")
        return

    if cmd == "/disablepkg":
        if not args:
            await send(token, chat_id, "Usage: /disablepkg <slug>")
            return
        pkg = await _find_pkg(db, args[0])
        if not pkg:
            await send(token, chat_id, "Package not found.")
            return
        pkg.is_active = False
        await db.commit()
        await send(token, chat_id, f"Disabled package {pkg.slug}.")
        return

    # --- jobs ---
    if cmd == "/alljobs":
        page = int(args[0]) if args and args[0].isdigit() else 1
        page = max(1, page)
        jobs = (
            await db.execute(
                select(Job).order_by(Job.id.desc()).offset((page - 1) * PAGE).limit(PAGE)
            )
        ).scalars().all()
        if not jobs:
            await send(token, chat_id, f"No jobs on page {page}.")
            return
        total = (await db.execute(select(func.count()).select_from(Job))).scalar() or 0
        lines = [f"Jobs · page {page}/{(int(total) + PAGE - 1) // PAGE} ({total} total)"]
        for j in jobs:
            owner = await db.get(User, j.owner_id)
            pct = 100.0 * j.done_searches / j.total_searches if j.total_searches else 0.0
            lines.append(
                f"• {j.public_id} [{j.status}] {pct:.0f}% "
                f"owner={owner.username if owner else j.owner_id} rows={j.rows_saved}"
            )
        if page * PAGE < int(total):
            lines.append(f"\nNext: /alljobs {page + 1}")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/job":
        if not args:
            await send(token, chat_id, "Usage: /job <public_id>")
            return
        j = await _find_job(db, args[0])
        if not j:
            await send(token, chat_id, "Job not found.")
            return
        owner = await db.get(User, j.owner_id)
        pct = 100.0 * j.done_searches / j.total_searches if j.total_searches else 0.0
        leased = (
            await db.execute(
                select(JobChunk).where(JobChunk.job_id == j.id, JobChunk.state == "leased")
            )
        ).scalars().all()
        wnames = []
        for ch in leased:
            if ch.worker_id:
                ww = await db.get(WorkerNode, ch.worker_id)
                wnames.append(ww.name if ww else str(ch.worker_id))
        s = j.settings or {}
        lines = [
            f"Job {j.public_id} (#{j.id})",
            f"status={j.status} {pct:.1f}%",
            f"owner={owner.username if owner else j.owner_id} tg={owner.telegram_id if owner else '-'}",
            f"searches {j.done_searches:,}/{j.total_searches:,} · rows {j.rows_saved:,}",
            f"engine={s.get('engine', '—')} threads={jobs_svc.job_thread_count(j)}",
            f"leases: {', '.join(wnames) or 'none'}",
            f"created={j.created_at} started={j.started_at or '-'}",
        ]
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/adminstop":
        if not args:
            await send(token, chat_id, "Usage: /adminstop <public_id>")
            return
        j = await _find_job(db, args[0])
        if not j:
            await send(token, chat_id, "Job not found.")
            return
        if j.status not in ("queued", "running"):
            await send(token, chat_id, f"Job is {j.status} — nothing to stop.")
            return
        zip_path = await jobs_svc.finalize_job(db, j, cancelled=True)
        await db.refresh(j)
        msg = f"⏹ Stopped {j.public_id} (status={j.status}). Rows: {j.rows_saved}."
        if zip_path:
            msg += " Partial results ready."
        await send(token, chat_id, msg)
        owner = await db.get(User, j.owner_id)
        if owner and owner.telegram_id:
            await send(token, int(owner.telegram_id), f"⚠️ Admin stopped your job {j.public_id}.")
        return

    # --- proxies / captcha / bot ---
    if cmd == "/proxies":
        pools = (await db.execute(select(ProxyPool).order_by(ProxyPool.id))).scalars().all()
        if not pools:
            await send(token, chat_id, "No proxy pools. Manage lists in the panel.")
            return
        lines = ["Proxy pools:"]
        for p in pools:
            n = sum(1 for ln in (p.proxies_text or "").splitlines() if ln.strip() and not ln.strip().startswith("#"))
            wcount = (
                await db.execute(select(func.count()).select_from(WorkerNode).where(WorkerNode.proxy_pool_id == p.id))
            ).scalar() or 0
            flag = "ON" if p.is_active else "OFF"
            lines.append(f"• #{p.id} {p.name} [{flag}] proxies={n} workers={wcount}")
        lines.append("\n/proxy <id> on|off · edit proxy text in panel")
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/proxy":
        if len(args) < 2 or not args[0].isdigit():
            await send(token, chat_id, "Usage: /proxy <id> on|off")
            return
        p = await db.get(ProxyPool, int(args[0]))
        if not p:
            await send(token, chat_id, "Pool not found.")
            return
        p.is_active = args[1].lower() in ("on", "1", "true", "yes", "enable")
        await db.commit()
        await send(token, chat_id, f"Pool {p.name} active={p.is_active}")
        return

    if cmd == "/captcha":
        cap = await get_captcha_settings(db)
        if args and args[0].lower() in ("off", "disable", "none"):
            cap.captcha_provider = "none"
            cap.captcha_backup_provider = "none"
            await db.commit()
            await send(token, chat_id, "Captcha providers set to none. Keys unchanged (edit in panel).")
            return
        lines = [
            "Captcha (keys redacted):",
            f"primary={cap.captcha_provider} key={'yes' if cap.captcha_key else 'no'} "
            f"host={cap.captcha_host or '-'} retries={cap.captcha_retries}",
            f"backup={cap.captcha_backup_provider} key={'yes' if cap.captcha_backup_key else 'no'}",
            "\n/captcha off — disable solvers · set keys/providers in panel Admin → Captcha",
        ]
        await send(token, chat_id, "\n".join(lines))
        return

    if cmd == "/botstatus":
        settings = await db.get(BotSettings, 1)
        assert settings
        await send(
            token,
            chat_id,
            f"Bot enabled={settings.enabled}\n"
            f"username=@{settings.username or '-'}\n"
            f"admin_commands={settings.admin_commands_enabled}\n"
            f"public_packages={settings.public_packages}\n"
            f"support={settings.support_enabled}\n"
            f"deliver_results={settings.deliver_results_telegram}\n"
            "Token never shown here — manage in Bot Builder.",
        )
        return

    if cmd == "/boton":
        settings = await db.get(BotSettings, 1)
        assert settings
        settings.enabled = True
        await db.commit()
        await send(token, chat_id, "Bot enabled=True (polling continues).")
        return

    if cmd == "/botoff":
        settings = await db.get(BotSettings, 1)
        assert settings
        settings.enabled = False
        await db.commit()
        await send(token, chat_id, "Bot enabled=False. Polling will pause until re-enabled in panel or /boton from a running process.")
        return

    await send(token, chat_id, f"Unhandled admin command {cmd}. Send /admin.")
