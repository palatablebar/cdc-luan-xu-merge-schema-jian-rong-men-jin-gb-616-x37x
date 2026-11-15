"""合并批次控制器。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.services import audit_service, merge_run_service, reject_service, table_service


def create_run(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    run = merge_run_service.create_and_execute(
        db,
        table_id=payload.get("table_id"),
        trigger=payload.get("trigger") or "manual",
        operator_email=principal["email"],
        allowed_lateness_seconds=payload.get("allowed_lateness_seconds"),
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="run.create",
        entity="merge_run",
        entity_id=run.id,
        detail={"run_no": run.run_no, "status": run.status, "events": run.events_in, "late": run.late_count},
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return merge_run_service.serialize(run, table_service.get_table(db, run.table_id) if run.table_id else None)


def list_runs(db: Session, **filters) -> dict:
    return merge_run_service.list_runs(db, **filters)


def get_run(db: Session, run_id: int) -> dict:
    run = merge_run_service.get_run(db, run_id)
    table = table_service.get_table(db, run.table_id) if run.table_id else None
    return merge_run_service.serialize(run, table)


def get_run_detail(db: Session, run_id: int) -> dict:
    run = merge_run_service.get_run(db, run_id)
    table = table_service.get_table(db, run.table_id) if run.table_id else None
    rejects = reject_service.list_rejects(db, run_id=run.id, limit=100, offset=0)
    return {"run": merge_run_service.serialize(run, table), "rejects": rejects}
