"""源表控制器：写操作落领域审计。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, table_service


def list_tables(db: Session, **filters) -> dict:
    return table_service.list_tables(db, **filters)


def get_table(db: Session, table_id: int) -> dict:
    return table_service.serialize(table_service.get_table(db, table_id))


def create_table(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    table = table_service.create_table(db, payload)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="table.create",
        entity="source_table",
        entity_id=table.id,
        detail={"code": table.code, "table": table.table_name},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return table_service.serialize(table)


def update_table(db: Session, table_id: int, payload: dict, principal: dict, request: Request) -> dict:
    table = table_service.get_table(db, table_id)
    table_service.update_table(db, table, payload)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="table.update",
        entity="source_table",
        entity_id=table.id,
        detail={"fields": list(payload.keys())},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return table_service.serialize(table)
