"""事件流控制器：列表/详情/乱序注入。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, event_service


def list_events(db: Session, **filters) -> dict:
    return event_service.list_events(db, **filters)


def get_event(db: Session, event_id: int) -> dict:
    return event_service.serialize(event_service.get_event(db, event_id))


def ingest(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    event = event_service.ingest(db, payload)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="event.ingest",
        entity="cdc_event",
        entity_id=event.id,
        detail={"pk": event.pk_value, "lsn": event.lsn, "op": event.op, "late": event.is_late},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return event_service.serialize(event)
