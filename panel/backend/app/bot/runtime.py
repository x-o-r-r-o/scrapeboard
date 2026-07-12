"""Telegram bot runtime — full command set wired to panel services."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    BotCommand,
    BotSettings,
    Job,
    Order,
    Package,
    PaymentTxid,
    Subscription,
    SupportTicket,
    User,
    WorkerNode,
)
from app.services import billing as billing_svc
from app.services import jobs as jobs_svc
from app.services.notify import send_document, send_text

log = logging.getLogger("bot.runtime")

# Slash commands implemented in code (DB rows only gate enable/audience/help/copy).
CODE_HANDLED_COMMANDS = frozenset(
    {
        "/whoami",
        "/id",
        "/start",
        "/help",
        "/packages",
        "/plans",
        "/buy",
        "/paid",
        "/subscription",
        "/me",
        "/renew",
        "/support",
        "/run",
        "/status",
        "/stats",
        "/jobs",
        "/stop",
        "/servers",
        "/pending",
        "/approve",
        "/users",
        "/grant",
        "/revoke",
        "/extend",
        "/disable",
        "/enable",
    }
)
COMMAND_ALIASES = {
    "/id": "/whoami",
    "/plans": "/packages",
    "/me": "/subscription",
    "/renew": "/subscription",
}


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float):
        self.max = max_calls
        self.per = per_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        import time

        now = time.time()
        q = self.hits[key]
        while q and now - q[0] > self.per:
            q.popleft()
        if len(q) >= self.max:
            return False
        q.append(now)
        return True


class TelegramBotRuntime:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.offset: int | None = None
        self._cmd_limiter = RateLimiter(30, 60)
        self._pay_limiter = RateLimiter(5, 600)
        self._inputs: dict[int, dict[str, Path]] = {}  # user.id -> keywords/locations paths

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="telegram-bot-runtime")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait([self._task], timeout=5)

    async def restart(self) -> None:
        await self.stop()
        self.start()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                async with SessionLocal() as db:
                    settings = await db.get(BotSettings, 1)
                    if not settings or not settings.enabled or not settings.token:
                        await asyncio.sleep(5)
                        continue
                    token = settings.token
                updates = await self._get_updates(token)
                for u in updates:
                    async with SessionLocal() as db:
                        await self._handle_update(db, token, u)
            except Exception:
                log.exception("bot loop error")
                await asyncio.sleep(3)

    async def _get_updates(self, token: str) -> list[dict]:
        params: dict[str, Any] = {"timeout": 25}
        if self.offset is not None:
            params["offset"] = self.offset
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                r = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
                data = r.json()
        except Exception:
            return []
        if not data.get("ok"):
            return []
        updates = data.get("result") or []
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    async def _send(self, token: str, chat_id: int, text: str) -> None:
        await send_text(token, chat_id, text)

    async def _handle_update(self, db: AsyncSession, token: str, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        frm = msg.get("from") or {}
        uid = frm.get("id")
        chat_id = (msg.get("chat") or {}).get("id")
        if uid is None or chat_id is None:
            return

        settings = await db.get(BotSettings, 1)
        assert settings

        if not self._cmd_limiter.allow(str(uid)):
            return

        user = (await db.execute(select(User).where(User.telegram_id == str(uid)))).scalar_one_or_none()

        if msg.get("document"):
            if user and not user.is_active:
                await self._send(token, chat_id, "⛔ Your account is disabled. Contact support.")
                return
            await self._handle_document(db, token, chat_id, user, msg["document"], msg.get("caption") or "")
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]

        if user and not user.is_active and cmd not in ("/whoami", "/id", "/start", "/help"):
            await self._send(token, chat_id, "⛔ Your account is disabled. Contact support.")
            return

        has_sub = False
        if user and user.is_active:
            if user.role == "admin":
                has_sub = True
            else:
                has_sub = bool(await billing_svc.active_subscription(db, user))

        commands_all = {
            c.command: c for c in (await db.execute(select(BotCommand))).scalars().all()
        }
        commands = {k: v for k, v in commands_all.items() if v.enabled}

        def _row_for(slash: str):
            if slash in commands_all:
                return commands_all[slash]
            alias = COMMAND_ALIASES.get(slash)
            if alias and alias in commands_all:
                return commands_all[alias]
            return None

        row = _row_for(cmd)
        if row is not None and not row.enabled:
            await self._send(token, chat_id, "This command is disabled.")
            return

        if cmd in ("/whoami", "/id"):
            link = f"linked as {user.username}" if user else "not linked to a panel account"
            status = ""
            if user:
                status = " · disabled" if not user.is_active else (" · subscribed" if has_sub else " · no subscription")
            await self._send(token, chat_id, f"Your Telegram id: {uid}\nPanel: {link}{status}")
            return

        if cmd == "/start":
            await self._flow_start(db, token, chat_id, user, settings)
            return

        if cmd == "/help":
            await self._send(token, chat_id, self._help_text(commands, settings, user, has_sub))
            return

        # billing open commands
        if cmd in ("/packages", "/plans", "/buy", "/paid", "/subscription", "/me", "/renew"):
            await self._handle_billing(db, token, chat_id, user, cmd, args, settings)
            return

        meta = commands.get(cmd) or commands.get(COMMAND_ALIASES.get(cmd, ""))
        if meta and not self._audience_ok(meta.audience, user, has_sub):
            await self._send(token, chat_id, "⛔ Not allowed for your role.")
            return

        # Custom / static-reply commands (DB-only; no Python handler).
        if meta and meta.response_text and cmd not in CODE_HANDLED_COMMANDS:
            await self._send(token, chat_id, meta.response_text)
            return

        if not user:
            extra = " Send /packages to see plans." if settings.public_packages else ""
            await self._send(token, chat_id, f"⛔ Not authorized. Your id is {uid}.{extra}")
            return

        if cmd == "/support":
            await self._support(db, token, chat_id, user, uid, args, settings)
            return

        if cmd == "/run":
            await self._run(db, token, chat_id, user, args)
            return

        if cmd in ("/status", "/stats", "/jobs"):
            await self._status(db, token, chat_id, user)
            return

        if cmd == "/stop":
            await self._stop_job(db, token, chat_id, user)
            return

        # admin telegram commands
        if cmd in ("/servers", "/pending", "/approve", "/users", "/grant", "/revoke", "/extend", "/disable", "/enable"):
            if user.role != "admin" or not settings.admin_commands_enabled:
                await self._send(token, chat_id, "⛔ Admins only (enable admin commands in Bot Builder).")
                return
            await self._admin_cmd(db, token, chat_id, cmd, args)
            return

        if meta and meta.response_text:
            await self._send(token, chat_id, meta.response_text)
            return

        if cmd.startswith("/"):
            await self._send(token, chat_id, "Unknown or disabled command. Send /help.")

    async def _flow_start(self, db, token, chat_id, user, settings) -> None:
        if not user:
            msg = "No panel account linked. Ask an admin to create your user and set your Telegram ID."
        else:
            msg = settings.welcome_text or "Welcome!"
            msg += f"\nAccount: {user.username} ({user.role})."
            sub = await billing_svc.active_subscription(db, user)
            if user.role == "admin":
                msg += "\nAdmin — no subscription required."
            elif sub:
                msg += f"\nPlan: {sub.package_name} until {sub.expires_at.date()}."
            else:
                msg += "\nNo subscription. Send /packages."
            msg += "\nUpload keywords/locations files, then /run."
        await self._send(token, chat_id, msg)

    async def _handle_billing(self, db, token, chat_id, user, cmd, args, settings) -> None:
        b = await billing_svc.get_billing(db)
        if cmd in ("/packages", "/plans"):
            if not settings.public_packages and not user:
                await self._send(token, chat_id, "⛔ Packages only for linked users.")
                return
            pkgs = (await db.execute(select(Package).where(Package.is_active == True))).scalars().all()  # noqa: E712
            if not pkgs:
                await self._send(token, chat_id, "No packages configured.")
                return
            lines = ["Available packages:"]
            for p in sorted(pkgs, key=lambda x: x.tier):
                lines.append(
                    f"• {p.name} ({p.slug}) — {p.price_usdt} USDT / {p.duration_days}d | "
                    f"threads {p.threads}, upload {p.max_upload_mb}MB"
                )
            lines.append("\nBuy: /buy <slug>")
            await self._send(token, chat_id, "\n".join(lines))
            return

        if cmd in ("/subscription", "/me"):
            if not user:
                await self._send(token, chat_id, "Not linked to a panel account.")
                return
            if user.role == "admin":
                await self._send(token, chat_id, "You are an admin (no subscription needed).")
                return
            sub = await billing_svc.active_subscription(db, user)
            if not sub:
                await self._send(token, chat_id, "No subscription. Send /packages.")
                return
            await self._send(
                token,
                chat_id,
                f"Package: {sub.package_name}\nExpires: {sub.expires_at.date()}\n"
                f"Threads: {sub.threads} | Upload: {sub.max_upload_mb} MB",
            )
            return

        if cmd in ("/buy", "/renew"):
            if not user:
                await self._send(token, chat_id, "Link a panel account first (admin must set your Telegram ID).")
                return
            if not b.enabled:
                await self._send(token, chat_id, "Billing is disabled.")
                return
            if not args:
                await self._send(token, chat_id, "Usage: /buy <package_slug>")
                return
            pkg = (
                await db.execute(select(Package).where(Package.slug == args[0], Package.is_active == True))  # noqa: E712
            ).scalar_one_or_none()
            if not pkg:
                await self._send(token, chat_id, "Unknown package. /packages")
                return
            ok, why = await billing_svc.can_purchase(db, user, pkg)
            if not ok:
                await self._send(token, chat_id, f"⛔ {why}")
                return
            await billing_svc.create_order(db, user, pkg, "usdt" if b.usdt_enabled else "manual")
            await self._send(token, chat_id, await billing_svc.payment_instructions(db, pkg))
            return

        if cmd == "/paid":
            if not user:
                await self._send(token, chat_id, "Link a panel account first.")
                return
            if not self._pay_limiter.allow(str(user.id)):
                await self._send(token, chat_id, "Too many verification attempts. Wait a few minutes.")
                return
            if not b.usdt_enabled or not b.usdt_wallet:
                await self._send(token, chat_id, "USDT not enabled.")
                return
            if not args:
                await self._send(token, chat_id, "Usage: /paid <txid>")
                return
            txid = args[0].strip()
            if not billing_svc.valid_txid(txid):
                await self._send(token, chat_id, "That doesn't look like a valid TRON TxID.")
                return
            if await billing_svc.txid_used(db, txid):
                await self._send(token, chat_id, "⛔ That transaction was already used.")
                return
            order = (
                await db.execute(
                    select(Order).where(Order.user_id == user.id, Order.status == "pending").order_by(Order.id.desc())
                )
            ).scalars().first()
            if not order:
                await self._send(token, chat_id, "No pending order. /buy <slug> first.")
                return
            pkg = await db.get(Package, order.package_id)
            if not pkg:
                await self._send(token, chat_id, "Package missing.")
                return
            await self._send(token, chat_id, "🔎 Verifying on-chain…")
            ok, detail, amount = await billing_svc.verify_trc20_payment(
                txid, b.usdt_wallet, pkg.price_usdt, b.usdt_api_base, b.usdt_api_key, b.usdt_contract
            )
            if not ok:
                await self._send(token, chat_id, f"❌ {detail}")
                return
            db.add(PaymentTxid(txid=txid, user_id=user.id, order_id=order.id))
            order.status = "paid"
            order.txid = txid
            await db.commit()
            sub = await billing_svc.activate_subscription(db, user, pkg)
            await self._send(
                token,
                chat_id,
                f"✅ Verified ({amount:.2f} USDT). {pkg.name} active until {sub.expires_at.date()}.",
            )

    async def _support(self, db, token, chat_id, user, uid, args, settings) -> None:
        if not settings.support_enabled:
            await self._send(token, chat_id, "Support is not enabled.")
            return
        body = " ".join(args).strip() or "(empty)"
        ticket = SupportTicket(user_id=user.id if user else None, telegram_id=str(uid), message=body)
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)
        await self._send(token, chat_id, f"✅ Support ticket #{ticket.id} created.")
        if settings.support_chat_id:
            await self._send(token, int(settings.support_chat_id), f"Support #{ticket.id} from {uid}:\n{body}")

    async def _handle_document(self, db, token, chat_id, user, doc, caption) -> None:
        if not user:
            await self._send(token, chat_id, "⛔ Link a panel account first.")
            return
        perms = jobs_svc.effective_perms(user)
        if not perms.get("can_upload_inputs") and user.role != "admin":
            await self._send(token, chat_id, "⛔ You can't upload inputs.")
            return
        b = await billing_svc.get_billing(db)
        fname = (doc.get("file_name") or "").lower()
        ext = Path(fname).suffix.lower()
        allowed = b.allowed_extensions or [".txt", ".csv"]
        if allowed and ext not in allowed:
            await self._send(token, chat_id, f"⛔ File type not allowed. Allowed: {', '.join(allowed)}")
            return
        size_mb = (doc.get("file_size") or 0) / (1024 * 1024)
        sub = await billing_svc.active_subscription(db, user)
        cap = sub.max_upload_mb if sub else b.max_upload_mb
        if user.role == "admin":
            cap = 999
        if size_mb > cap:
            await self._send(token, chat_id, f"⛔ File is {size_mb:.1f} MB; limit is {cap} MB.")
            return
        cap_txt = (caption or "").lower()
        kind = None
        if "keyword" in cap_txt or "keyword" in fname:
            kind = "keywords"
        elif "location" in cap_txt or "location" in fname:
            kind = "locations"
        if not kind:
            await self._send(token, chat_id, "Send .txt with caption 'keywords' or 'locations'.")
            return

        file_id = doc.get("file_id")
        async with httpx.AsyncClient(timeout=60) as client:
            settings = await db.get(BotSettings, 1)
            assert settings and settings.token
            meta = (await client.get(
                f"https://api.telegram.org/bot{settings.token}/getFile",
                params={"file_id": file_id},
            )).json()
            fp = (meta.get("result") or {}).get("file_path")
            if not fp:
                await self._send(token, chat_id, "❌ Could not get file path.")
                return
            raw = (
                await client.get(f"https://api.telegram.org/file/bot{settings.token}/{fp}")
            ).content

        cfg = get_settings()
        dest_dir = cfg.uploads_dir / f"tg_{user.id}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{kind}.txt"
        dest.write_bytes(raw)
        self._inputs.setdefault(user.id, {})[kind] = dest
        n = len([ln for ln in raw.decode("utf-8", errors="ignore").splitlines() if ln.strip() and not ln.startswith("#")])
        await self._send(token, chat_id, f"✅ Saved {n} {kind}. Use /run when ready.")

    async def _run(self, db, token, chat_id, user, args) -> None:
        perms = jobs_svc.effective_perms(user)
        if not perms.get("can_run") and user.role != "admin":
            await self._send(token, chat_id, "⛔ No run permission.")
            return
        if user.role != "admin":
            sub = await billing_svc.active_subscription(db, user)
            if not sub:
                await self._send(token, chat_id, "⛔ Subscription required. /packages")
                return
        inputs = self._inputs.get(user.id) or {}
        kw = inputs.get("keywords")
        loc = inputs.get("locations")
        if not kw or not loc or not kw.exists() or not loc.exists():
            await self._send(token, chat_id, "Upload keywords and locations files first (caption them).")
            return
        overrides: dict[str, Any] = {}
        for tok in args:
            if "=" in tok:
                k, v = tok.split("=", 1)
                overrides[k.strip().replace("-", "_")] = v
        try:
            job = await jobs_svc.create_job_from_bytes(db, user, kw.read_bytes(), loc.read_bytes(), overrides)
        except (PermissionError, ValueError) as e:
            await self._send(token, chat_id, f"❌ {e}")
            return
        await self._send(
            token,
            chat_id,
            f"✅ Job queued: {job.public_id}\n"
            f"{job.total_searches:,} searches · {jobs_svc.job_thread_count(job)} threads.\n"
            f"Threads are shared across your running jobs — this job starts when enough "
            f"capacity is free (or lower threads with panel Edit). /status for progress.",
        )

    async def _status(self, db, token, chat_id, user) -> None:
        """Show the user's running jobs with progress % and recent job stats."""
        active = (
            await db.execute(
                select(Job)
                .where(Job.owner_id == user.id, Job.status.in_(("queued", "running")))
                .order_by(Job.id.desc())
            )
        ).scalars().all()
        recent = (
            await db.execute(
                select(Job)
                .where(Job.owner_id == user.id)
                .order_by(Job.id.desc())
                .limit(8)
            )
        ).scalars().all()
        if not recent:
            await self._send(
                token,
                chat_id,
                "No jobs yet.\nUpload keywords + locations, then /run.\nUse /status anytime for progress.",
            )
            return

        def _pct(j: Job) -> float:
            return 100.0 * j.done_searches / j.total_searches if j.total_searches else 0.0

        def _bar(pct: float, width: int = 10) -> str:
            filled = max(0, min(width, int(round(pct / 100.0 * width))))
            return "█" * filled + "░" * (width - filled)

        def _engine(j: Job) -> str:
            s = j.settings or {}
            return str(s.get("engine") or "—")

        lines: list[str] = ["📊 Your job status", ""]
        if active:
            lines.append("▶ Currently running")
            for j in active:
                pct = _pct(j)
                lines.append(f"• {j.public_id}")
                lines.append(f"  [{j.status}] {_bar(pct)} {pct:.1f}%")
                lines.append(
                    f"  searches {j.done_searches:,}/{j.total_searches:,} · "
                    f"rows {j.rows_saved:,} · engine {_engine(j)}"
                )
                if j.started_at:
                    lines.append(f"  started {j.started_at.strftime('%Y-%m-%d %H:%M UTC')}")
            lines.append("")
        else:
            lines.append("▶ Currently running")
            lines.append("• none — queue a job with /run")
            lines.append("")

        lines.append("📁 Recent jobs")
        for j in recent:
            pct = _pct(j)
            mark = "▶" if j.status in ("queued", "running") else "•"
            lines.append(
                f"{mark} {j.public_id} [{j.status}] {pct:.1f}% · "
                f"{j.done_searches}/{j.total_searches} · rows {j.rows_saved}"
            )
        lines.append("")
        lines.append("Tips: /status or /jobs · /stop to cancel · panel Jobs for download")
        await self._send(token, chat_id, "\n".join(lines))

    async def _stop_job(self, db, token, chat_id, user) -> None:
        """Stop the user's own active job (ownership via own_active_job). Panel stop is admin-only."""
        perms = jobs_svc.effective_perms(user)
        if user.role != "admin" and not perms.get("can_stop", True):
            await self._send(token, chat_id, "⛔ You don't have permission to stop jobs.")
            return
        job = await jobs_svc.own_active_job(db, user)
        if not job:
            await self._send(
                token,
                chat_id,
                "Nothing to stop — you have no queued or running jobs.",
            )
            return
        # own_active_job is owner-scoped; never stop another user's job from Telegram.
        zip_path = await jobs_svc.finalize_job(db, job, cancelled=True)
        await db.refresh(job)
        if job.status != "stopped":
            await self._send(
                token,
                chat_id,
                f"Could not stop {job.public_id} (status is now {job.status}).",
            )
            return
        msg = f"⏹ Stopped {job.public_id}. Rows saved: {job.rows_saved}."
        if zip_path:
            msg += " Partial results ready."
        await self._send(token, chat_id, msg)
        if zip_path:
            settings = await db.get(BotSettings, 1)
            if settings and settings.deliver_results_telegram:
                await send_document(token, chat_id, zip_path, caption=zip_path.name)

    async def _admin_cmd(self, db, token, chat_id, cmd, args) -> None:
        if cmd == "/servers":
            workers = (await db.execute(select(WorkerNode))).scalars().all()
            if not workers:
                await self._send(token, chat_id, "No workers.")
                return
            lines = []
            for w in workers:
                lines.append(
                    f"• {w.name}: {'on' if w.last_seen_at else 'off'} "
                    f"cpu {w.cpu_percent:.0f}% mem {w.mem_percent:.0f}% "
                    f"{'drain' if w.is_draining else ''}{' disabled' if not w.is_enabled else ''}"
                )
            await self._send(token, chat_id, "Workers:\n" + "\n".join(lines))
        elif cmd == "/pending":
            orders = (
                await db.execute(select(Order).where(Order.status == "pending").order_by(Order.id.desc()))
            ).scalars().all()
            if not orders:
                await self._send(token, chat_id, "No pending orders.")
                return
            lines = []
            for o in orders:
                u = await db.get(User, o.user_id)
                p = await db.get(Package, o.package_id)
                lines.append(f"• order {o.id}: {u.username if u else o.user_id} → {p.slug if p else o.package_id}")
            lines.append("Approve in panel or: /approve <order_id>")
            await self._send(token, chat_id, "\n".join(lines))
        elif cmd == "/approve":
            if not args or not args[0].isdigit():
                await self._send(token, chat_id, "Usage: /approve <order_id>")
                return
            order = await db.get(Order, int(args[0]))
            if not order or order.status != "pending":
                await self._send(token, chat_id, "Pending order not found.")
                return
            user = await db.get(User, order.user_id)
            pkg = await db.get(Package, order.package_id)
            if not user or not pkg:
                await self._send(token, chat_id, "User/package missing.")
                return
            order.status = "approved"
            await db.commit()
            sub = await billing_svc.activate_subscription(db, user, pkg)
            await self._send(token, chat_id, f"✅ Approved order {order.id} → {user.username} until {sub.expires_at.date()}")
            if user.telegram_id:
                await self._send(token, int(user.telegram_id), f"✅ Subscription {pkg.name} active until {sub.expires_at.date()}.")
        elif cmd == "/users":
            users = (await db.execute(select(User).order_by(User.id))).scalars().all()
            lines = []
            for u in users:
                sub = await billing_svc.active_subscription(db, u)
                flag = "ON" if u.is_active else "OFF"
                plan = sub.package_name if sub else "-"
                lines.append(f"• {u.id} {u.username} [{flag}] tg={u.telegram_id or '-'} plan={plan}")
            await self._send(token, chat_id, "Users:\n" + "\n".join(lines[:80]))
        elif cmd == "/grant":
            # /grant <telegram_id|username> <package_slug> [days]
            if len(args) < 2:
                await self._send(token, chat_id, "Usage: /grant <telegram_id|username> <package_slug> [days]")
                return
            target = await self._find_user(db, args[0])
            if not target:
                await self._send(token, chat_id, "User not found.")
                return
            pkg = (
                await db.execute(select(Package).where(Package.slug == args[1]))
            ).scalar_one_or_none()
            if not pkg:
                await self._send(token, chat_id, "Package not found.")
                return
            days = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
            sub = await billing_svc.activate_subscription(db, target, pkg, duration_days=days)
            await self._send(
                token,
                chat_id,
                f"✅ Granted {pkg.name} → {target.username} until {sub.expires_at.date()}",
            )
            if target.telegram_id:
                await self._send(
                    token,
                    int(target.telegram_id),
                    f"✅ Subscription {pkg.name} active until {sub.expires_at.date()}.",
                )
        elif cmd == "/revoke":
            # /revoke <telegram_id|username>
            if not args:
                await self._send(token, chat_id, "Usage: /revoke <telegram_id|username>")
                return
            target = await self._find_user(db, args[0])
            if not target:
                await self._send(token, chat_id, "User not found.")
                return
            sub = await billing_svc.active_subscription(db, target)
            if not sub:
                await self._send(token, chat_id, "No active subscription.")
                return
            await billing_svc.revoke_subscription(db, sub)
            await self._send(token, chat_id, f"Revoked subscription for {target.username}.")
            if target.telegram_id:
                await self._send(token, int(target.telegram_id), "⚠️ Your subscription was revoked.")
        elif cmd == "/extend":
            # /extend <telegram_id|username> <days>
            if len(args) < 2 or not args[1].isdigit():
                await self._send(token, chat_id, "Usage: /extend <telegram_id|username> <days>")
                return
            target = await self._find_user(db, args[0])
            if not target:
                await self._send(token, chat_id, "User not found.")
                return
            sub = await billing_svc.active_subscription(db, target)
            if not sub:
                # extend last sub or require grant
                sub = (
                    await db.execute(
                        select(Subscription)
                        .where(Subscription.user_id == target.id)
                        .order_by(Subscription.expires_at.desc())
                    )
                ).scalars().first()
            if not sub:
                await self._send(token, chat_id, "No subscription to extend. Use /grant first.")
                return
            sub = await billing_svc.extend_subscription(db, sub, int(args[1]))
            await self._send(
                token,
                chat_id,
                f"Extended {target.username} by {args[1]}d → {sub.expires_at.date()}",
            )
            if target.telegram_id:
                await self._send(
                    token,
                    int(target.telegram_id),
                    f"✅ Subscription extended until {sub.expires_at.date()}.",
                )
        elif cmd == "/disable":
            if not args:
                await self._send(token, chat_id, "Usage: /disable <telegram_id|username>")
                return
            target = await self._find_user(db, args[0])
            if not target:
                await self._send(token, chat_id, "User not found.")
                return
            if target.role == "admin":
                await self._send(token, chat_id, "Cannot disable an admin.")
                return
            target.is_active = False
            await db.commit()
            await self._send(token, chat_id, f"Disabled {target.username}.")
        elif cmd == "/enable":
            if not args:
                await self._send(token, chat_id, "Usage: /enable <telegram_id|username>")
                return
            target = await self._find_user(db, args[0])
            if not target:
                await self._send(token, chat_id, "User not found.")
                return
            target.is_active = True
            await db.commit()
            await self._send(token, chat_id, f"Enabled {target.username}.")

    async def _find_user(self, db: AsyncSession, key: str) -> User | None:
        key = key.strip()
        if key.isdigit():
            by_tg = (await db.execute(select(User).where(User.telegram_id == key))).scalar_one_or_none()
            if by_tg:
                return by_tg
            by_id = await db.get(User, int(key))
            if by_id:
                return by_id
        return (await db.execute(select(User).where(User.username == key))).scalar_one_or_none()

    def _audience_ok(self, audience: str, user: User | None, has_sub: bool = False) -> bool:
        if audience == "everyone":
            return True
        if not user or not user.is_active:
            return False
        if audience == "admins":
            return user.role == "admin"
        if audience == "users":
            return True
        if audience == "subscribers":
            return has_sub or user.role == "admin"
        return False

    def _help_text(self, commands: dict, settings: BotSettings, user: User | None, has_sub: bool = False) -> str:
        lines = ["Commands:"]
        for c in sorted(commands.values(), key=lambda x: x.sort_order):
            if not self._audience_ok(c.audience, user, has_sub):
                continue
            if c.audience == "admins" and not settings.admin_commands_enabled:
                continue
            lines.append(f"{c.command} — {c.title or c.description}")
        return "\n".join(lines)


bot_runtime = TelegramBotRuntime()
