"""幂等 seed：3 账号 / 3 张源表 / 2400 条 CDC 事件 / 2 个合并批次 / 6 个 Schema 变更。

自然键 upsert（存在即跳过，不覆盖用户改过的复核/门禁状态）：
  users.email / source_tables.code / cdc_events(table_id, pk_value, lsn, seq)
  merge_runs.seed_key / schema_changes.change_key / audit_logs.seed_key

SEED_SUMMARY 口径：
  users=账号数 sources=源表数 runs=合并批次数 findings=Schema 变更数 pending=待门禁复核数
连续两次执行这一行逐字一致。
"""
from __future__ import annotations

from sqlalchemy import func, select

from src.config.database import session_scope
from src.models.audit import AuditLog
from src.models.cdc_event import CdcEvent
from src.models.merge_run import MergeRun
from src.models.reject_row import RejectRow
from src.models.schema_change import SchemaChange
from src.models.source_table import SourceTable
from src.models.user import User
from src.services import merge_run_service, schema_gate
from src.utils.security import hash_password

from scripts.demo_config import (
    DEMO_PASSWORD,
    DEMO_USERS,
    ORG,
    SCHEMA_CHANGES,
    SEED_AUDIT,
    SEED_RUNS,
    TABLES,
    build_events,
)


def ensure_users(db) -> int:
    for email, display_name, role in DEMO_USERS:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(DEMO_PASSWORD),
                    display_name=display_name,
                    role=role,
                    org=ORG,
                )
            )
    db.flush()
    return db.scalar(select(func.count()).select_from(User)) or 0


def ensure_tables(db) -> int:
    for spec in TABLES:
        table = db.scalar(select(SourceTable).where(SourceTable.code == spec["code"]))
        if table is None:
            table = SourceTable(
                code=spec["code"],
                name=spec["name"],
                database_name="biz_prod",
                table_name=spec["table_name"],
                pk_column=spec["pk_column"],
                schema_columns=spec["schema_columns"],
                binlog_file=spec["binlog_file"],
                binlog_position=spec["binlog_position"],
                allowed_lateness_seconds=spec["allowed_lateness_seconds"],
                owner=ORG,
                note=spec["note"],
                enabled=True,
            )
            db.add(table)
            db.flush()
    return db.scalar(select(func.count()).select_from(SourceTable)) or 0


def ensure_events(db) -> int:
    tables = {table.code: table for table in db.scalars(select(SourceTable)).all()}
    for spec in TABLES:
        table = tables.get(spec["code"])
        if table is None:
            continue
        existing = db.scalar(select(func.count()).select_from(CdcEvent).where(CdcEvent.table_id == table.id)) or 0
        if existing:
            continue
        rows = build_events(spec)
        db.add_all(
            [
                CdcEvent(
                    table_id=table.id,
                    pk_value=row["pk_value"],
                    lsn=row["lsn"],
                    seq=row["seq"],
                    op=row["op"],
                    event_time=row["event_time"],
                    ingest_at=row["ingest_at"],
                    before_data=row["before_data"],
                    after_data=row["after_data"],
                    is_late=row["is_late"],
                    late_seconds=row["late_seconds"],
                    is_duplicate=row["is_duplicate"],
                    status=row["status"],
                )
                for row in rows
            ]
        )
        table.last_event_at = max(row["ingest_at"] for row in rows)
        db.flush()
    return db.scalar(select(func.count()).select_from(CdcEvent)) or 0


def ensure_schema_changes(db) -> int:
    tables = {table.code: table for table in db.scalars(select(SourceTable)).all()}
    for spec in SCHEMA_CHANGES:
        table = tables.get(spec["table_code"])
        if table is None:
            continue
        existing = db.scalar(select(SchemaChange).where(SchemaChange.change_key == spec["change_key"]))
        if existing is not None:
            continue
        before = list(table.schema_columns or [])
        after = [dict(col) for col in spec["after_columns"]]
        result = schema_gate.evaluate_schema_change(
            before,
            after,
            table_name=table.table_name,
            rename_hints=spec.get("rename_hints") or {},
            change_key=spec["change_key"],
        )
        db.add(
            SchemaChange(
                table_id=table.id,
                change_key=spec["change_key"],
                kind=result["change_kind"],
                column_name=result["column_name"],
                target_column=result["target_column"],
                before_columns=before,
                after_columns=after,
                compat=result["compat"],
                severity=result["severity"],
                action=result["action"],
                rollback_sql=result["rollback_sql"],
                summary=result["summary"] + f"｜{spec['note']}",
                detail={
                    "rename_hints": spec.get("rename_hints") or {},
                    "note": spec["note"],
                    **result["counts"],
                    "findings": result["findings"],
                },
                gate_status=spec["gate_status"],
                reviewer="admin@cdcmerge.io" if spec["gate_status"] != "pending" else None,
                review_note="seed 预置门禁结论" if spec["gate_status"] != "pending" else None,
            )
        )
        db.flush()
    return db.scalar(select(func.count()).select_from(SchemaChange)) or 0


def ensure_runs(db) -> int:
    tables = {table.code: table for table in db.scalars(select(SourceTable)).all()}
    for seed_key, code, trigger, operator in SEED_RUNS:
        table = tables.get(code) if code else None
        if code and table is None:
            continue
        merge_run_service.create_and_execute(
            db,
            table_id=table.id if table else None,
            trigger=trigger,
            operator_email=operator,
            seed_key=seed_key,
        )
    return db.scalar(select(func.count()).select_from(MergeRun)) or 0


def ensure_audit(db) -> None:
    for seed_key, email, role, action, entity, detail in SEED_AUDIT:
        exists = db.scalar(select(AuditLog).where(AuditLog.seed_key == seed_key))
        if exists is None:
            db.add(
                AuditLog(
                    actor_email=email,
                    actor_role=role,
                    action=action,
                    entity=entity,
                    detail=detail,
                    layer="seed",
                    seed_key=seed_key,
                )
            )
    db.flush()


def seed_all() -> dict:
    with session_scope() as db:
        users = ensure_users(db)
        tables = ensure_tables(db)
        ensure_events(db)
        ensure_schema_changes(db)
        runs = ensure_runs(db)
        ensure_audit(db)
        findings = db.scalar(select(func.count()).select_from(SchemaChange)) or 0
        pending = db.scalar(select(func.count()).select_from(SchemaChange).where(SchemaChange.gate_status == "pending")) or 0
        rejects = db.scalar(select(func.count()).select_from(RejectRow)) or 0
        summary = (
            f"SEED_SUMMARY brand=cdcmerge users={users} sources={tables} "
            f"runs={runs} findings={findings} pending={pending}"
        )
        print(summary, flush=True)
        print(f"[seed] cdc_events={db.scalar(select(func.count()).select_from(CdcEvent)) or 0} reject_rows={rejects}", flush=True)
        return {"users": users, "sources": tables, "runs": runs, "findings": findings, "pending": pending}


if __name__ == "__main__":
    seed_all()
