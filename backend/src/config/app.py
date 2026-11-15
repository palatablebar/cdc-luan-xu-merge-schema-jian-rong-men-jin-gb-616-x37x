"""FastAPI 装配：中间件顺序、异常处理、路由挂载、启动钩子。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config.database import engine, wait_for_db
from src.config.settings import settings
from src.middlewares.audit_logger import AuditMiddleware
from src.middlewares.auth import AuthMiddleware
from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.rate_limit import RateLimitMiddleware
from src.routes import api_router

logger = logging.getLogger(f"{settings.app_name}.app")


# 增量列：老库（create_all 不会 ALTER 已存在表）补齐关联门禁所需列，幂等。
_ADDITIVE_COLUMNS = {
    "source_tables": [
        ("parent_table_code", "VARCHAR(40) NULL"),
        ("parent_key_column", "VARCHAR(60) NULL"),
        ("parent_status_column", "VARCHAR(60) NULL DEFAULT 'status'"),
        ("void_statuses", "JSON NULL"),
    ],
    "merge_runs": [
        ("link_pending_count", "INT NOT NULL DEFAULT 0"),
        ("link_released_count", "INT NOT NULL DEFAULT 0"),
        ("link_withdrawn_count", "INT NOT NULL DEFAULT 0"),
        ("wide_published_count", "INT NOT NULL DEFAULT 0"),
        ("wide_withdrawn_count", "INT NOT NULL DEFAULT 0"),
    ],
}


def _ensure_additive_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table_name, columns in _ADDITIVE_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            current = {col["name"] for col in inspector.get_columns(table_name)}
            for column_name, ddl in columns:
                if column_name in current:
                    continue
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))


def _bootstrap_database() -> None:
    from src.models import Base  # noqa: WPS433 - 延迟导入避免循环

    wait_for_db()
    Base.metadata.create_all(bind=engine)
    try:
        _ensure_additive_columns()
    except Exception as exc:  # noqa: BLE001 - 兼容老库，失败不阻塞启动
        logger.warning("[BOOTSTRAP] additive columns failed (ignored): %s", exc)


def _run_seed_once() -> None:
    if not settings.seed_demo:
        return
    try:
        from scripts.seed_demo import seed_all

        seed_all()
    except Exception as exc:  # noqa: BLE001 - 启动阶段不阻塞
        logger.warning("[SEED_DEMO] seed failed (ignored): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap_database()
    _run_seed_once()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_title, version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(RateLimitMiddleware)
    register_exception_handlers(app)

    app.include_router(api_router, prefix="/api")
    app.include_router(api_router, include_in_schema=False)
    return app
