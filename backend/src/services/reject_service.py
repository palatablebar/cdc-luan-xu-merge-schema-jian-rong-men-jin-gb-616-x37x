"""拒绝行服务：按批次/原因筛选、重放处置。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import CDC_OP_LABELS, REJECT_REASON_LABELS, REPLAY_STATUS_LABELS
from src.models.merge_run import MergeRun
from src.models.reject_row import RejectRow
from src.models.source_table import SourceTable
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc


def serialize(row: RejectRow, run_no: str | None = None, table_name: str | None = None) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "run_no": run_no,
        "table_id": row.table_id,
        "table_name": table_name,
        "pk_value": row.pk_value,
        "op": row.op,
        "op_label": CDC_OP_LABELS.get(row.op, row.op),
        "reason": row.reason,
        "reason_label": REJECT_REASON_LABELS.get(row.reason, row.reason),
        "event_lsn": row.event_lsn,
        "event_time": iso_utc(row.event_time),
        "detail": row.detail or {},
        "replay_status": row.replay_status,
        "replay_status_label": REPLAY_STATUS_LABELS.get(row.replay_status, row.replay_status),
        "replay_note": row.replay_note,
        "reviewer": row.reviewer,
        "created_at": iso_utc(row.created_at),
    }


def list_rejects(
    db: Session,
    *,
    run_id: int | None = None,
    reason: str | None = None,
    replay_status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(RejectRow)
    count_stmt = select(func.count()).select_from(RejectRow)
    if run_id:
        stmt = stmt.where(RejectRow.run_id == run_id)
        count_stmt = count_stmt.where(RejectRow.run_id == run_id)
    if reason:
        stmt = stmt.where(RejectRow.reason == reason)
        count_stmt = count_stmt.where(RejectRow.reason == reason)
    if replay_status:
        stmt = stmt.where(RejectRow.replay_status == replay_status)
        count_stmt = count_stmt.where(RejectRow.replay_status == replay_status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(RejectRow.pk_value.ilike(like))
        count_stmt = count_stmt.where(RejectRow.pk_value.ilike(like))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(RejectRow.id.desc()).limit(limit).offset(offset)).all()
    runs = {run.id: run.run_no for run in db.scalars(select(MergeRun)).all()}
    tables = {table.id: table.name for table in db.scalars(select(SourceTable)).all()}
    return {
        "items": [serialize(row, runs.get(row.run_id), tables.get(row.table_id)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_reject(db: Session, reject_id: int) -> RejectRow:
    row = db.get(RejectRow, reject_id)
    if row is None:
        raise not_found("reject_row", reject_id)
    return row


def replay(db: Session, row: RejectRow, *, replay_status: str, note: str | None, reviewer: str) -> RejectRow:
    row.replay_status = replay_status
    row.replay_note = note
    row.reviewer = reviewer
    db.flush()
    return row


def reason_breakdown(db: Session) -> list[dict]:
    from src.constants.enums import RejectReason

    rows = db.execute(select(RejectRow.reason, func.count()).group_by(RejectRow.reason)).all()
    counts = {reason: int(count) for reason, count in rows}
    return [
        {"key": reason.value, "label": REJECT_REASON_LABELS.get(reason.value, reason.value), "count": counts.get(reason.value, 0)}
        for reason in RejectReason
        if counts.get(reason.value, 0)
    ]
