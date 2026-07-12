from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, billing, infra, jobs, settings_bot, stats, support, users
from app.bot.runtime import bot_runtime
from app.core.config import get_settings
from app.core.database import SessionLocal, init_db
from app.services.bootstrap import bootstrap


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await bootstrap(db)
    bot_runtime.start()
    yield
    await bot_runtime.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(billing.router, prefix="/api")
    app.include_router(infra.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(stats.router, prefix="/api")
    app.include_router(settings_bot.router, prefix="/api")
    app.include_router(support.router, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
