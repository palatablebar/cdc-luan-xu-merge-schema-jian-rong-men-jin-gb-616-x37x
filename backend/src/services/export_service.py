"""导出服务：合并批次 / 拒绝行 / Schema 门禁 CSV（均含算法真实输出列）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import (
    CHANGE_KIND_LABELS,
    COMPAT_LABELS,
    GATE_ACTION_LABELS,
    GATE_STATUS_LABELS,
    LINK_REASON_LABELS,
    LINK_STATUS_LABELS,
    REJECT_REASON_LABELS,
    REPLAY_STATUS_LABELS,
    RUN_STATUS_LABELS,
    SEVERITY_LABELS,
    TRIGGER_LABELS,
    WIDE_ROW_STATUS_LABELS,
)
from src.models.merge_run import MergeRun
from src.models.pending_link import PendingLink
from src.models.reject_row import RejectRow
from src.models.schema_change import SchemaChange
from src.models.source_table import SourceTable
from src.models.wide_row import WideRow
from src.utils.csv_export import to_csv


def _stamp(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def merge_runs_csv(db: Session) -> bytes:
    runs = db.scalars(select(MergeRun).order_by(MergeRun.id)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    headers = [
        "运行号", "范围", "触发", "状态", "允许迟到(s)", "事件数", "upsert行",
        "删除终态", "迟到数", "迟到率", "重复去重", "拒绝行", "待关联", "已放行", "已撤回",
        "宽表已发布", "宽表已撤回", "水位线", "操作人", "开始时间", "结束时间",
    ]
    rows = []
    for run in runs:
        table = tables.get(run.table_id)
        metrics = run.metrics or {}
        rows.append(
            [
                run.run_no,
                "全库" if run.table_id is None else (table.name if table else f"table#{run.table_id}"),
                TRIGGER_LABELS.get(run.trigger, run.trigger),
                RUN_STATUS_LABELS.get(run.status, run.status),
                run.allowed_lateness_seconds,
                run.events_in,
                run.upsert_rows,
                run.deleted_rows,
                run.late_count,
                f"{metrics.get('late_ratio', 0):.4f}",
                run.dup_skipped,
                run.rejected_count,
                run.link_pending_count,
                run.link_released_count,
                run.link_withdrawn_count,
                run.wide_published_count,
                run.wide_withdrawn_count,
                _stamp(run.watermark_at),
                run.operator_email,
                _stamp(run.started_at),
                _stamp(run.finished_at),
            ]
        )
    return to_csv(headers, rows)


def link_rows_csv(db: Session, status: str | None = None) -> bytes:
    """待关联 / 放行 / 撤回清单导出：数量、放行依据、父单快照都可核对。"""
    stmt = select(PendingLink)
    if status:
        stmt = stmt.where(PendingLink.status == status)
    links = db.scalars(stmt.order_by(PendingLink.id)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    headers = [
        "记录号", "从表", "主键", "父表", "外键值", "关联状态", "判定原因", "事件时间",
        "放行批次", "放行依据", "撤回依据", "首次保留批次", "最近更新",
    ]
    rows = [
        [
            link.id,
            tables.get(link.table_id).code if tables.get(link.table_id) else "",
            link.pk_value,
            link.parent_table_code or "",
            link.parent_pk or "",
            LINK_STATUS_LABELS.get(link.status, link.status),
            LINK_REASON_LABELS.get(link.reason, link.reason),
            _stamp(link.event_time),
            link.released_run_id or "",
            link.release_basis or "",
            link.withdraw_basis or "",
            link.first_seen_run_id or "",
            _stamp(link.updated_at),
        ]
        for link in links
    ]
    return to_csv(headers, rows)


def wide_rows_csv(db: Session, status: str | None = None) -> bytes:
    stmt = select(WideRow)
    if status:
        stmt = stmt.where(WideRow.status == status)
    wide_rows = db.scalars(stmt.order_by(WideRow.parent_pk)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    headers = ["父表", "父单主键", "父单状态列值", "宽表状态", "关联依据", "子表", "子记录行数", "子记录主键", "来源批次", "发布时间"]
    rows = []
    for wide in wide_rows:
        table = tables.get(wide.parent_table_id) if wide.parent_table_id else None
        status_column = (table.parent_status_column if table else None) or "status"
        rows.append(
            [
                wide.parent_table_code,
                wide.parent_pk,
                (wide.parent_row or {}).get(status_column, ""),
                WIDE_ROW_STATUS_LABELS.get(wide.status, wide.status),
                wide.link_basis,
                ",".join(
                    sorted({str(child.get("child_code")) for child in (wide.child_rows or []) if child.get("child_code")})
                ),
                len(wide.child_rows or []),
                ",".join(str(child.get("pk_value")) for child in (wide.child_rows or [])),
                wide.source_run_id or "",
                _stamp(wide.published_at),
            ]
        )
    return to_csv(headers, rows)


def rejects_csv(db: Session, run_id: int | None = None) -> bytes:
    stmt = select(RejectRow)
    if run_id:
        stmt = stmt.where(RejectRow.run_id == run_id)
    rows = db.scalars(stmt.order_by(RejectRow.id)).all()
    runs = {run.id: run.run_no for run in db.scalars(select(MergeRun)).all()}
    tables = {table.id: table.name for table in db.scalars(select(SourceTable)).all()}
    headers = ["拒绝行号", "批次", "源表", "主键", "操作", "判定原因", "LSN", "事件时间", "处置状态", "复核人", "备注"]
    body = [
        [
            row.id,
            runs.get(row.run_id, ""),
            tables.get(row.table_id, ""),
            row.pk_value,
            row.op,
            REJECT_REASON_LABELS.get(row.reason, row.reason),
            row.event_lsn,
            _stamp(row.event_time),
            REPLAY_STATUS_LABELS.get(row.replay_status, row.replay_status),
            row.reviewer or "",
            row.replay_note or "",
        ]
        for row in rows
    ]
    return to_csv(headers, body)


def schema_changes_csv(db: Session, run_scope: int | None = None) -> bytes:
    stmt = select(SchemaChange)
    if run_scope:
        stmt = stmt.where(SchemaChange.table_id == run_scope)
    changes = db.scalars(stmt.order_by(SchemaChange.id)).all()
    tables = {table.id: table for table in db.scalars(select(SourceTable)).all()}
    headers = [
        "变更键", "源表", "变更类型", "列", "兼容性", "严重度", "动作", "门禁状态", "回滚 SQL", "判定摘要", "复核人", "发现时间",
    ]
    rows = [
        [
            change.change_key,
            (tables.get(change.table_id).table_name if tables.get(change.table_id) else ""),
            CHANGE_KIND_LABELS.get(change.kind, change.kind),
            change.column_name or "",
            COMPAT_LABELS.get(change.compat, change.compat),
            SEVERITY_LABELS.get(change.severity, change.severity),
            GATE_ACTION_LABELS.get(change.action, change.action),
            GATE_STATUS_LABELS.get(change.gate_status, change.gate_status),
            change.rollback_sql,
            change.summary,
            change.reviewer or "",
            _stamp(change.detected_at),
        ]
        for change in changes
    ]
    return to_csv(headers, rows)
