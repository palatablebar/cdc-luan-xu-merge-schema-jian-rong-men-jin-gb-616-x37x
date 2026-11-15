from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import GATE_ROLES
from src.controllers import schema_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.schemas.schema_change import GateIn
from src.utils.pagination import page_params

router = APIRouter(prefix="/schema-changes", tags=["schema-changes"])


@router.get("")
def list_changes(
    table_id: int | None = None,
    compat: str | None = None,
    gate_status: str | None = None,
    q: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return schema_controller.list_changes(
        db,
        table_id=table_id,
        compat=compat,
        gate_status=gate_status,
        q=q,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{change_id}")
def get_change(
    change_id: int, db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))
) -> dict:
    return schema_controller.get_change(db, change_id)


@router.patch("/{change_id}/gate")
def gate(
    change_id: int,
    payload: GateIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*GATE_ROLES)),
) -> dict:
    return schema_controller.gate(db, change_id, payload.model_dump(), principal, request)


@router.post("/{change_id}/reevaluate")
def reevaluate(
    change_id: int,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*GATE_ROLES)),
) -> dict:
    return schema_controller.reevaluate(db, change_id, principal, request)
