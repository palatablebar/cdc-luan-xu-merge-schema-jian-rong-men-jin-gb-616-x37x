"""Schema 变更控制器：门禁复核 + 重算兼容性。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, schema_service, table_service


def list_changes(db: Session, **filters) -> dict:
    return schema_service.list_changes(db, **filters)


def get_change(db: Session, change_id: int) -> dict:
    return schema_service.serialize(schema_service.get_change(db, change_id))


def gate(db: Session, change_id: int, payload: dict, principal: dict, request: Request) -> dict:
    change = schema_service.get_change(db, change_id)
    schema_service.gate(
        db,
        change,
        gate_status=payload["gate_status"],
        note=payload.get("note"),
        reviewer=principal["email"],
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="schema.gate",
        entity="schema_change",
        entity_id=change.id,
        detail={"gate": change.gate_status, "compat": change.compat, "action": change.action},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return schema_service.serialize(change)


def reevaluate(db: Session, change_id: int, principal: dict, request: Request) -> dict:
    change = schema_service.get_change(db, change_id)
    table = table_service.get_table(db, change.table_id) if change.table_id else None
    schema_service.reevaluate(db, change, table)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="schema.reevaluate",
        entity="schema_change",
        entity_id=change.id,
        detail={"compat": change.compat, "action": change.action},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return schema_service.serialize(change)
