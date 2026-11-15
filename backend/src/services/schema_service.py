"""Schema 变更服务：查询、门禁审批、重算兼容性（算法 B）。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    CHANGE_KIND_LABELS,
    COMPAT_LABELS,
    GATE_ACTION_LABELS,
    GATE_STATUS_LABELS,
    SEVERITY_LABELS,
)
from src.models.schema_change import SchemaChange
from src.models.source_table import SourceTable
from src.services import schema_gate
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc, utcnow


def serialize(change: SchemaChange, table: SourceTable | None = None) -> dict:
    return {
        "id": change.id,
        "table_id": change.table_id,
        "table_code": table.code if table else None,
        "table_name": table.name if table else None,
        "change_key": change.change_key,
        "kind": change.kind,
        "kind_label": CHANGE_KIND_LABELS.get(change.kind, change.kind),
        "column_name": change.column_name,
        "target_column": change.target_column,
        "before_columns": change.before_columns or [],
        "after_columns": change.after_columns or [],
        "compat": change.compat,
        "compat_label": COMPAT_LABELS.get(change.compat, change.compat),
        "severity": change.severity,
        "severity_label": SEVERITY_LABELS.get(change.severity, change.severity),
        "action": change.action,
        "action_label": GATE_ACTION_LABELS.get(change.action, change.action),
        "rollback_sql": change.rollback_sql,
        "summary": change.summary,
        "detail": change.detail or {},
        "gate_status": change.gate_status,
        "gate_status_label": GATE_STATUS_LABELS.get(change.gate_status, change.gate_status),
        "reviewer": change.reviewer,
        "review_note": change.review_note,
        "detected_at": iso_utc(change.detected_at),
        "created_at": iso_utc(change.created_at),
    }


def list_changes(
    db: Session,
    *,
    table_id: int | None = None,
    compat: str | None = None,
    gate_status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(SchemaChange)
    count_stmt = select(func.count()).select_from(SchemaChange)
    if table_id:
        stmt = stmt.where(SchemaChange.table_id == table_id)
        count_stmt = count_stmt.where(SchemaChange.table_id == table_id)
    if compat:
        stmt = stmt.where(SchemaChange.compat == compat)
        count_stmt = count_stmt.where(SchemaChange.compat == compat)
    if gate_status:
        stmt = stmt.where(SchemaChange.gate_status == gate_status)
        count_stmt = count_stmt.where(SchemaChange.gate_status == gate_status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(SchemaChange.summary.ilike(like) | SchemaChange.change_key.ilike(like))
        count_stmt = count_stmt.where(SchemaChange.summary.ilike(like) | SchemaChange.change_key.ilike(like))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(SchemaChange.id.desc()).limit(limit).offset(offset)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    return {
        "items": [serialize(row, tables.get(row.table_id)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_change(db: Session, change_id: int) -> SchemaChange:
    change = db.get(SchemaChange, change_id)
    if change is None:
        raise not_found("schema_change", change_id)
    return change


def gate(db: Session, change: SchemaChange, *, gate_status: str, note: str | None, reviewer: str) -> SchemaChange:
    change.gate_status = gate_status
    change.review_note = note
    change.reviewer = reviewer
    change.updated_at = utcnow()
    db.flush()
    return change


def reevaluate(db: Session, change: SchemaChange, table: SourceTable | None) -> SchemaChange:
    """用算法 B 重新跑一遍存储的前后列快照，覆盖结论（门禁状态保留）。"""
    result = schema_gate.evaluate_schema_change(
        change.before_columns or [],
        change.after_columns or [],
        table_name=(table.table_name if table else "ods_table"),
        rename_hints=(change.detail or {}).get("rename_hints") or {},
        change_key=change.change_key,
    )
    change.kind = result["change_kind"]
    change.column_name = result["column_name"]
    change.target_column = result["target_column"]
    change.compat = result["compat"]
    change.severity = result["severity"]
    change.action = result["action"]
    change.rollback_sql = result["rollback_sql"]
    change.summary = result["summary"]
    change.detail = {**(change.detail or {}), **result["counts"], "findings": result["findings"]}
    change.updated_at = utcnow()
    db.flush()
    return change


def compat_breakdown(db: Session) -> list[dict]:
    from src.constants.enums import Compat

    rows = db.execute(select(SchemaChange.compat, func.count()).group_by(SchemaChange.compat)).all()
    counts = {compat: int(count) for compat, count in rows}
    return [
        {"key": compat.value, "label": COMPAT_LABELS.get(compat.value, compat.value), "count": counts.get(compat.value, 0)}
        for compat in Compat
    ]
