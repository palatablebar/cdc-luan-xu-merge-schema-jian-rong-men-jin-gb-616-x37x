"""源表与订阅服务：登记、查询、按表统计事件/迟到。"""
from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.models.cdc_event import CdcEvent
from src.models.source_table import SourceTable
from src.utils.errors import AppError, not_found
from src.utils.timeutil import iso_utc


def serialize(table: SourceTable, stats: dict | None = None) -> dict:
    stats = stats or {}
    return {
        "id": table.id,
        "code": table.code,
        "name": table.name,
        "database_name": table.database_name,
        "table_name": table.table_name,
        "pk_column": table.pk_column,
        "schema_columns": table.schema_columns or [],
        "column_count": len(table.schema_columns or []),
        "binlog_file": table.binlog_file,
        "binlog_position": table.binlog_position,
        "allowed_lateness_seconds": table.allowed_lateness_seconds,
        "owner": table.owner,
        "note": table.note,
        "enabled": table.enabled,
        "event_count": stats.get("event_count", 0),
        "late_count": stats.get("late_count", 0),
        "reject_count": stats.get("reject_count", 0),
        "last_event_at": iso_utc(table.last_event_at),
        "created_at": iso_utc(table.created_at),
        "updated_at": iso_utc(table.updated_at),
    }


def _stats(db: Session) -> dict[int, dict]:
    rows = db.execute(
        select(
            CdcEvent.table_id,
            func.count(),
            func.sum(case((CdcEvent.is_late.is_(True), 1), else_=0)),
        ).group_by(CdcEvent.table_id)
    ).all()
    return {int(table_id): {"event_count": int(total or 0), "late_count": int(late or 0)} for table_id, total, late in rows}


def list_tables(
    db: Session,
    *,
    q: str | None = None,
    enabled: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(SourceTable)
    count_stmt = select(func.count()).select_from(SourceTable)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(SourceTable.name.ilike(like) | SourceTable.code.ilike(like) | SourceTable.table_name.ilike(like))
        count_stmt = count_stmt.where(
            SourceTable.name.ilike(like) | SourceTable.code.ilike(like) | SourceTable.table_name.ilike(like)
        )
    if enabled is not None:
        stmt = stmt.where(SourceTable.enabled == enabled)
        count_stmt = count_stmt.where(SourceTable.enabled == enabled)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(SourceTable.id).limit(limit).offset(offset)).all()
    stats = _stats(db)
    return {
        "items": [serialize(row, stats.get(row.id)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_table(db: Session, table_id: int) -> SourceTable:
    table = db.get(SourceTable, table_id)
    if table is None:
        raise not_found("source_table", table_id)
    return table


def create_table(db: Session, payload: dict) -> SourceTable:
    exists = db.scalar(select(SourceTable).where(SourceTable.code == payload["code"]))
    if exists is not None:
        raise AppError("CONFLICT", 409, {"code": payload["code"]})
    table = SourceTable(
        code=payload["code"],
        name=payload["name"],
        database_name=payload.get("database_name") or "biz_prod",
        table_name=payload["table_name"],
        pk_column=payload.get("pk_column") or "id",
        schema_columns=payload.get("schema_columns") or [],
        binlog_file=payload.get("binlog_file"),
        binlog_position=int(payload.get("binlog_position") or 0),
        allowed_lateness_seconds=int(payload.get("allowed_lateness_seconds") or 30),
        owner=payload.get("owner") or "衡远保险 · 数据集成组",
        note=payload.get("note"),
        enabled=payload.get("enabled", True),
    )
    db.add(table)
    db.flush()
    return table


def update_table(db: Session, table: SourceTable, payload: dict) -> SourceTable:
    for field in (
        "name",
        "allowed_lateness_seconds",
        "binlog_file",
        "binlog_position",
        "schema_columns",
        "owner",
        "note",
        "enabled",
    ):
        if field in payload and payload[field] is not None:
            setattr(table, field, payload[field])
    db.flush()
    return table
