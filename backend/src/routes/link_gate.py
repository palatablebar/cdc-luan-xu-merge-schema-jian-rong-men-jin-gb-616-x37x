"""跨表关联门禁路由：待关联清单 / 宽表状态 / 重算。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import LINK_RECOMPUTE_ROLES
from src.controllers import link_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.utils.pagination import page_params

router = APIRouter(prefix="/link-gate", tags=["link-gate"])


@router.get("")
def overview(db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))) -> dict:
    return link_controller.overview(db)


@router.get("/links")
def list_links(
    status: str | None = None,
    table_id: int | None = None,
    reason: str | None = None,
    parent_pk: str | None = None,
    q: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return link_controller.list_links(
        db,
        status=status,
        table_id=table_id,
        reason=reason,
        parent_pk=parent_pk,
        q=q,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/links/{link_id}")
def get_link(
    link_id: int, db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))
) -> dict:
    return link_controller.get_link(db, link_id)


@router.get("/wide-rows")
def list_wide_rows(
    status: str | None = None,
    parent_table_code: str | None = None,
    q: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return link_controller.list_wide_rows(
        db, status=status, parent_table_code=parent_table_code, q=q, limit=page.limit, offset=page.offset
    )


@router.post("/reconcile")
def reconcile(
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*LINK_RECOMPUTE_ROLES)),
) -> dict:
    return link_controller.recompute(db, principal, request)
