"""拒绝行控制器：列表 + 重放处置。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, reject_service


def list_rejects(db: Session, **filters) -> dict:
    return reject_service.list_rejects(db, **filters)


def get_reject(db: Session, reject_id: int) -> dict:
    return reject_service.serialize(reject_service.get_reject(db, reject_id))


def replay(db: Session, reject_id: int, payload: dict, principal: dict, request: Request) -> dict:
    row = reject_service.get_reject(db, reject_id)
    reject_service.replay(
        db,
        row,
        replay_status=payload["replay_status"],
        note=payload.get("note"),
        reviewer=principal["email"],
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="reject.replay",
        entity="reject_row",
        entity_id=row.id,
        detail={"reason": row.reason, "status": row.replay_status, "pk": row.pk_value},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return reject_service.serialize(row)
