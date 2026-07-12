from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_security_settings, require_admin, require_ready_user
from app.bot.demos import DEMO_COMMANDS, DEMO_WORKFLOWS
from app.bot.runtime import bot_runtime
from app.core.database import get_db
from app.models import BotCommand, BotSettings, BotWorkflow, User
from app.schemas import (
    BotCommandOut,
    BotCommandUpdate,
    BotSettingsOut,
    BotSettingsUpdate,
    BotWorkflowCreate,
    BotWorkflowOut,
    BotWorkflowUpdate,
    MessageOut,
    SecuritySettingsOut,
    SecuritySettingsUpdate,
)

router = APIRouter(tags=["settings-bot"])


def _slugify_key(raw: str) -> str:
    key = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (raw or "").strip().lower())
    key = key.strip("_-")[:64]
    return key or "workflow"


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


@router.get("/bot/settings", response_model=BotSettingsOut)
async def get_bot_settings(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    b = await db.get(BotSettings, 1)
    if not b:
        b = BotSettings(id=1)
        db.add(b)
        await db.commit()
        await db.refresh(b)
    return BotSettingsOut(
        enabled=b.enabled,
        token_configured=bool(b.token),
        username=b.username,
        mode=b.mode,
        welcome_text=b.welcome_text,
        notify_interval_sec=b.notify_interval_sec,
        support_enabled=b.support_enabled,
        support_chat_id=b.support_chat_id,
        public_packages=b.public_packages,
        deliver_results_telegram=b.deliver_results_telegram,
        admin_commands_enabled=b.admin_commands_enabled,
    )


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
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    await db.commit()
    await bot_runtime.restart()
    return await get_bot_settings(_, __, db)


@router.get("/bot/commands", response_model=list[BotCommandOut])
async def list_commands(_: User = Depends(require_admin), __: User = Depends(require_ready_user), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(BotCommand).order_by(BotCommand.sort_order))).scalars().all()


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
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    await db.commit()
    await db.refresh(c)
    return c


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
    existing_cmds = {c.key for c in (await db.execute(select(BotCommand))).scalars().all()}
    for cmd in DEMO_COMMANDS:
        if cmd["key"] not in existing_cmds:
            db.add(BotCommand(**cmd))
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
    return MessageOut(detail="Demo commands and workflows installed/refreshed")


@router.post("/bot/restart", response_model=MessageOut)
async def restart_bot(_: User = Depends(require_admin), __: User = Depends(require_ready_user)):
    await bot_runtime.restart()
    return MessageOut(detail="Bot runtime restarted")
