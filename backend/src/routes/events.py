from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import EVENT_INGEST_ROLES
from src.controllers import event_controller
from src.middlewares.rbac import READ_ROLES, require_roles
from src.schemas.event import EventIn
from src.utils.pagination import page_params

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
def list_events(
    table_id: int | None = None,
    op: str | None = None,
    late_only: bool = False,
    q: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return event_controller.list_events(
        db, table_id=table_id, op=op, late_only=late_only, q=q, limit=page.limit, offset=page.offset
    )


@router.get("/{event_id}")
def get_event(
    event_id: int, db: Session = Depends(get_db), principal: dict = Depends(require_roles(*READ_ROLES))
) -> dict:
    return event_controller.get_event(db, event_id)


@router.post("", status_code=201)
def ingest_event(
    payload: EventIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*EVENT_INGEST_ROLES)),
) -> dict:
    return event_controller.ingest(db, payload.model_dump(), principal, request)
