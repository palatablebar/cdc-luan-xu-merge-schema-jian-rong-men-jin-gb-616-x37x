"""事件流服务：分页查询、迟到标注、单条乱序注入。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import CDC_OP_LABELS, EVENT_STATUS_LABELS, RejectReason
from src.models.cdc_event import CdcEvent
from src.models.source_table import SourceTable
from src.utils.errors import AppError, not_found
from src.utils.timeutil import iso_utc, utcnow


def serialize(event: CdcEvent, table_code: str | None = None, table_name: str | None = None) -> dict:
    after = event.after_data or {}
    preview = {key: after[key] for key in list(after)[:3]}
    return {
        "id": event.id,
        "table_id": event.table_id,
        "table_code": table_code,
        "table_name": table_name,
        "pk_value": event.pk_value,
        "lsn": event.lsn,
        "seq": event.seq,
        "op": event.op,
        "op_label": CDC_OP_LABELS.get(event.op, event.op),
        "event_time": iso_utc(event.event_time),
        "ingest_at": iso_utc(event.ingest_at),
        "late_seconds": event.late_seconds,
        "is_late": event.is_late,
        "is_duplicate": event.is_duplicate,
        "status": event.status,
        "status_label": EVENT_STATUS_LABELS.get(event.status, event.status),
        "after_preview": preview,
        "after": after,
        "before": event.before_data or {},
    }


def list_events(
    db: Session,
    *,
    table_id: int | None = None,
    op: str | None = None,
    late_only: bool = False,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(CdcEvent)
    count_stmt = select(func.count()).select_from(CdcEvent)
    if table_id:
        stmt = stmt.where(CdcEvent.table_id == table_id)
        count_stmt = count_stmt.where(CdcEvent.table_id == table_id)
    if op:
        stmt = stmt.where(CdcEvent.op == op)
        count_stmt = count_stmt.where(CdcEvent.op == op)
    if late_only:
        stmt = stmt.where(CdcEvent.is_late.is_(True))
        count_stmt = count_stmt.where(CdcEvent.is_late.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(CdcEvent.pk_value.ilike(like) | CdcEvent.status.ilike(like))
        count_stmt = count_stmt.where(CdcEvent.pk_value.ilike(like) | CdcEvent.status.ilike(like))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(CdcEvent.ingest_at.desc(), CdcEvent.id.desc()).limit(limit).offset(offset)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    items = [
        serialize(row, tables.get(row.table_id).code if tables.get(row.table_id) else None,
                  tables.get(row.table_id).name if tables.get(row.table_id) else None)
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_event(db: Session, event_id: int) -> CdcEvent:
    event = db.get(CdcEvent, event_id)
    if event is None:
        raise not_found("cdc_event", event_id)
    return event


def watermark_of(db: Session, table_id: int, allowed_lateness_seconds: int = 30) -> datetime | None:
    latest = db.scalar(select(func.max(CdcEvent.ingest_at)).where(CdcEvent.table_id == table_id))
    if latest is None:
        return None
    return latest - timedelta(seconds=max(0, allowed_lateness_seconds))


def ingest(db: Session, payload: dict) -> CdcEvent:
    table = db.get(SourceTable, payload["table_id"])
    if table is None:
        raise not_found("source_table", payload["table_id"])
    duplicate = db.scalar(
        select(CdcEvent).where(
            CdcEvent.table_id == table.id,
            CdcEvent.pk_value == payload["pk_value"],
            CdcEvent.lsn == payload["lsn"],
        )
    )
    if duplicate is not None:
        raise AppError("CONFLICT", 409, {"table_id": table.id, "pk_value": payload["pk_value"], "lsn": payload["lsn"]})

    ingest_at = utcnow()
    event_time = payload.get("event_time") or ingest_at
    if isinstance(event_time, str):
        event_time = datetime.fromisoformat(event_time.replace("Z", ""))
    event_time = event_time.replace(tzinfo=None) if event_time.tzinfo else event_time
    watermark = watermark_of(db, table.id, table.allowed_lateness_seconds)
    is_late = watermark is not None and event_time < watermark
    late_seconds = int((watermark - event_time).total_seconds()) if is_late and watermark else 0
    seq = (db.scalar(select(func.max(CdcEvent.seq)).where(CdcEvent.table_id == table.id)) or 0) + 1
    event = CdcEvent(
        table_id=table.id,
        pk_value=payload["pk_value"],
        lsn=int(payload["lsn"]),
        seq=int(seq),
        op=payload["op"],
        event_time=event_time,
        ingest_at=ingest_at,
        before_data=payload.get("before") or {},
        after_data=payload.get("after") or {},
        is_late=is_late,
        late_seconds=late_seconds,
        is_duplicate=False,
        status="late" if is_late else "applied",
    )
    db.add(event)
    table.last_event_at = ingest_at
    db.flush()
    return event


def reason_breakdown(db: Session) -> list[dict]:
    from src.constants.enums import REJECT_REASON_LABELS
    from src.models.reject_row import RejectRow

    rows = db.execute(select(RejectRow.reason, func.count()).group_by(RejectRow.reason)).all()
    counts = Counter({reason: int(count) for reason, count in rows})
    order = [reason.value for reason in RejectReason]
    return [
        {"key": reason, "label": REJECT_REASON_LABELS.get(reason, reason), "count": counts.get(reason, 0)}
        for reason in order
        if counts.get(reason, 0) > 0
    ]
