import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_security_settings, require_admin, require_ready_user
from app.bot.demos import DEMO_COMMANDS, DEMO_WORKFLOWS
from app.bot.runtime import bot_runtime
from app.bot.tg_auth import admin_setup_hint, first_admin_telegram_id, normalize_telegram_id
from app.core.database import get_db
from app.models import BotCommand, BotSettings, BotWorkflow, User
from app.schemas import (
    BotCommandCreate,
    BotCommandOut,
    BotCommandUpdate,
    BotRuntimeStatusOut,
    BotSettingsOut,
    BotSettingsUpdate,
    BotWorkflowCreate,
    BotWorkflowOut,
    BotWorkflowUpdate,
    CaptchaSettingsOut,
    CaptchaSettingsUpdate,
    MessageOut,
    SecuritySettingsOut,
    SecuritySettingsUpdate,
)
from app.services.captcha_settings import get_captcha_settings

log = logging.getLogger("api.settings_bot")

router = APIRouter(tags=["settings-bot"])

BUILTIN_COMMAND_KEYS = frozenset(c["key"] for c in DEMO_COMMANDS)
# Critical built-ins: edit/settings allowed; hard delete blocked (disable instead).
PROTECTED_COMMAND_KEYS = frozenset({"start", "stop", "help"})
ALLOWED_AUDIENCES = frozenset({"everyone", "users", "admins", "subscribers"})


def _slugify_key(raw: str) -> str:
    key = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (raw or "").strip().lower())
    key = key.strip("_-")[:64]
    return key or "workflow"


def _normalize_command(raw: str) -> str:
    c = (raw or "").strip().lower().split()[0] if (raw or "").strip() else ""
    c = c.split("@")[0]
    if not c:
        raise HTTPException(400, "command is required")
    if not c.startswith("/"):
        c = f"/{c}"
    body = c[1:]
    if not body or not all(ch.isalnum() or ch == "_" for ch in body):
        raise HTTPException(400, "command must be /name with letters, numbers, or underscore")
    if len(c) > 64:
        raise HTTPException(400, "command too long")
    return c


def _validate_audience(audience: str | None) -> str | None:
    if audience is None:
        return None
    a = (audience or "").strip().lower()
    if a not in ALLOWED_AUDIENCES:
        raise HTTPException(400, f"audience must be one of: {', '.join(sorted(ALLOWED_AUDIENCES))}")
    return a


def _command_out(row: BotCommand) -> BotCommandOut:
    builtin = row.key in BUILTIN_COMMAND_KEYS
    return BotCommandOut(
        id=row.id,
        key=row.key,
        command=row.command,
        title=row.title,
        description=row.description,
        response_text=row.response_text or "",
        enabled=row.enabled,
        audience=row.audience,
        sort_order=row.sort_order,
        is_builtin=builtin,
        handler="builtin" if builtin else "static",
    )


def _captcha_out(row) -> CaptchaSettingsOut:
    return CaptchaSettingsOut(
        captcha_provider=row.captcha_provider or "none",
        captcha_key_configured=bool((row.captcha_key or "").strip()),
        captcha_host=row.captcha_host or "",
        captcha_retries=int(row.captcha_retries or 2),
        captcha_backup_provider=row.captcha_backup_provider or "none",
        captcha_backup_key_configured=bool((row.captcha_backup_key or "").strip()),
        captcha_backup_host=row.captcha_backup_host or "",
    )


def _token_hint(token: str) -> str:
    """Masked BotFather token for admin UI — never return the full secret."""
    raw = (token or "").strip()
    if not raw:
        return ""
    if ":" in raw:
        bot_id, secret = raw.split(":", 1)
        bot_id = bot_id.strip()
        secret = secret.strip()
        tail = secret[-4:] if len(secret) >= 4 else ""
        return f"{bot_id}:{'•' * 8}{tail}"
    if len(raw) <= 8:
        return "••••••••"
    return f"{raw[:4]}{'•' * 8}{raw[-4:]}"


def _settings_out(b: BotSettings, *, suggested: str | None = None, admin_hint: str = "") -> BotSettingsOut:
    snap = bot_runtime.snapshot()
    return BotSettingsOut(
        enabled=b.enabled,
        token_configured=bool(b.token),
        token_hint=_token_hint(b.token or ""),
        username=b.username,
        mode=b.mode,
        welcome_text=b.welcome_text,
        notify_interval_sec=b.notify_interval_sec,
        support_enabled=b.support_enabled,
        support_chat_id=b.support_chat_id,
        public_packages=b.public_packages,
        deliver_results_telegram=b.deliver_results_telegram,
        admin_commands_enabled=b.admin_commands_enabled,
        suggested_support_chat_id=suggested,
        admin_setup_hint=admin_hint,
        runtime_status=str(snap.get("status") or "stopped"),
        runtime_task_running=bool(snap.get("task_running")),
        runtime_error=str(snap.get("last_error") or ""),
        runtime_last_ok_at=snap.get("last_ok_at"),  # type: ignore[arg-type]
        runtime_updates_handled=int(snap.get("updates_handled") or 0),
    )


async def _enrich_settings(db: AsyncSession, b: BotSettings) -> BotSettingsOut:
    suggested = await first_admin_telegram_id(db)
    hint = await admin_setup_hint(db, b)
    return _settings_out(b, suggested=suggested, admin_hint=hint)


async def _validate_telegram_token(token: str) -> dict:
    """Call getMe; never log the token. Raises HTTPException on failure."""
    token = (token or "").strip()
    if not token or ":" not in token:
        raise HTTPException(400, "Invalid bot token format (expected digits:secret from BotFather)")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = r.json()
    except Exception as exc:
        log.warning("getMe network error while validating token")
        raise HTTPException(400, f"Could not reach Telegram to validate token: {exc}") from exc
    if not data.get("ok"):
        desc = str(data.get("description") or "Telegram rejected the token")
        code = data.get("error_code")
        raise HTTPException(400, f"Telegram rejected token{f' ({code})' if code else ''}: {desc}")
    result = data.get("result") or {}
    if not result.get("is_bot"):
        raise HTTPException(400, "Token is valid but getMe did not return a bot account")
    return result


def _runtime_hint(b: BotSettings, snap: dict) -> str:
    if not b.token:
        return "Paste a BotFather token and Save, then turn Live on."
    if not b.enabled:
        return "Token is set but Live is off — enable Live so the API process starts polling."
    if not snap.get("task_running"):
        return "Runtime task is not running. Click Restart runtime or restart systemd scrapeboard."
    status = str(snap.get("status") or "")
    err = str(snap.get("last_error") or "")
    if status == "error" or err:
        if "Unauthorized" in err or "401" in err:
            return "Token invalid or revoked. Paste a new token from BotFather and Save."
        if "conflict" in err.lower() or "webhook" in err.lower():
            return "getUpdates conflict (webhook or another poller). Restart runtime; scrapeboard clears webhooks automatically."
        return f"Polling error — check journalctl -u scrapeboard. ({err[:120]})"
    if status == "polling":
        return "Polling Telegram. Send /start in a DM to the bot; unlinked users still get a reply."
    if status == "idle":
        return "Waiting for enabled+token (should not idle while Live is on)."
    return "Runtime starting…"


@router.get("/settings/security", response_model=SecuritySettingsOut)
async def get_security(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    sec = await get_security_settings(db)
    return SecuritySettingsOut(
        recaptcha_mode=sec.recaptcha_mode,  # type: ignore[arg-type]
        recaptcha_site_key=sec.recaptcha_site_key,
        recaptcha_v3_min_score=sec.recaptcha_v3_min_score,
        max_login_failures=sec.max_login_failures,
        lockout_minutes=sec.lockout_minutes,
        recaptcha_secret_configured=bool(sec.recaptcha_secret_key),
    )


@router.put("/settings/security", response_model=SecuritySettingsOut)
async def update_security(
    body: SecuritySettingsUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    sec = await get_security_settings(db)
    data = body.model_dump(exclude_unset=True)
    # Enforce single mode: v2 XOR v3 XOR none
    if "recaptcha_mode" in data and data["recaptcha_mode"] not in ("none", "v2", "v3"):
        data["recaptcha_mode"] = "none"
    for k, v in data.items():
        setattr(sec, k, v)
    await db.commit()
    return await get_security(_, __, db)


@router.get("/settings/captcha", response_model=CaptchaSettingsOut)
async def get_captcha(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    row = await get_captcha_settings(db)
    return _captcha_out(row)


@router.put("/settings/captcha", response_model=CaptchaSettingsOut)
async def update_captcha(
    body: CaptchaSettingsUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    row = await get_captcha_settings(db)
    data = body.model_dump(exclude_unset=True)
    for secret in ("captcha_key", "captcha_backup_key"):
        if secret in data and (data[secret] is None or str(data[secret]) == ""):
            data.pop(secret)
    allowed = {
        "none",
        "2captcha",
        "captchaai",
    }
    for provider_key in ("captcha_provider", "captcha_backup_provider"):
        if provider_key in data and data[provider_key] not in allowed:
            data[provider_key] = "none"
    for k, v in data.items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return _captcha_out(row)


@router.get("/bot/settings", response_model=BotSettingsOut)
async def get_bot_settings(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    b = await db.get(BotSettings, 1)
    if not b:
        b = BotSettings(id=1)
        db.add(b)
        await db.commit()
        await db.refresh(b)
    return await _enrich_settings(db, b)


@router.put("/bot/settings", response_model=BotSettingsOut)
async def update_bot_settings(
    body: BotSettingsUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await db.get(BotSettings, 1) or BotSettings(id=1)
    if not await db.get(BotSettings, 1):
        db.add(b)
    data = body.model_dump(exclude_unset=True)
    clear_token = bool(data.pop("clear_token", None))
    if clear_token:
        data.pop("token", None)
        data["token"] = ""
    elif "token" in data:
        raw = data["token"]
        if raw is None or str(raw).strip() == "":
            data.pop("token")
        else:
            me = await _validate_telegram_token(str(raw))
            data["token"] = str(raw).strip()
            tg_user = str(me.get("username") or "").strip()
            # Prefer Telegram's username when saving a new token (unless caller sent one).
            if tg_user and not (data.get("username") or "").strip():
                data["username"] = tg_user
    if "support_chat_id" in data:
        raw_sc = data["support_chat_id"]
        data["support_chat_id"] = (
            normalize_telegram_id(raw_sc, allow_group=True) or ""
            if raw_sc is not None and str(raw_sc).strip() != ""
            else ""
        )
    for k, v in data.items():
        setattr(b, k, v)
    # Auto-fill empty support chat from first enabled admin telegram_id.
    if not (b.support_chat_id or "").strip():
        suggested = await first_admin_telegram_id(db)
        if suggested:
            b.support_chat_id = suggested
            log.info("Auto-filled support_chat_id from admin telegram_id=%s", suggested)
    await db.commit()
    await bot_runtime.restart()
    await bot_runtime.refresh_command_menu()
    await db.refresh(b)
    return await _enrich_settings(db, b)


@router.get("/bot/status", response_model=BotRuntimeStatusOut)
async def bot_runtime_status(
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    b = await db.get(BotSettings, 1)
    if not b:
        b = BotSettings(id=1)
        db.add(b)
        await db.commit()
        await db.refresh(b)
    snap = bot_runtime.snapshot()
    suggested = await first_admin_telegram_id(db)
    return BotRuntimeStatusOut(
        status=str(snap.get("status") or "stopped"),
        task_running=bool(snap.get("task_running")),
        last_error=str(snap.get("last_error") or ""),
        last_ok_at=snap.get("last_ok_at"),  # type: ignore[arg-type]
        updates_handled=int(snap.get("updates_handled") or 0),
        offset=snap.get("offset"),  # type: ignore[arg-type]
        enabled=bool(b.enabled),
        token_configured=bool(b.token),
        username=b.username or "",
        hint=_runtime_hint(b, snap),
        admin_setup_hint=await admin_setup_hint(db, b),
        suggested_support_chat_id=suggested,
    )


@router.get("/bot/commands", response_model=list[BotCommandOut])
async def list_commands(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(BotCommand).order_by(BotCommand.sort_order, BotCommand.id))).scalars().all()
    return [_command_out(r) for r in rows]


@router.get("/bot/commands/{command_id}", response_model=BotCommandOut)
async def get_command(
    command_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(BotCommand, command_id)
    if not c:
        raise HTTPException(404, "Not found")
    return _command_out(c)


@router.post("/bot/commands", response_model=BotCommandOut)
async def create_command(
    body: BotCommandCreate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    command = _normalize_command(body.command)
    key = _slugify_key(body.key) if (body.key or "").strip() else _slugify_key(command.lstrip("/"))
    if key in BUILTIN_COMMAND_KEYS:
        raise HTTPException(400, f"key '{key}' is reserved for a built-in command")
    audience = _validate_audience(body.audience) or "everyone"
    existing_key = (await db.execute(select(BotCommand).where(BotCommand.key == key))).scalar_one_or_none()
    if existing_key:
        raise HTTPException(400, f"Command key '{key}' already exists")
    existing_cmd = (await db.execute(select(BotCommand).where(BotCommand.command == command))).scalar_one_or_none()
    if existing_cmd:
        raise HTTPException(400, f"Command '{command}' already exists")
    title = (body.title or "").strip() or command.lstrip("/")
    c = BotCommand(
        key=key,
        command=command,
        title=title,
        description=body.description or "",
        response_text=body.response_text or "",
        enabled=bool(body.enabled),
        audience=audience,
        sort_order=int(body.sort_order or 0),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    bot_runtime.invalidate_command_menu()
    await bot_runtime.refresh_command_menu(db)
    return _command_out(c)


@router.patch("/bot/commands/{command_id}", response_model=BotCommandOut)
async def update_command(
    command_id: int,
    body: BotCommandUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(BotCommand, command_id)
    if not c:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    if "audience" in data:
        data["audience"] = _validate_audience(data["audience"])
    if "command" in data:
        if c.key in BUILTIN_COMMAND_KEYS:
            raise HTTPException(400, "Cannot change the slash trigger of a built-in command")
        new_cmd = _normalize_command(data["command"])
        clash = (
            await db.execute(select(BotCommand).where(BotCommand.command == new_cmd, BotCommand.id != c.id))
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, f"Command '{new_cmd}' already exists")
        data["command"] = new_cmd
    for k, v in data.items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    bot_runtime.invalidate_command_menu()
    await bot_runtime.refresh_command_menu(db)
    return _command_out(c)


@router.post("/bot/commands/{command_id}/toggle", response_model=BotCommandOut)
async def toggle_command(
    command_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(BotCommand, command_id)
    if not c:
        raise HTTPException(404, "Not found")
    c.enabled = not c.enabled
    await db.commit()
    await db.refresh(c)
    bot_runtime.invalidate_command_menu()
    await bot_runtime.refresh_command_menu(db)
    return _command_out(c)


@router.delete("/bot/commands/{command_id}", response_model=MessageOut)
async def delete_command(
    command_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(BotCommand, command_id)
    if not c:
        raise HTTPException(404, "Not found")
    if c.key in PROTECTED_COMMAND_KEYS:
        raise HTTPException(
            400,
            f"Built-in {c.command} cannot be deleted. Disable it in settings, or edit its copy instead.",
        )
    await db.delete(c)
    await db.commit()
    bot_runtime.invalidate_command_menu()
    await bot_runtime.refresh_command_menu(db)
    return MessageOut(detail="Command deleted")


@router.get("/bot/workflows", response_model=list[BotWorkflowOut])
async def list_workflows(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    return (
        await db.execute(select(BotWorkflow).order_by(BotWorkflow.sort_order, BotWorkflow.id))
    ).scalars().all()


@router.get("/bot/workflows/{workflow_id}", response_model=BotWorkflowOut)
async def get_workflow(
    workflow_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(BotWorkflow, workflow_id)
    if not w:
        raise HTTPException(404, "Not found")
    return w


@router.post("/bot/workflows", response_model=BotWorkflowOut)
async def create_workflow(
    body: BotWorkflowCreate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    key = _slugify_key(body.key)
    exists = (await db.execute(select(BotWorkflow).where(BotWorkflow.key == key))).scalar_one_or_none()
    if exists:
        raise HTTPException(400, f"Workflow key '{key}' already exists")
    definition = dict(body.definition) if isinstance(body.definition, dict) else {}
    if not definition.get("trigger"):
        definition["trigger"] = "command:/start"
    if not isinstance(definition.get("steps"), list):
        definition["steps"] = []
    w = BotWorkflow(
        key=key,
        name=(body.name or key).strip() or key,
        description=body.description or "",
        enabled=bool(body.enabled),
        is_demo=False,
        sort_order=int(body.sort_order or 0),
        definition=definition,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return w


@router.patch("/bot/workflows/{workflow_id}", response_model=BotWorkflowOut)
async def update_workflow(
    workflow_id: int,
    body: BotWorkflowUpdate,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(BotWorkflow, workflow_id)
    if not w:
        raise HTTPException(404, "Not found")
    data = body.model_dump(exclude_unset=True)
    if "definition" in data and data["definition"] is not None:
        if not isinstance(data["definition"], dict):
            raise HTTPException(400, "definition must be an object")
        steps = data["definition"].get("steps")
        if steps is not None and not isinstance(steps, list):
            raise HTTPException(400, "definition.steps must be a list")
    for k, v in data.items():
        setattr(w, k, v)
    await db.commit()
    await db.refresh(w)
    return w


@router.post("/bot/workflows/{workflow_id}/toggle", response_model=BotWorkflowOut)
async def toggle_workflow(
    workflow_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(BotWorkflow, workflow_id)
    if not w:
        raise HTTPException(404, "Not found")
    w.enabled = not w.enabled
    await db.commit()
    await db.refresh(w)
    return w


@router.delete("/bot/workflows/{workflow_id}", response_model=MessageOut)
async def delete_workflow(
    workflow_id: int,
    _: User = Depends(require_admin),
    __: User = Depends(require_ready_user),
    db: AsyncSession = Depends(get_db),
):
    w = await db.get(BotWorkflow, workflow_id)
    if not w:
        raise HTTPException(404, "Not found")
    await db.delete(w)
    await db.commit()
    return MessageOut(detail="Workflow deleted")


@router.post("/bot/install-demos", response_model=MessageOut)
async def install_demos(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    existing_cmds = {c.key: c for c in (await db.execute(select(BotCommand))).scalars().all()}
    for cmd in DEMO_COMMANDS:
        if cmd["key"] not in existing_cmds:
            db.add(BotCommand(**cmd))
        else:
            row = existing_cmds[cmd["key"]]
            row.title = cmd.get("title", row.title)
            row.description = cmd.get("description", row.description)
            row.command = cmd.get("command", row.command)
            row.audience = cmd.get("audience", row.audience)
            row.sort_order = cmd.get("sort_order", row.sort_order)
            if cmd.get("response_text") is not None and (
                not row.response_text or cmd["key"] in ("run", "help", "formats", "scrapers", "support")
            ):
                row.response_text = cmd["response_text"]
            if cmd["key"] == "formats" and "enabled" in cmd:
                row.enabled = bool(cmd["enabled"])
            if cmd["key"] == "help":
                row.title = cmd.get("title", row.title)
                row.description = cmd.get("description", row.description)
    existing_wf = {w.key for w in (await db.execute(select(BotWorkflow))).scalars().all()}
    for i, wf in enumerate(DEMO_WORKFLOWS):
        payload = {**wf, "sort_order": wf.get("sort_order", (i + 1) * 10)}
        if payload["key"] not in existing_wf:
            db.add(BotWorkflow(**payload))
        else:
            row = (await db.execute(select(BotWorkflow).where(BotWorkflow.key == payload["key"]))).scalar_one()
            row.definition = payload["definition"]
            row.description = payload["description"]
            row.name = payload["name"]
            row.is_demo = True
            row.sort_order = payload["sort_order"]
    await db.commit()
    bot_runtime.invalidate_command_menu()
    await bot_runtime.refresh_command_menu(db)
    return MessageOut(detail="Demo commands and workflows installed/refreshed")


@router.post("/bot/restart", response_model=MessageOut)
async def restart_bot(_: User = Depends(require_admin), __: User = Depends(require_ready_user)):
    await bot_runtime.restart()
    await bot_runtime.refresh_command_menu()
    snap = bot_runtime.snapshot()
    detail = "Bot runtime restarted"
    if snap.get("task_running"):
        detail += f" (status={snap.get('status')})"
    else:
        detail += " — warning: task not running after restart"
    return MessageOut(detail=detail)
