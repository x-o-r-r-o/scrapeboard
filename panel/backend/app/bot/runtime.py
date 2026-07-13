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

from app.bot.admin import ADMIN_COMMANDS, handle_admin
from app.bot.tg_auth import (
    find_user_by_telegram,
    normalize_telegram_id,
    resolve_admin,
)
from app.bot.tg_commands import sync_telegram_command_menu
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import (
    BotCommand,
    BotSettings,
    Job,
    Order,
    Package,
    User,
)
from app.services import billing as billing_svc
from app.services import jobs as jobs_svc
from app.services import support as support_svc
from app.services.input_files import (
    InputFileError,
    check_extension,
    entries_to_bytes,
    parse_entries,
)
from app.services.notify import send_document, send_photo, send_text

log = logging.getLogger("bot.runtime")

# Slash commands implemented in code (DB rows only gate enable/audience/help/copy).
CODE_HANDLED_COMMANDS = frozenset(
    {
        "/whoami",
        "/id",
        "/start",
        "/help",
        "/formats",
        "/scrapers",
        "/packages",
        "/plans",
        "/buy",
        "/upgrade",
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
        *ADMIN_COMMANDS,
    }
)
COMMAND_ALIASES = {
    "/id": "/whoami",
    "/plans": "/packages",
    "/upgrade": "/buy",
    "/me": "/subscription",
    "/renew": "/buy",
    "/servers": "/workers",
    "/formats": "/help",  # legacy — full guide is attached on /help
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


def _tg_error_text(data: dict | None, fallback: str = "unknown Telegram error") -> str:
    if not data:
        return fallback
    desc = str(data.get("description") or "").strip()
    code = data.get("error_code")
    if desc and code is not None:
        return f"{code}: {desc}"
    return desc or fallback


class TelegramBotRuntime:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.offset: int | None = None
        self._cmd_limiter = RateLimiter(30, 60)
        self._pay_limiter = RateLimiter(5, 600)
        self._inputs: dict[int, dict[str, Path]] = {}  # user.id -> keywords/locations paths
        self._webhook_cleared_for: str | None = None
        self._commands_menu_token: str | None = None
        self._commands_menu_admin_chats: set[int] = set()
        self._commands_menu_dirty: bool = True
        self.status: str = "stopped"  # stopped|idle|polling|error
        self.last_error: str = ""
        self.last_ok_at: float | None = None
        self.updates_handled: int = 0

    def snapshot(self) -> dict[str, Any]:
        task_running = bool(self._task and not self._task.done())
        return {
            "status": self.status,
            "task_running": task_running,
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
            "updates_handled": self.updates_handled,
            "offset": self.offset,
        }

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.status = "starting"
        self._task = asyncio.create_task(self._loop(), name="telegram-bot-runtime")

    async def stop(self) -> None:
        """Stop the polling task. Must cancel — long-poll can block for ~25s."""
        self._stop.set()
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("bot task stop error")
        self.status = "stopped"

    async def restart(self) -> None:
        await self.stop()
        self.start()

    def invalidate_command_menu(self) -> None:
        """Mark Telegram setMyCommands scopes stale (settings/commands/admins changed)."""
        self._commands_menu_dirty = True
        self._commands_menu_token = None

    async def refresh_command_menu(self, db: AsyncSession | None = None, token: str | None = None) -> None:
        """Push public vs admin-scoped BotFather menus. Safe to call from API handlers."""
        try:
            if db is not None:
                if not token:
                    settings = await db.get(BotSettings, 1)
                    if not settings or not settings.token:
                        return
                    token = settings.token
                applied = await sync_telegram_command_menu(
                    db,
                    token,
                    previous_admin_chats=self._commands_menu_admin_chats,
                )
                self._commands_menu_admin_chats = applied
                self._commands_menu_token = token
                self._commands_menu_dirty = False
                return

            async with SessionLocal() as session:
                settings = await session.get(BotSettings, 1)
                if not settings or not settings.token:
                    return
                tok = token or settings.token
                applied = await sync_telegram_command_menu(
                    session,
                    tok,
                    previous_admin_chats=self._commands_menu_admin_chats,
                )
                self._commands_menu_admin_chats = applied
                self._commands_menu_token = tok
                self._commands_menu_dirty = False
        except Exception:
            log.exception("refresh_command_menu failed")

    async def _ensure_command_menu(self, db: AsyncSession, token: str) -> None:
        if not self._commands_menu_dirty and self._commands_menu_token == token:
            return
        await self.refresh_command_menu(db, token)

    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    async with SessionLocal() as db:
                        settings = await db.get(BotSettings, 1)
                        if not settings or not settings.enabled or not settings.token:
                            self.status = "idle"
                            self.last_error = (
                                "Bot disabled or token missing — set token and enable Live in Bot Builder."
                                if not settings or not settings.token
                                else "Bot is disabled (Live toggle off)."
                            )
                            await asyncio.sleep(5)
                            continue
                        token = settings.token
                        await self._ensure_command_menu(db, token)
                    await self._ensure_polling_mode(token)
                    updates = await self._get_updates(token)
                    for u in updates:
                        async with SessionLocal() as db:
                            await self._handle_update(db, token, u)
                        self.updates_handled += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.status = "error"
                    self.last_error = f"bot loop error: {exc}"
                    log.exception("bot loop error")
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            self.status = "stopped"
            raise

    async def _ensure_polling_mode(self, token: str) -> None:
        """Drop any webhook so getUpdates works (Conflict: terminated by other getUpdates / webhook)."""
        if self._webhook_cleared_for == token:
            return
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"https://api.telegram.org/bot{token}/deleteWebhook",
                    params={"drop_pending_updates": "false"},
                )
                data = r.json()
            if data.get("ok"):
                self._webhook_cleared_for = token
                log.info("Telegram webhook cleared; using long-poll getUpdates")
            else:
                err = _tg_error_text(data, "deleteWebhook failed")
                self.last_error = err
                log.warning("deleteWebhook failed: %s", err)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"deleteWebhook network error: {exc}"
            log.warning("deleteWebhook error: %s", exc)

    async def _get_updates(self, token: str) -> list[dict]:
        params: dict[str, Any] = {"timeout": 25}
        if self.offset is not None:
            params["offset"] = self.offset
        try:
            self.status = "polling"
            async with httpx.AsyncClient(timeout=40) as client:
                r = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
                data = r.json()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = "error"
            self.last_error = f"getUpdates network error: {exc}"
            log.warning("getUpdates network error: %s", exc)
            await asyncio.sleep(3)
            return []
        if not data.get("ok"):
            err = _tg_error_text(data, "getUpdates failed")
            self.status = "error"
            self.last_error = err
            # Webhook / concurrent poller conflict — force clear and retry next loop.
            if "conflict" in err.lower() or "webhook" in err.lower():
                self._webhook_cleared_for = None
                log.warning("getUpdates conflict: %s — will re-clear webhook", err)
            else:
                log.warning("getUpdates rejected: %s", err)
            await asyncio.sleep(5)
            return []
        import time

        self.last_ok_at = time.time()
        self.last_error = ""
        updates = data.get("result") or []
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    async def _send(self, token: str, chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
        await send_text(token, chat_id, text, reply_markup=reply_markup)

    async def _send_payment(self, token: str, chat_id: int, text: str, qr: bytes | None) -> None:
        if qr:
            ok = await send_photo(token, chat_id, qr, caption=text)
            if ok:
                return
        await self._send(token, chat_id, text)

    async def _handle_update(self, db: AsyncSession, token: str, update: dict) -> None:
        if update.get("callback_query"):
            await self._handle_callback(db, token, update["callback_query"])
            return

        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        frm = msg.get("from") or {}
        uid = frm.get("id")
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        chat_type = str(chat.get("type") or "private")
        if uid is None or chat_id is None:
            return

        settings = await db.get(BotSettings, 1)
        assert settings

        if not self._cmd_limiter.allow(str(uid)):
            return

        user = await find_user_by_telegram(db, uid)
        tid_disp = normalize_telegram_id(uid, allow_group=False) or str(uid)
        display_name = (frm.get("username") or frm.get("first_name") or "").strip() or None

        if msg.get("document"):
            if user and not user.is_active:
                await self._send(token, chat_id, "⛔ Your account is disabled. Contact support.")
                return
            await self._handle_document(db, token, chat_id, user, msg["document"], msg.get("caption") or "")
            return

        text = (msg.get("text") or "").strip()
        if not text:
            return

        # Persistent reply-keyboard labels → slash commands
        mapped = billing_svc.resolve_menu_text(text)
        if mapped:
            text = mapped

        # Admin reply-to a forwarded "Support #N …" message → instant user notify.
        reply_src = msg.get("reply_to_message") or {}
        reply_blob = (reply_src.get("text") or reply_src.get("caption") or "").strip()
        ticket_from_reply = support_svc.parse_ticket_id_from_forward(reply_blob)
        if ticket_from_reply is not None and not text.startswith("/"):
            gate = await resolve_admin(db, uid, settings)
            if gate.ok:
                try:
                    ticket = await support_svc.admin_reply(
                        db,
                        ticket_id=ticket_from_reply,
                        body=text,
                        admin=gate.user,
                    )
                    await self._send(
                        token,
                        chat_id,
                        f"✅ Replied to ticket #{ticket.id} (user notified on Telegram).",
                    )
                except LookupError as exc:
                    await self._send(token, chat_id, str(exc))
                except ValueError as exc:
                    await self._send(token, chat_id, str(exc))
                return

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]
        resolved = COMMAND_ALIASES.get(cmd, cmd)
        is_admin_cmd = resolved in ADMIN_COMMANDS or cmd in ADMIN_COMMANDS

        if user and not user.is_active and cmd not in (
            "/whoami",
            "/id",
            "/start",
            "/help",
            "/formats",
            "/scrapers",
            "/support",
        ):
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
            # Legacy /formats is disabled in the menu but still aliases to /help.
            alias_target = COMMAND_ALIASES.get(cmd)
            help_row = commands_all.get("/help")
            allow_as_help = (
                alias_target == "/help"
                and help_row is not None
                and bool(help_row.enabled)
            )
            if not allow_as_help:
                await self._send(token, chat_id, "This command is disabled.")
                return

        if cmd in ("/whoami", "/id"):
            if user:
                link = f"account: {user.username} (role={user.role})"
                status = " · disabled" if not user.is_active else (" · subscribed" if has_sub else " · no subscription")
            else:
                link = "no account yet — send /start"
                status = ""
            admin_line = ""
            if user and user.role == "admin":
                flag = "on" if settings.admin_commands_enabled else "OFF — enable in Bot Builder"
                admin_line = f"\nAdmin commands: {flag}"
            await self._send(
                token,
                chat_id,
                f"Your Telegram id: {tid_disp}\n{link}{status}{admin_line}",
            )
            return

        if cmd == "/start":
            await self._flow_start(db, token, chat_id, user, settings, uid=uid, display_name=display_name)
            return

        if cmd == "/help" or resolved == "/help":
            await self._send_help(db, token, chat_id, user, settings, commands, has_sub)
            return

        if cmd == "/scrapers":
            await self._scrapers_list(db, token, chat_id, user)
            return

        # billing open commands (/packages aliases to same list as /buy|/upgrade)
        if cmd in ("/packages", "/plans", "/buy", "/upgrade", "/paid", "/subscription", "/me", "/renew"):
            await self._handle_billing(
                db, token, chat_id, user, cmd, args, settings, uid=uid, display_name=display_name
            )
            return

        # Admin Telegram commands — resolve before audience gate so denials are explicit.
        if is_admin_cmd:
            gate = await resolve_admin(db, uid, settings)
            if not gate.ok:
                log.info(
                    "Admin cmd %s denied tg=%s reason=%s",
                    resolved,
                    tid_disp,
                    gate.reason,
                )
                await self._send(token, chat_id, gate.message)
                return
            await handle_admin(
                db=db,
                token=token,
                chat_id=chat_id,
                chat_type=chat_type,
                admin=gate.user,  # type: ignore[arg-type]
                cmd=resolved if resolved in ADMIN_COMMANDS else cmd,
                args=args,
                send=self._send,
            )
            # First successful admin DM often unlocks BotCommandScopeChat.
            if chat_type == "private" and int(chat_id) not in self._commands_menu_admin_chats:
                self.invalidate_command_menu()
                await self._ensure_command_menu(db, token)
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
            extra = " Send /start to create your account, then /buy to see plans."
            await self._send(token, chat_id, f"⛔ Not authorized. Your id is {tid_disp}.{extra}")
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

        if meta and meta.response_text:
            await self._send(token, chat_id, meta.response_text)
            return

        if cmd.startswith("/"):
            await self._send(token, chat_id, "Unknown or disabled command. Send /help.")

    async def _menu_markup(self, db, user: User | None, settings: BotSettings | None = None) -> dict:
        """Persistent reply keyboard for the user's current access state."""
        is_admin = bool(user and user.role == "admin")
        has_sub = False
        if user and user.is_active:
            if is_admin:
                has_sub = True
            else:
                has_sub = bool(await billing_svc.active_subscription(db, user))
        support_on = True
        if settings is not None:
            support_on = bool(getattr(settings, "support_enabled", True))
        return billing_svc.user_reply_keyboard(
            is_admin=is_admin,
            has_sub=has_sub,
            support_enabled=support_on,
        )

    async def _send_menu(self, db, token: str, chat_id: int, text: str, user: User | None, settings=None) -> None:
        await self._send(token, chat_id, text, reply_markup=await self._menu_markup(db, user, settings))

    async def _flow_start(
        self,
        db,
        token,
        chat_id,
        user,
        settings,
        *,
        uid: Any = None,
        display_name: str | None = None,
    ) -> None:
        # Auto-provision a panel user so buyers can browse/buy without admin linking.
        if not user and uid is not None:
            try:
                user = await billing_svc.ensure_telegram_user(db, uid, display_name=display_name)
            except Exception:
                log.exception("ensure_telegram_user failed")
                await self._send(
                    token,
                    chat_id,
                    "Could not create your account. Try again or contact support.",
                )
                return

        if not user:
            await self._send(
                token,
                chat_id,
                "Welcome! Use the menu below or send /buy to see subscription plans.",
                reply_markup=billing_svc.user_reply_keyboard(has_sub=False),
            )
            return

        msg = settings.welcome_text or "Welcome!"
        msg += f"\nAccount: {user.username} ({user.role})."
        sub = await billing_svc.active_subscription(db, user)
        menu = await self._menu_markup(db, user, settings)

        if user.role == "admin":
            msg += "\nAdmin — no subscription required."
            msg += "\nUpload inputs (.txt/.csv), then /run source=…. See /help and /scrapers."
            msg += "\nTap Admin for the admin command menu."
            await self._send(token, chat_id, msg, reply_markup=menu)
            return

        if sub:
            msg += f"\nPlan: {sub.package_name} until {sub.expires_at.date()}."
            msg += "\nUpload inputs (.txt/.csv), then /run source=…. See /help and /scrapers."
            msg += "\nTap Upgrade for a higher tier (same/lower plans are not offered)."
            await self._send(token, chat_id, msg, reply_markup=menu)
            return

        # New / unsubscribed: sell packages instead of "ask admin for telegram id"
        msg += "\nNo active subscription — pick a package to get access."
        await self._send(token, chat_id, msg, reply_markup=menu)

        pkgs = (await db.execute(select(Package).where(Package.is_active == True))).scalars().all()  # noqa: E712
        if not pkgs:
            await self._send(token, chat_id, "(No packages configured yet — check back soon.)")
            return

        # Inline package picker (reply keyboard stays — do not remove_keyboard)
        await self._send(
            token,
            chat_id,
            billing_svc.format_packages_list(pkgs),
            reply_markup=billing_svc.packages_inline_keyboard(pkgs),
        )

    async def _answer_callback(self, token: str, callback_id: str, text: str = "") -> None:
        try:
            payload: dict[str, Any] = {"callback_query_id": callback_id}
            if text:
                payload["text"] = text[:200]
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                    json=payload,
                )
        except Exception:
            log.exception("answerCallbackQuery failed")

    async def _handle_callback(self, db: AsyncSession, token: str, cq: dict) -> None:
        data = str(cq.get("data") or "")
        frm = cq.get("from") or {}
        uid = frm.get("id")
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        cb_id = cq.get("id")
        if uid is None or chat_id is None or not cb_id:
            return
        if not self._cmd_limiter.allow(str(uid)):
            await self._answer_callback(token, cb_id, "Slow down")
            return

        display_name = (frm.get("username") or frm.get("first_name") or "").strip() or None
        user = await find_user_by_telegram(db, uid)
        if not user:
            try:
                user = await billing_svc.ensure_telegram_user(db, uid, display_name=display_name)
            except Exception:
                await self._answer_callback(token, cb_id, "Account error")
                return

        if data.startswith("buy:"):
            slug = data[4:].strip()
            await self._answer_callback(token, cb_id)
            await self._buy_package(db, token, chat_id, user, slug, network=None)
            return
        if data.startswith("buynet:"):
            parts = data.split(":")
            if len(parts) >= 3:
                slug, network = parts[1], parts[2]
                await self._answer_callback(token, cb_id)
                await self._buy_package(db, token, chat_id, user, slug, network=network)
                return
        await self._answer_callback(token, cb_id, "Unknown action")

    async def _buy_package(
        self,
        db,
        token,
        chat_id,
        user: User,
        slug: str,
        *,
        network: str | None,
    ) -> None:
        b = await billing_svc.get_billing(db)
        if not b.enabled:
            await self._send(token, chat_id, "Billing is disabled.")
            return
        pkg = (
            await db.execute(select(Package).where(Package.slug == slug, Package.is_active == True))  # noqa: E712
        ).scalar_one_or_none()
        if not pkg:
            await self._send(token, chat_id, "Unknown package. /buy")
            return
        ok, why = await billing_svc.can_purchase(db, user, pkg)
        if not ok:
            await self._send(token, chat_id, f"⛔ {why}")
            return

        nets = billing_svc.available_usdt_networks(b)
        net, net_err = billing_svc.resolve_network(b, network)

        # Step 2: always pick network when USDT nets exist and none was chosen yet
        if not network and nets:
            if len(nets) == 1:
                # Still show a one-button picker so the step order is visible
                await self._send(
                    token,
                    chat_id,
                    f"Step 2/3 — confirm payment network for {pkg.name} ({pkg.price_usdt} USDT):",
                    reply_markup=billing_svc.network_inline_keyboard(pkg.slug, nets),
                )
                return
            await self._send(
                token,
                chat_id,
                f"Step 2/3 — choose a payment network for {pkg.name} ({pkg.price_usdt} USDT):",
                reply_markup=billing_svc.network_inline_keyboard(pkg.slug, nets),
            )
            return

        if network and net_err:
            await self._send(token, chat_id, f"⛔ {net_err}")
            return

        if not net and not b.manual_enabled:
            await self._send(token, chat_id, f"⛔ {net_err or 'No payment method configured.'}")
            return

        if net:
            method = billing_svc.method_for_network(net)
        elif b.manual_enabled:
            method = billing_svc.METHOD_MANUAL
        else:
            await self._send(token, chat_id, f"⛔ {net_err or 'Choose a network.'}")
            return

        order = await billing_svc.create_order(db, user, pkg, method)
        text, qr = await billing_svc.payment_instructions(db, pkg, order=order, network=net)
        caption = f"Step 3/3 — pay & submit TxID\n\n{text}"
        await self._send_payment(token, chat_id, caption, qr)

    async def _handle_billing(
        self,
        db,
        token,
        chat_id,
        user,
        cmd,
        args,
        settings,
        *,
        uid: Any = None,
        display_name: str | None = None,
    ) -> None:
        b = await billing_svc.get_billing(db)

        # Auto-link on billing commands so /buy and /packages work for new Telegram users.
        if not user and uid is not None and cmd in (
            "/packages",
            "/plans",
            "/buy",
            "/upgrade",
            "/renew",
            "/paid",
            "/subscription",
            "/me",
        ):
            try:
                user = await billing_svc.ensure_telegram_user(db, uid, display_name=display_name)
            except Exception:
                log.exception("ensure_telegram_user failed on %s", cmd)

        if cmd in ("/packages", "/plans", "/buy", "/upgrade", "/renew"):
            if cmd in ("/packages", "/plans") and not settings.public_packages and not user:
                await self._send(token, chat_id, "⛔ Send /start first to create your account.")
                return
            if cmd in ("/buy", "/upgrade", "/renew"):
                if not user:
                    await self._send(token, chat_id, "Send /start to create your account, then Buy.")
                    return
                if not b.enabled:
                    await self._send(token, chat_id, "Billing is disabled.")
                    return
                if args:
                    slug = args[0]
                    network = args[1] if len(args) > 1 else None
                    await self._buy_package(db, token, chat_id, user, slug, network=network)
                    return

            # Shared list: /packages, /plans, and buy/upgrade with no args
            pkgs = await billing_svc.purchasable_packages(db, user)
            has_sub = bool(user and user.role != "admin" and await billing_svc.active_subscription(db, user))
            if not pkgs:
                if has_sub:
                    await self._send(token, chat_id, billing_svc.top_plan_message())
                else:
                    await self._send(token, chat_id, "No packages configured.")
                return
            if cmd in ("/buy", "/upgrade", "/renew"):
                text = "Step 1/3 — pick a package:\n\n" + billing_svc.format_packages_list(
                    pkgs, upgrade=has_sub
                )
            else:
                text = billing_svc.format_packages_list(pkgs, upgrade=has_sub)
            await self._send(token, chat_id, text, reply_markup=billing_svc.packages_inline_keyboard(pkgs))
            return

        if cmd in ("/subscription", "/me"):
            if not user:
                await self._send(token, chat_id, "Send /start to create your account.")
                return
            if user.role == "admin":
                await self._send(token, chat_id, "You are an admin (no subscription needed).")
                return
            sub = await billing_svc.active_subscription(db, user)
            if not sub:
                await self._send(token, chat_id, "No subscription. Send /buy.")
                return
            await self._send(
                token,
                chat_id,
                f"Package: {sub.package_name}\nExpires: {sub.expires_at.date()}\n"
                f"Threads: {sub.threads} | Upload: {sub.max_upload_mb} MB",
            )
            return

        if cmd == "/paid":
            if not user:
                await self._send(token, chat_id, "Send /start to create your account first.")
                return
            if not self._pay_limiter.allow(str(user.id)):
                await self._send(token, chat_id, "Too many verification attempts. Wait a few minutes.")
                return
            if not args:
                await self._send(token, chat_id, "Usage: /paid <txid>")
                return
            txid = args[0].strip()
            if not billing_svc.valid_txid(txid):
                await self._send(token, chat_id, "That doesn't look like a valid transaction id.")
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
                await self._send(token, chat_id, "No pending order. Buy a package first.")
                return
            net = billing_svc.network_from_method(order.payment_method)
            if net not in (billing_svc.NETWORK_TRC20, billing_svc.NETWORK_BEP20):
                await self._send(
                    token,
                    chat_id,
                    "This order is manual — wait for admin /approve (TxID auto-verify is TRC-20/BEP-20 only).",
                )
                return
            if net == billing_svc.NETWORK_TRC20 and (not b.usdt_enabled or not b.usdt_wallet):
                await self._send(token, chat_id, "USDT TRC-20 not enabled.")
                return
            if net == billing_svc.NETWORK_BEP20 and (
                not getattr(b, "usdt_bep20_enabled", False)
                or not (getattr(b, "usdt_bep20_wallet", "") or "").strip()
            ):
                await self._send(token, chat_id, "USDT BEP-20 not enabled.")
                return
            pkg = await db.get(Package, order.package_id)
            if not pkg:
                await self._send(token, chat_id, "Package missing.")
                return
            await self._send(token, chat_id, "🔎 Verifying on-chain (≥20 confirmations)…")
            ok, detail, amount = await billing_svc.verify_usdt_payment(net, txid, b, float(pkg.price_usdt))
            if not ok:
                await self._send(token, chat_id, f"❌ {detail}")
                return
            sub = await billing_svc.fulfill_paid_order(
                db, user=user, order=order, pkg=pkg, txid=txid, network=net
            )
            await self._send_menu(
                db,
                token,
                chat_id,
                f"✅ Verified ({amount:.2f} USDT). {pkg.name} active until {sub.expires_at.date()}.\n{detail}",
                user,
                settings,
            )

    async def _support(self, db, token, chat_id, user, uid, args, settings) -> None:
        if not settings.support_enabled:
            await self._send(token, chat_id, "Support is not enabled.")
            return
        body = " ".join(args).strip()
        if not body:
            await self._send(
                token,
                chat_id,
                "Usage: /support <message>\n"
                "Opens a ticket (or adds a follow-up to your open ticket). "
                "Admins reply here on Telegram.",
            )
            return
        tid = normalize_telegram_id(uid, allow_group=False) or str(uid)
        ticket, _msg, created = await support_svc.create_or_append_user_message(
            db,
            settings=settings,
            user=user,
            telegram_id=tid,
            body=body,
        )
        if created:
            await self._send(
                token,
                chat_id,
                f"✅ Support ticket #{ticket.id} created. An admin will reply here.",
            )
        else:
            await self._send(
                token,
                chat_id,
                f"✅ Added to open ticket #{ticket.id}.",
            )

    async def _handle_document(self, db, token, chat_id, user, doc, caption) -> None:
        if not user:
            await self._send(token, chat_id, "⛔ Send /start first to create your account.")
            return
        perms = jobs_svc.effective_perms(user)
        if not perms.get("can_upload_inputs") and user.role != "admin":
            await self._send(token, chat_id, "⛔ You can't upload inputs.")
            return
        b = await billing_svc.get_billing(db)
        fname = (doc.get("file_name") or "").lower()
        allowed = b.allowed_extensions or [".txt", ".csv"]
        try:
            check_extension(fname, allowed)
        except InputFileError as e:
            await self._send(token, chat_id, f"⛔ {e}")
            return
        mime = (doc.get("mime_type") or "").lower()
        if mime.startswith(("image/", "video/", "audio/")) or mime in (
            "application/pdf",
            "application/zip",
            "application/x-zip-compressed",
        ):
            pretty = ", ".join(allowed) if allowed else ".txt, .csv"
            await self._send(token, chat_id, f"⛔ File must be {pretty} (not {mime or 'this type'}).")
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
        if any(t in cap_txt for t in ("email", "emails", "e-mail")) or any(
            t in fname for t in ("email", "emails")
        ):
            kind = "keywords"
        elif "keyword" in cap_txt or "keyword" in fname or "dork" in cap_txt or "dork" in fname:
            kind = "keywords"
        elif "location" in cap_txt or "location" in fname or "region" in cap_txt or "region" in fname:
            kind = "locations"
        if not kind:
            await self._send(
                token,
                chat_id,
                "Send a .txt or .csv with caption 'keywords', 'locations', or 'emails'. See /help.",
            )
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

        try:
            # Email lists: accept via parse_email_list when caption/name says email
            if any(t in cap_txt for t in ("email", "emails", "e-mail")) or any(
                t in fname for t in ("email", "emails")
            ):
                from app.services.input_files import parse_email_list

                entries = parse_email_list(
                    raw,
                    filename=fname,
                    check_ext=True,
                    configured_extensions=allowed,
                )
            else:
                entries = parse_entries(
                    raw,
                    kind,  # type: ignore[arg-type]
                    filename=fname,
                    check_ext=True,
                    configured_extensions=allowed,
                )
        except InputFileError as e:
            await self._send(
                token,
                chat_id,
                f"❌ {e}\nFix the file and re-upload. Job was not started. See /help.",
            )
            return

        cfg = get_settings()
        dest_dir = cfg.uploads_dir / f"tg_{user.id}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{kind}.txt"
        dest.write_bytes(entries_to_bytes(entries))
        self._inputs.setdefault(user.id, {})[kind] = dest
        await self._send(
            token,
            chat_id,
            f"✅ Saved {len(entries)} {kind}. Upload the other file if needed, then /run "
            f"(e.g. /run source=google_search use_dork=yes). See /scrapers.",
        )

    async def _scrapers_list(self, db, token, chat_id, user) -> None:
        from app.models import Package
        from app.services.scraper_registry import SOURCE_GMAPS, catalog_payload
        from app.services.scraper_settings import get_scraper_settings

        site = await get_scraper_settings(db)
        allowed: list[str] | None = None
        is_admin = bool(user and user.role == "admin")
        if user and not is_admin:
            sub = await billing_svc.active_subscription(db, user)
            if not sub or not getattr(sub, "package_id", None):
                allowed = [SOURCE_GMAPS]
            else:
                pkg = await db.get(Package, sub.package_id)
                allowed = list(getattr(pkg, "allowed_sources", None) or [SOURCE_GMAPS]) if pkg else [SOURCE_GMAPS]
        rows = catalog_payload(
            enabled_sources=list(site.enabled_sources or []),
            allowed_sources=allowed,
            is_admin=is_admin,
        )
        lines = [
            "🛠 Scrapers for /run source=…",
            "(Only sources allowed on your plan are listed.)",
            "",
        ]
        current_group = None
        any_sel = False
        for r in rows:
            if not r.get("selectable"):
                continue
            any_sel = True
            g = r.get("group_label") or r.get("group") or ""
            if g != current_group:
                current_group = g
                lines.append(f"— {g} —")
            sid = r["id"]
            mark = "★" if sid == SOURCE_GMAPS else "•"
            inputs = r.get("inputs") or "keywords × locations"
            lines.append(f"{mark} {sid} — {r['label']}")
            lines.append(f"  {r.get('description') or ''}")
            lines.append(f"  Inputs: {inputs}")
        if not any_sel:
            lines.append("(none selectable — /buy or ask admin)")
        lines.extend(
            [
                "",
                "Examples:",
                "/run source=gmaps threads=2 scrape_websites=yes",
                "/run source=gmaps scrape_websites=no",
                "/run source=google_search use_dork=yes",
                "/run source=email_validate",
                "/run source=email_harvest validate_after=yes",
                "/run source=tiktok_shop",
                "/run source=youtube max_results=30",
                "/run source=facebook_pages",
                "",
                "Help: /help (user guide attached) · /scrapers",
            ]
        )
        await self._send(token, chat_id, "\n".join(lines))

    async def _run(self, db, token, chat_id, user, args) -> None:
        perms = jobs_svc.effective_perms(user)
        if not perms.get("can_run") and user.role != "admin":
            await self._send(token, chat_id, "⛔ No run permission.")
            return
        if user.role != "admin":
            sub = await billing_svc.active_subscription(db, user)
            if not sub:
                await self._send(token, chat_id, "⛔ Subscription required. /buy")
                return
        inputs = self._inputs.get(user.id) or {}
        kw = inputs.get("keywords")
        loc = inputs.get("locations")
        overrides: dict[str, Any] = {}
        for tok in args:
            if "=" in tok:
                k, v = tok.split("=", 1)
                overrides[k.strip().replace("-", "_")] = v
        # Pull optional display name out of scrape overrides (name= / title=).
        display_name = overrides.pop("name", None)
        if display_name is None:
            display_name = overrides.pop("title", None)
        source = overrides.pop("source", None)
        channels_raw = overrides.pop("channels", None)
        channel_list = None
        if channels_raw is not None:
            channel_list = [p.strip() for p in str(channels_raw).split(",") if p.strip()]

        from app.services.scraper_registry import normalize_source

        src = normalize_source(source)
        dork_on = str(overrides.get("use_dork", "")).strip().lower() in ("1", "true", "yes", "on")

        if not kw or not kw.exists():
            await self._send(
                token,
                chat_id,
                "Upload a keywords (or emails) file first (caption it). See /help.",
            )
            return

        loc_bytes: bytes
        loc_name: str
        if loc and loc.exists():
            loc_bytes = loc.read_bytes()
            loc_name = loc.name
        elif src == "email_validate" or (src == "google_search" and dork_on):
            loc_bytes = b"-\n"
            loc_name = "locations.txt"
        else:
            await self._send(
                token,
                chat_id,
                "Upload keywords and locations files first (caption them). "
                "For Google dorks use /run source=google_search use_dork=yes. See /help.",
            )
            return

        try:
            job = await jobs_svc.create_job_from_bytes(
                db,
                user,
                kw.read_bytes(),
                loc_bytes,
                overrides,
                name=display_name,
                keywords_name=kw.name,
                locations_name=loc_name,
                check_ext=False,
                source=source,
                channels=channel_list,
            )
        except (PermissionError, ValueError) as e:
            await self._send(token, chat_id, f"❌ {e}\nJob was not started. See /help.")
            return
        blocker = await jobs_svc.owner_blocking_job(db, user.id, exclude_job_id=job.id)
        if blocker:
            wait_note = (
                f"1 job at a time — waiting for {jobs_svc.job_display_label(blocker)} to finish.\n"
            )
        else:
            wait_note = "Workers will pick it up when a slot is free.\n"
        src_note = f"source={getattr(job, 'source', None) or src}"
        await self._send(
            token,
            chat_id,
            f"✅ Job queued: {jobs_svc.job_display_label(job)}\n"
            f"{src_note} · {job.total_searches:,} searches · {jobs_svc.job_thread_count(job)} threads.\n"
            f"{wait_note}"
            f"/status for progress.",
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
                "No jobs yet.\nUpload inputs, then /run source=…\n"
                "See /help and /scrapers. Use /status anytime for progress.",
            )
            return

        def _bar(pct: float, width: int = 10) -> str:
            filled = max(0, min(width, int(round(pct / 100.0 * width))))
            return "█" * filled + "░" * (width - filled)

        def _engine(j: Job) -> str:
            s = j.settings or {}
            return str(s.get("engine") or "—")

        async def _progress(j: Job) -> tuple[int, int, float]:
            done = j.done_searches
            rows = j.rows_saved
            if j.status in ("running", "queued") and j.total_searches:
                done, rows = await jobs_svc.live_job_progress(db, j)
            pct = 100.0 * done / j.total_searches if j.total_searches else 0.0
            return done, rows, pct

        lines: list[str] = ["📊 Your job status", ""]
        if active:
            lines.append("▶ Active jobs (1 runs at a time)")
            for j in active:
                done, rows, pct = await _progress(j)
                lines.append(f"• {jobs_svc.job_display_label(j)}")
                lines.append(f"  [{j.status}] {_bar(pct)} {pct:.1f}%")
                lines.append(
                    f"  searches {done:,}/{j.total_searches:,} · "
                    f"rows {rows:,} · engine {_engine(j)}"
                )
                if j.status == "queued":
                    blocker = await jobs_svc.owner_blocking_job(db, user.id, exclude_job_id=j.id)
                    if blocker:
                        lines.append(
                            f"  ⏳ 1 job at a time — waiting for "
                            f"{jobs_svc.job_display_label(blocker)} to finish"
                        )
                if j.started_at:
                    lines.append(f"  started {j.started_at.strftime('%Y-%m-%d %H:%M UTC')}")
            lines.append("")
        else:
            lines.append("▶ Active jobs (1 runs at a time)")
            lines.append("• none — queue a job with /run")
            lines.append("")

        lines.append("📁 Recent jobs")
        for j in recent:
            done, rows, pct = await _progress(j)
            mark = "▶" if j.status in ("queued", "running") else "•"
            lines.append(
                f"{mark} {jobs_svc.job_display_label(j)} [{j.status}] {pct:.1f}% · "
                f"{done}/{j.total_searches} · rows {rows}"
            )
        lines.append("")
        lines.append("Tips: /status · /stop · /scrapers · /help · /support")
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
                f"Could not stop {jobs_svc.job_display_label(job)} (status is now {job.status}).",
            )
            return
        msg = f"⏹ Stopped {jobs_svc.job_display_label(job)}. Rows saved: {job.rows_saved}."
        if zip_path:
            msg += " Partial results ready."
        await self._send(token, chat_id, msg)
        if zip_path:
            settings = await db.get(BotSettings, 1)
            if settings and settings.deliver_results_telegram:
                await send_document(token, chat_id, zip_path, caption=zip_path.name)

    def _telegram_users_guide_path(self) -> Path | None:
        """Locate TELEGRAM_USERS.md (bundled assets, then repo root)."""
        here = Path(__file__).resolve()
        candidates = [
            here.parent / "assets" / "TELEGRAM_USERS.md",
            here.parents[4] / "TELEGRAM_USERS.md" if len(here.parents) >= 5 else None,
            Path.cwd() / "TELEGRAM_USERS.md",
            Path.cwd().parent / "TELEGRAM_USERS.md",
        ]
        for p in candidates:
            if p is not None and p.is_file():
                return p
        return None

    async def _send_help(
        self,
        db,
        token,
        chat_id,
        user: User | None,
        settings: BotSettings,
        commands: dict,
        has_sub: bool,
    ) -> None:
        help_body = self._help_text(commands, settings, user, has_sub)
        support_on = bool(getattr(settings, "support_enabled", False))
        if support_on:
            support_block = (
                "— Support tickets —\n"
                "Send: /support <your message>\n"
                "Example: /support Payment verified but plan not active. TxID: …\n"
                "Opens a ticket (or adds a follow-up). Admins reply in this chat.\n"
                "You can also tap Support on the menu."
            )
        else:
            support_block = (
                "— Support tickets —\n"
                "Support is currently disabled on this bot. Contact the site admin directly."
            )
        menu = await self._menu_markup(db, user, settings)
        text = (
            f"{help_body}\n\n"
            "Use the menu buttons below, or type a command.\n"
            "Upload captions: keywords · locations · emails\n"
            "Start a job: /run source=gmaps … · list sources: /scrapers\n\n"
            f"{support_block}\n\n"
            "Full Telegram user guide is attached (uploads, scrapers, /run options)."
        )
        await self._send(token, chat_id, text, reply_markup=menu)
        guide = self._telegram_users_guide_path()
        if guide is not None:
            ok = await send_document(
                token,
                chat_id,
                guide,
                caption="Scrapeboard Telegram user guide — uploads, scrapers, support",
            )
            if not ok:
                await self._send(
                    token,
                    chat_id,
                    "Could not attach the user guide file. Use /scrapers and /support, "
                    "or ask an admin for help.",
                )
        else:
            await self._send(
                token,
                chat_id,
                "User guide file is missing on the server. Use /scrapers for sources and "
                "/support <message> for tickets.",
            )

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
            if not c.enabled:
                continue
            if (c.command or "").lower() in ("/formats",):
                continue  # legacy alias of /help — do not list twice
            if not self._audience_ok(c.audience, user, has_sub):
                continue
            if c.audience == "admins" and not settings.admin_commands_enabled:
                continue
            lines.append(f"{c.command} — {c.title or c.description}")
        if not any(c.command == "/scrapers" for c in commands.values() if c.enabled):
            lines.append("/scrapers — Available scraper sources for /run")
        if not any(c.command == "/support" for c in commands.values() if c.enabled):
            lines.append("/support <message> — Open a support ticket")
        return "\n".join(lines) if len(lines) > 1 else "No commands available."


bot_runtime = TelegramBotRuntime()
