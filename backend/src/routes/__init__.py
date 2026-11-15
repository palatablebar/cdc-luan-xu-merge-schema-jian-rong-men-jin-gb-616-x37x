"""路由聚合：统一 /api 前缀（Nginx 反代时剥掉 /api）。"""
from fastapi import APIRouter

from src.routes.audit import router as audit_router
from src.routes.auth import router as auth_router
from src.routes.events import router as events_router
from src.routes.exports import router as exports_router
from src.routes.health import router as health_router
from src.routes.overview import router as overview_router
from src.routes.rejects import router as rejects_router
from src.routes.runs import router as runs_router
from src.routes.schema_changes import router as schema_changes_router
from src.routes.tables import router as tables_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(overview_router)
api_router.include_router(tables_router)
api_router.include_router(events_router)
api_router.include_router(runs_router)
api_router.include_router(rejects_router)
api_router.include_router(schema_changes_router)
api_router.include_router(exports_router)
api_router.include_router(audit_router)
