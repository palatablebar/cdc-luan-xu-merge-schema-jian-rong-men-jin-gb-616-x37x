from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import REPLAY_ROLES
from src.controllers import reject_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.schemas.reject import ReplayIn
from src.utils.pagination import page_params

router = APIRouter(prefix="/rejects", tags=["rejects"])


@router.get("")
def list_rejects(
    run_id: int | None = None,
    reason: str | None = None,
    replay_status: str | None = None,
    q: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return reject_controller.list_rejects(
        db,
        run_id=run_id,
        reason=reason,
        replay_status=replay_status,
        q=q,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{reject_id}")
def get_reject(
    reject_id: int, db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))
) -> dict:
    return reject_controller.get_reject(db, reject_id)


@router.patch("/{reject_id}/replay")
def replay(
    reject_id: int,
    payload: ReplayIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*REPLAY_ROLES)),
) -> dict:
    return reject_controller.replay(db, reject_id, payload.model_dump(), principal, request)
