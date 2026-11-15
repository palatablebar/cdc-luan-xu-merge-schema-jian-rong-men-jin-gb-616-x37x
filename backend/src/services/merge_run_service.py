"""合并批次服务：装载事件 -> 调用算法 A -> 落指标与拒绝行。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.constants.enums import REJECT_REASON_LABELS
from src.models.cdc_event import CdcEvent
from src.models.merge_run import MergeRun
from src.models.reject_row import RejectRow
from src.models.source_table import SourceTable
from src.services import watermark_merge
from src.utils.errors import AppError
from src.utils.timeutil import iso_utc, local_now, utcnow


def serialize(run: MergeRun, table: SourceTable | None = None) -> dict:
    metrics = run.metrics or {}
    return {
        "id": run.id,
        "run_no": run.run_no,
        "table_id": run.table_id,
        "table_code": table.code if table else None,
        "table_name": table.name if table else "全库批次",
        "scope": "全库" if run.table_id is None else (table.name if table else "已删除的表"),
        "trigger": run.trigger,
        "status": run.status,
        "operator_email": run.operator_email,
        "allowed_lateness_seconds": run.allowed_lateness_seconds,
        "events_in": run.events_in,
        "upsert_rows": run.upsert_rows,
        "deleted_rows": run.deleted_rows,
        "late_count": run.late_count,
        "late_ratio": metrics.get("late_ratio", 0.0),
        "dup_skipped": run.dup_skipped,
        "rejected_count": run.rejected_count,
        "watermark_at": iso_utc(run.watermark_at),
        "metrics": metrics,
        "log_text": run.log_text,
        "started_at": iso_utc(run.started_at),
        "finished_at": iso_utc(run.finished_at),
    }


def next_run_no(db: Session) -> str:
    prefix = f"MR{local_now():%Y%m%d}"
    count = db.scalar(select(func.count()).select_from(MergeRun).where(MergeRun.run_no.like(f"{prefix}%"))) or 0
    return f"{prefix}-{count + 1:04d}"


def _event_rows(db: Session, table_id: int | None) -> list[dict]:
    stmt = select(CdcEvent)
    if table_id is not None:
        stmt = stmt.where(CdcEvent.table_id == table_id)
    rows = db.scalars(stmt.order_by(CdcEvent.table_id, CdcEvent.seq, CdcEvent.id)).all()
    return [
        {
            "id": row.id,
            "table_id": row.table_id,
            "pk_value": row.pk_value,
            "lsn": row.lsn,
            "op": row.op,
            "event_time": row.event_time,
            "ingest_at": row.ingest_at,
            "before": row.before_data or {},
            "after": row.after_data or {},
        }
        for row in rows
    ]


def _persist_rejects(db: Session, run: MergeRun, rejects: list[dict]) -> None:
    db.execute(delete(RejectRow).where(RejectRow.run_id == run.id))
    seen: set[tuple[str, int, str, int | None]] = set()
    for item in rejects:
        key = (item["reason"], item["event_lsn"], item["pk_value"], item.get("table_id"))
        if key in seen:
            continue
        seen.add(key)
        db.add(
            RejectRow(
                run_id=run.id,
                table_id=item.get("table_id"),
                pk_value=item["pk_value"],
                op=item["op"],
                reason=item["reason"],
                event_lsn=item["event_lsn"],
                event_time=item.get("event_time"),
                detail=item.get("detail") or {},
                replay_status="pending",
            )
        )


def execute(db: Session, run: MergeRun, *, table_id: int | None) -> MergeRun:
    run.status = "running"
    events = _event_rows(db, table_id)
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    lateness = int(run.allowed_lateness_seconds or 30)

    ingest_times = [event["ingest_at"] for event in events if event["ingest_at"] is not None]
    watermark = (max(ingest_times) - timedelta(seconds=lateness)) if ingest_times else None

    grouped: dict[int, list[dict]] = {}
    for event in events:
        grouped.setdefault(int(event["table_id"]), []).append(event)

    totals = {
        "upsert_rows": 0,
        "deletes": 0,
        "late_count": 0,
        "dup_skipped": 0,
        "rejected": 0,
        "ops": {"c": 0, "u": 0, "d": 0},
    }
    per_table: dict[str, dict] = {}
    all_rejects: list[dict] = []
    samples: list[dict] = []
    for tid in sorted(grouped):
        table = tables.get(tid)
        result = watermark_merge.merge_events(
            grouped[tid],
            allowed_lateness_seconds=lateness,
            schema_columns=(table.schema_columns or []) if table else [],
            watermark=watermark,
        )
        metrics = result["metrics"]
        for key in ("upsert_rows", "deletes", "late_count", "dup_skipped", "rejected"):
            totals[key] += int(metrics.get(key) or 0)
        for op_key in ("c", "u", "d"):
            totals["ops"][op_key] += int((metrics.get("ops") or {}).get(op_key) or 0)
        per_table[table.code if table else str(tid)] = {
            "table_id": tid,
            "table_name": table.name if table else str(tid),
            "events": metrics.get("events_in", 0),
            "upsert_rows": metrics.get("upsert_rows", 0),
            "deletes": metrics.get("deletes", 0),
            "late_count": metrics.get("late_count", 0),
            "dup_skipped": metrics.get("dup_skipped", 0),
            "rejected": metrics.get("rejected", 0),
            "watermark": metrics.get("watermark"),
        }
        for reject in result["rejects"]:
            reject["table_id"] = tid
        all_rejects.extend(result["rejects"])
        # 折叠终态样例：把「按 event_time 排序后谁赢」在页面上可核对（取每表前 3 个主键）
        for row in result["rows"][:3]:
            samples.append(
                {
                    "table": table.code if table else str(tid),
                    "pk_value": row["pk_value"],
                    "after": row["after"],
                    "last_lsn": row["last_lsn"],
                    "last_event_time": row["last_event_time"].isoformat() if row["last_event_time"] else None,
                }
            )

    events_in = len(events)
    metrics = {
        "events_in": events_in,
        "upsert_rows": totals["upsert_rows"],
        "deletes": totals["deletes"],
        "late_count": totals["late_count"],
        "late_ratio": round(totals["late_count"] / events_in, 4) if events_in else 0.0,
        "dup_skipped": totals["dup_skipped"],
        "rejected": totals["rejected"],
        "ops": totals["ops"],
        "pks": sum(int(item.get("pks") or 0) for item in per_table.values()) or totals["upsert_rows"],
        "tables": per_table,
        "watermark": watermark.isoformat() if watermark else None,
        "allowed_lateness_seconds": lateness,
        "samples": samples[:9],
        "reject_reasons": {
            reason: sum(1 for item in all_rejects if item["reason"] == reason)
            for reason in sorted({item["reason"] for item in all_rejects})
        },
    }
    _persist_rejects(db, run, all_rejects)

    run.metrics = metrics
    run.events_in = events_in
    run.upsert_rows = totals["upsert_rows"]
    run.deleted_rows = totals["deletes"]
    run.late_count = totals["late_count"]
    run.dup_skipped = totals["dup_skipped"]
    run.rejected_count = totals["rejected"]
    run.watermark_at = watermark
    run.status = "success" if events_in else "partial"
    reason_text = "、".join(
        f"{REJECT_REASON_LABELS.get(reason, reason)} {count}" for reason, count in metrics["reject_reasons"].items()
    )
    run.log_text = (
        f"批次范围 {'全库' if table_id is None else '单表'}，读取 {events_in} 条事件；"
        f"水位 {metrics['watermark'] or '—'}（允许迟到 {lateness}s）；"
        f"迟到 {totals['late_count']} 条、重复 {totals['dup_skipped']} 条、拒绝 {totals['rejected']} 条"
        + (f"（{reason_text}）" if reason_text else "")
        + f"；折叠 {totals['upsert_rows']} 行 upsert、{totals['deletes']} 个删除终态"
    )
    run.finished_at = utcnow()
    db.flush()
    return run


def create_and_execute(
    db: Session,
    *,
    table_id: int | None,
    trigger: str,
    operator_email: str,
    allowed_lateness_seconds: int | None = None,
    seed_key: str | None = None,
) -> MergeRun:
    if seed_key:
        existing = db.scalar(select(MergeRun).where(MergeRun.seed_key == seed_key))
        if existing is not None:
            return existing
    table = None
    if table_id is not None:
        table = db.get(SourceTable, table_id)
        if table is None:
            raise AppError("NOT_FOUND", 404, {"entity": "source_table", "id": table_id})
    lateness = int(
        allowed_lateness_seconds
        if allowed_lateness_seconds is not None
        else (table.allowed_lateness_seconds if table else 30)
    )
    run = MergeRun(
        run_no=next_run_no(db),
        table_id=table_id,
        trigger=trigger,
        status="running",
        operator_email=operator_email,
        allowed_lateness_seconds=max(0, min(3600, lateness)),
        seed_key=seed_key,
    )
    db.add(run)
    db.flush()
    return execute(db, run, table_id=table_id)


def list_runs(
    db: Session,
    *,
    table_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(MergeRun)
    count_stmt = select(func.count()).select_from(MergeRun)
    if table_id:
        stmt = stmt.where(MergeRun.table_id == table_id)
        count_stmt = count_stmt.where(MergeRun.table_id == table_id)
    if status:
        stmt = stmt.where(MergeRun.status == status)
        count_stmt = count_stmt.where(MergeRun.status == status)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.order_by(MergeRun.id.desc()).limit(limit).offset(offset)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    return {
        "items": [serialize(row, tables.get(row.table_id)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_run(db: Session, run_id: int) -> MergeRun:
    run = db.get(MergeRun, run_id)
    if run is None:
        raise AppError("NOT_FOUND", 404, {"entity": "merge_run", "id": run_id})
    return run
