"""关联门禁查询服务：待关联清单、放行依据、宽表最终状态与可恢复重算。"""
from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    LINK_REASON_LABELS,
    LINK_STATUS_LABELS,
    LINK_STATUS_ORDER,
    LinkReason,
    LinkStatus,
    WideRowStatus,
)
from src.models.pending_link import PendingLink
from src.models.source_table import SourceTable
from src.models.wide_row import WideRow
from src.services import audit_service, link_gate
from src.utils.errors import not_found
from src.utils.timeutil import iso_utc, utcnow


def _tables(db: Session) -> dict[int, SourceTable]:
    return {table.id: table for table in db.scalars(select(SourceTable)).all()}


def list_links(
    db: Session,
    *,
    status: str | None = None,
    table_id: int | None = None,
    reason: str | None = None,
    parent_pk: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(PendingLink)
    count = select(func.count()).select_from(PendingLink)
    if status:
        stmt = stmt.where(PendingLink.status == status)
        count = count.where(PendingLink.status == status)
    if table_id:
        stmt = stmt.where(PendingLink.table_id == table_id)
        count = count.where(PendingLink.table_id == table_id)
    if reason:
        stmt = stmt.where(PendingLink.reason == reason)
        count = count.where(PendingLink.reason == reason)
    if parent_pk:
        stmt = stmt.where(PendingLink.parent_pk == parent_pk)
        count = count.where(PendingLink.parent_pk == parent_pk)
    if q:
        like = f"%{q.strip()}%"
        clause = or_(
            PendingLink.pk_value.ilike(like),
            PendingLink.parent_pk.ilike(like),
            PendingLink.parent_table_code.ilike(like),
        )
        stmt = stmt.where(clause)
        count = count.where(clause)
    total = db.scalar(count) or 0
    rows = db.scalars(stmt.order_by(PendingLink.id.desc()).limit(limit).offset(offset)).all()
    tables = _tables(db)
    return {
        "items": [link_gate.serialize_link(row, tables) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_link(db: Session, link_id: int) -> dict:
    row = db.get(PendingLink, link_id)
    if row is None:
        raise not_found("pending_link", link_id)
    tables = _tables(db)
    item = link_gate.serialize_link(row, tables)
    wide = None
    snapshot = row.parent_snapshot or {}
    if snapshot.get("parent_pk"):
        wide = db.scalar(
            select(WideRow).where(
                WideRow.parent_table_code == row.parent_table_code,
                WideRow.parent_pk == str(snapshot["parent_pk"]),
            )
        )
    item["wide_row"] = link_gate.serialize_wide(wide, tables) if wide else None
    return item


def list_wide_rows(
    db: Session,
    *,
    status: str | None = None,
    parent_table_code: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    stmt = select(WideRow)
    count = select(func.count()).select_from(WideRow)
    if status:
        stmt = stmt.where(WideRow.status == status)
        count = count.where(WideRow.status == status)
    if parent_table_code:
        stmt = stmt.where(WideRow.parent_table_code == parent_table_code)
        count = count.where(WideRow.parent_table_code == parent_table_code)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(WideRow.parent_pk.ilike(like))
        count = count.where(WideRow.parent_pk.ilike(like))
    total = db.scalar(count) or 0
    rows = db.scalars(stmt.order_by(WideRow.parent_pk).limit(limit).offset(offset)).all()
    tables = _tables(db)
    return {
        "items": [link_gate.serialize_wide(row, tables) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _stage_counts(db: Session, tables: dict[int, SourceTable]) -> dict:
    rows = db.execute(select(PendingLink.status, func.count()).group_by(PendingLink.status)).all()
    counts = {status: 0 for status in LINK_STATUS_ORDER}
    for status, value in rows:
        counts[status] = int(value)
    wide_rows = db.execute(select(WideRow.status, func.count()).group_by(WideRow.status)).all()
    wide = {status.value: 0 for status in WideRowStatus}
    for status, value in wide_rows:
        wide[status] = int(value)
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "wide_counts": wide,
        "wide_total": sum(wide.values()),
    }


def overview(db: Session) -> dict:
    """页面核对口径：待关联数量、放行依据、宽表最终状态 + 门禁不变量。"""
    tables = _tables(db)
    stage = _stage_counts(db, tables)
    children = [table for table in tables.values() if table.parent_table_code]

    pending_rows = db.scalars(
        select(PendingLink).where(PendingLink.status == LinkStatus.PENDING.value).order_by(PendingLink.id).limit(8)
    ).all()
    released_rows = db.scalars(
        select(PendingLink).where(PendingLink.status == LinkStatus.RELEASED.value).order_by(PendingLink.id.desc()).limit(8)
    ).all()
    withdrawn_rows = db.scalars(
        select(PendingLink).where(PendingLink.status == LinkStatus.WITHDRAWN.value).order_by(PendingLink.id.desc()).limit(8)
    ).all()

    def _samples(rows):
        return [link_gate.serialize_link(row, tables) for row in rows]

    latest_wide = db.scalars(select(WideRow).order_by(WideRow.updated_at.desc(), WideRow.id.desc()).limit(8)).all()

    # 不变量 1：released 的记录必须能在已发布父宽表里找到
    released_all = db.scalars(select(PendingLink).where(PendingLink.status == LinkStatus.RELEASED.value)).all()
    wide_cache = {(row.parent_table_code, row.parent_pk): row for row in db.scalars(select(WideRow)).all()}
    orphan = [
        link
        for link in released_all
        if (link.parent_table_code, str((link.parent_snapshot or {}).get("parent_pk")))
        not in wide_cache
        or wide_cache[(link.parent_table_code, str((link.parent_snapshot or {}).get("parent_pk")))].status
        != WideRowStatus.PUBLISHED.value
    ]
    # 不变量 2：已发布宽表里的 child_rows 必须全部来自 released 记录
    released_keys = {(link.table_id, link.pk_value) for link in released_all}
    code_to_id = {table.code: table.id for table in tables.values()}
    stray_children = []
    for wide in wide_cache.values():
        if wide.status != WideRowStatus.PUBLISHED.value:
            continue
        for child in wide.child_rows or []:
            code = child.get("child_code")
            table_id = code_to_id.get(code)
            if table_id is None or (table_id, child.get("pk_value")) not in released_keys:
                stray_children.append({"parent": f"{wide.parent_table_code}/{wide.parent_pk}", "child": code, "pk": child.get("pk_value")})

    per_child = []
    for child in children:
        links = db.execute(
            select(PendingLink.status, func.count())
            .where(PendingLink.table_id == child.id)
            .group_by(PendingLink.status)
        ).all()
        bucket = {status: 0 for status in LINK_STATUS_ORDER}
        for status, value in links:
            bucket[status] = int(value)
        per_child.append(
            {
                "table_id": child.id,
                "table_code": child.code,
                "table_name": child.name,
                "parent_table_code": child.parent_table_code,
                "parent_key_column": child.parent_key_column,
                "parent_status_column": child.parent_status_column or "status",
                "void_statuses": child.void_statuses or [],
                **bucket,
                "total": sum(bucket.values()),
            }
        )

    reason_rows = db.execute(
        select(PendingLink.reason, func.count()).group_by(PendingLink.reason)
    ).all()
    reasons = [
        {
            "key": reason,
            "label": LINK_REASON_LABELS.get(reason, reason),
            "count": int(value),
        }
        for reason, value in reason_rows
    ]

    return {
        "generated_at": iso_utc(utcnow()),
        "children": per_child,
        "pending": stage["counts"].get(LinkStatus.PENDING.value, 0),
        "released": stage["counts"].get(LinkStatus.RELEASED.value, 0),
        "withdrawn": stage["counts"].get(LinkStatus.WITHDRAWN.value, 0),
        "links_total": stage["total"],
        "wide_published": stage["wide_counts"].get(WideRowStatus.PUBLISHED.value, 0),
        "wide_withdrawn": stage["wide_counts"].get(WideRowStatus.WITHDRAWN.value, 0),
        "wide_total": stage["wide_total"],
        "reasons": reasons,
        "samples": {
            "pending": _samples(pending_rows),
            "released": _samples(released_rows),
            "withdrawn": _samples(withdrawn_rows),
        },
        "wide_rows": [link_gate.serialize_wide(row, tables) for row in latest_wide],
        "checks": {
            "orphan_released": len(orphan),
            "stray_children": len(stray_children),
            "stray_examples": stray_children[:5],
            "ok": not orphan and not stray_children,
            "release_requires_published_parent": True,
            "held_children_not_published": all(
                not (wide.child_rows or [])
                for wide in wide_cache.values()
                if wide.status == WideRowStatus.WITHDRAWN.value
            ),
        },
    }


def recompute(db: Session, *, principal: dict, request=None) -> dict:
    """可恢复入口：不新增批次，用库内事件重算折叠终态，再跑一遍关联门禁。"""
    result = link_gate.recompute_from_events(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        source="manual-reconcile",
    )
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="link.reconcile",
        entity="link_gate",
        entity_id=None,
        detail={
            "pending": result.get("pending"),
            "released": result.get("released"),
            "withdrawn": result.get("withdrawn"),
            "wide_published": result.get("wide_published"),
            "wide_withdrawn": result.get("wide_withdrawn"),
            "transitions": result.get("transitions"),
        },
    )
    db.commit()
    return {"summary": result, "overview": overview(db)}


def reason_breakdown(db: Session) -> list[dict]:
    rows = db.execute(select(PendingLink.reason, func.count()).group_by(PendingLink.reason)).all()
    return [
        {"key": reason, "label": LINK_REASON_LABELS.get(reason, reason), "count": int(count)}
        for reason, count in rows
        if count
    ]


def status_breakdown(db: Session) -> list[dict]:
    rows = db.execute(select(PendingLink.status, func.count()).group_by(PendingLink.status)).all()
    counts = {status: int(count) for status, count in rows}
    return [
        {"key": status, "label": LINK_STATUS_LABELS.get(status, status), "count": counts.get(status, 0)}
        for status in LINK_STATUS_ORDER
        if counts.get(status, 0)
    ]


# 供枚举面板核对：pending 的含义不是失败，而是「等父单」
PENDING_IS_HOLD = True

__all__ = [
    "list_links",
    "get_link",
    "list_wide_rows",
    "overview",
    "recompute",
    "reason_breakdown",
    "status_breakdown",
    "PENDING_IS_HOLD",
    "LinkReason",
]
