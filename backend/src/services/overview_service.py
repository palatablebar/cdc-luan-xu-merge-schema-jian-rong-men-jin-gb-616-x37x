"""总览：事件水位、迟到/拒绝、门禁结论、近期趋势。"""
from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config.settings import settings
from src.constants.enums import (
    CDC_OP_LABELS,
    COMPAT_LABELS,
    GATE_STATUS_LABELS,
    REJECT_REASON_LABELS,
    REPLAY_STATUS_LABELS,
    RUN_STATUS_LABELS,
    SEVERITY_LABELS,
)
from src.models.audit import AuditLog
from src.models.cdc_event import CdcEvent
from src.models.merge_run import MergeRun
from src.models.pending_link import PendingLink
from src.models.reject_row import RejectRow
from src.models.schema_change import SchemaChange
from src.models.source_table import SourceTable
from src.models.wide_row import WideRow
from src.utils.timeutil import iso_utc, local_now, utcnow

from src.constants.log_templates import ACTIONS


def worker_alive() -> bool:
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        value = client.get(f"{settings.app_name}:worker:heartbeat")
        return bool(value) and (time.time() - float(value)) < 120
    except Exception:
        return False


def _breakdown(db: Session, column, labels: dict, order: list[str] | None = None) -> list[dict]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    counts = {key: int(count) for key, count in rows}
    keys = order if order else sorted(counts)
    return [{"key": key, "label": labels.get(key, key), "count": counts.get(key, 0)} for key in keys if counts.get(key)]


def overview(db: Session) -> dict:
    tables_total = db.scalar(select(func.count()).select_from(SourceTable)) or 0
    tables_enabled = db.scalar(select(func.count()).select_from(SourceTable).where(SourceTable.enabled.is_(True))) or 0
    events_total = db.scalar(select(func.count()).select_from(CdcEvent)) or 0
    late_total = db.scalar(select(func.count()).select_from(CdcEvent).where(CdcEvent.is_late.is_(True))) or 0
    dup_total = db.scalar(select(func.count()).select_from(CdcEvent).where(CdcEvent.is_duplicate.is_(True))) or 0
    reject_total = db.scalar(select(func.count()).select_from(RejectRow)) or 0
    reject_pending = db.scalar(select(func.count()).select_from(RejectRow).where(RejectRow.replay_status == "pending")) or 0
    runs_total = db.scalar(select(func.count()).select_from(MergeRun)) or 0
    gate_total = db.scalar(select(func.count()).select_from(SchemaChange)) or 0
    gate_pending = db.scalar(select(func.count()).select_from(SchemaChange).where(SchemaChange.gate_status == "pending")) or 0
    gate_breaking = db.scalar(select(func.count()).select_from(SchemaChange).where(SchemaChange.compat == "breaking")) or 0
    link_pending = db.scalar(select(func.count()).select_from(PendingLink).where(PendingLink.status == "pending")) or 0
    link_released = db.scalar(select(func.count()).select_from(PendingLink).where(PendingLink.status == "released")) or 0
    link_withdrawn = db.scalar(select(func.count()).select_from(PendingLink).where(PendingLink.status == "withdrawn")) or 0
    wide_published = db.scalar(select(func.count()).select_from(WideRow).where(WideRow.status == "published")) or 0
    wide_withdrawn = db.scalar(select(func.count()).select_from(WideRow).where(WideRow.status == "withdrawn")) or 0

    latest_run = db.scalar(select(MergeRun).order_by(MergeRun.id.desc()).limit(1))
    tables = db.scalars(select(SourceTable).order_by(SourceTable.id)).all()
    per_table_rows = db.execute(
        select(CdcEvent.table_id, func.count(), func.max(CdcEvent.ingest_at)).group_by(CdcEvent.table_id)
    ).all()
    late_rows = db.execute(
        select(CdcEvent.table_id, func.count()).where(CdcEvent.is_late.is_(True)).group_by(CdcEvent.table_id)
    ).all()
    late_map = {int(tid): int(count) for tid, count in late_rows}
    table_cards = []
    for tid, count, last_ingest in per_table_rows:
        table = next((item for item in tables if item.id == int(tid)), None)
        if table is None:
            continue
        watermark = (last_ingest - timedelta(seconds=table.allowed_lateness_seconds)) if last_ingest else None
        table_cards.append(
            {
                "table_id": table.id,
                "code": table.code,
                "name": table.name,
                "table_name": table.table_name,
                "events": int(count),
                "late": late_map.get(int(tid), 0),
                "watermark": iso_utc(watermark),
                "lateness": table.allowed_lateness_seconds,
                "last_ingest": iso_utc(last_ingest),
            }
        )

    since = utcnow() - timedelta(days=7)
    recent = db.scalars(select(CdcEvent).where(CdcEvent.ingest_at >= since)).all()
    today = local_now().date()
    trend = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        day_events = [row for row in recent if (row.ingest_at + timedelta(minutes=settings.app_timezone_offset_minutes)).date() == day]
        trend.append(
            {
                "date": day.isoformat(),
                "events": len(day_events),
                "late": sum(1 for row in day_events if row.is_late),
            }
        )

    recent_runs = db.scalars(select(MergeRun).order_by(MergeRun.id.desc()).limit(4)).all()
    recent_audit = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(8)).all()

    return {
        "tables_total": tables_total,
        "tables_enabled": tables_enabled,
        "events_total": events_total,
        "late_total": late_total,
        "late_ratio": round(late_total / events_total, 4) if events_total else 0.0,
        "dup_total": dup_total,
        "reject_total": reject_total,
        "reject_pending": reject_pending,
        "runs_total": runs_total,
        "gate_total": gate_total,
        "gate_pending": gate_pending,
        "gate_breaking": gate_breaking,
        "link_pending": link_pending,
        "link_released": link_released,
        "link_withdrawn": link_withdrawn,
        "wide_published": wide_published,
        "wide_withdrawn": wide_withdrawn,
        "worker_alive": worker_alive(),
        "latest_run": {
            "id": latest_run.id,
            "run_no": latest_run.run_no,
            "scope": "全库批次"
            if latest_run.table_id is None
            else next(
                (table.name for table in tables if table.id == latest_run.table_id),
                f"表 #{latest_run.table_id}",
            ),
            "status": latest_run.status,
            "status_label": RUN_STATUS_LABELS.get(latest_run.status, latest_run.status),
            "events_in": latest_run.events_in,
            "upsert_rows": latest_run.upsert_rows,
            "late_count": latest_run.late_count,
            "rejected_count": latest_run.rejected_count,
            "watermark_at": iso_utc(latest_run.watermark_at),
            "started_at": iso_utc(latest_run.started_at),
        }
        if latest_run
        else None,
        "recent_runs": [
            {
                "id": run.id,
                "run_no": run.run_no,
                "status": run.status,
                "status_label": RUN_STATUS_LABELS.get(run.status, run.status),
                "events_in": run.events_in,
                "late_count": run.late_count,
                "rejected_count": run.rejected_count,
                "started_at": iso_utc(run.started_at),
            }
            for run in recent_runs
        ],
        "reject_reasons": _breakdown(db, RejectRow.reason, REJECT_REASON_LABELS),
        "replay": _breakdown(db, RejectRow.replay_status, REPLAY_STATUS_LABELS),
        "compat": _breakdown(db, SchemaChange.compat, COMPAT_LABELS),
        "gate_status": _breakdown(db, SchemaChange.gate_status, GATE_STATUS_LABELS),
        "severity": _breakdown(db, SchemaChange.severity, SEVERITY_LABELS, ["high", "medium", "low"]),
        "ops": _breakdown(db, CdcEvent.op, CDC_OP_LABELS, ["c", "u", "d"]),
        "tables": table_cards,
        "trend": trend,
        "recent_audit": [
            {
                "id": row.id,
                "actor_email": row.actor_email,
                "actor_role": row.actor_role,
                "action": row.action,
                "action_label": ACTIONS.get(row.action, row.action),
                "entity": row.entity,
                "created_at": iso_utc(row.created_at),
            }
            for row in recent_audit
        ],
    }
