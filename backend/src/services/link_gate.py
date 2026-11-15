"""跨表关联门禁：父单未到的从表记录先保留、不发布宽表；父单到达后自动放行；
父单作废后撤回已发布结果并重算。

口径
----
- 从表：`source_tables.parent_table_code` 指向父表编码，`parent_key_column` 是从表里指
  向父表主键的列（普通外键，或与父表主键列同名的业务键）。
- 父单有效：父表折叠终态存在，且 `parent_status_column` 的值不在 `void_statuses` 里。
- 父单未到 / 外键为空 -> 从表记录进 `pending_links`，`status=pending`，**不发布宽表**。
- 父单有效 -> `status=released`，写进对应父单 `wide_rows.child_rows`，并留放行依据。
- 父单作废（删除 d，或状态命中 void_statuses）-> 该父单的从表记录 `status=withdrawn`，
  宽表行标记 `withdrawn`、子行清空，计数重算。
- 幂等：`pending_links` 自然键 (table_id, pk_value)、`wide_rows` 自然键
  (parent_table_code, parent_pk)；同一批重复执行只重复校验，状态不变就不写审计。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    LINK_REASON_LABELS,
    LINK_STATUS_LABELS,
    WIDE_ROW_STATUS_LABELS,
    LinkReason,
    LinkStatus,
    WideRowStatus,
)
from src.models.merge_run import MergeRun
from src.models.pending_link import PendingLink
from src.models.source_table import SourceTable
from src.models.wide_row import WideRow
from src.services import audit_service
from src.utils.timeutil import iso_utc, utcnow

LINK_ACTION_BY_STATUS = {
    LinkStatus.PENDING.value: "link.hold",
    LinkStatus.RELEASED.value: "link.release",
    LinkStatus.WITHDRAWN.value: "link.withdraw",
}


# ------------------------------------------------------------------ 序列化

def serialize_link(link: PendingLink, tables: dict[int, SourceTable] | None = None) -> dict:
    tables = tables or {}
    table = tables.get(link.table_id)
    return {
        "id": link.id,
        "table_id": link.table_id,
        "table_code": table.code if table else None,
        "table_name": table.name if table else None,
        "pk_value": link.pk_value,
        "op": link.op,
        "event_lsn": link.event_lsn,
        "event_time": iso_utc(link.event_time),
        "parent_table_code": link.parent_table_code,
        "parent_table_id": link.parent_table_id,
        "parent_pk": link.parent_pk,
        "parent_key_column": link.parent_key_column,
        "status": link.status,
        "status_label": LINK_STATUS_LABELS.get(link.status, link.status),
        "reason": link.reason,
        "reason_label": LINK_REASON_LABELS.get(link.reason, link.reason),
        "payload": link.payload or {},
        "parent_snapshot": link.parent_snapshot or {},
        "first_seen_run_id": link.first_seen_run_id,
        "released_run_id": link.released_run_id,
        "release_basis": link.release_basis,
        "withdraw_basis": link.withdraw_basis,
        "hold_basis": (
            f"父单 {link.parent_table_code} 上不存在 {link.parent_key_column}={link.parent_pk}，保留待关联且不发布"
            if link.status == LinkStatus.PENDING.value
            else None
        ),
        "basis": (
            link.withdraw_basis
            if link.status == LinkStatus.WITHDRAWN.value
            else link.release_basis
            if link.status == LinkStatus.RELEASED.value
            else f"父单 {link.parent_table_code} 上不存在 {link.parent_key_column}={link.parent_pk}，保留待关联且不发布"
        ),
        "released_at": iso_utc(link.released_at),
        "held_at": iso_utc(link.created_at),
        "withdrawn_at": iso_utc(link.withdrawn_at),
        "updated_at": iso_utc(link.updated_at),
    }


def serialize_wide(wide: WideRow, tables: dict[int, SourceTable] | None = None) -> dict:
    tables = tables or {}
    table = tables.get(wide.parent_table_id) if wide.parent_table_id else None
    return {
        "id": wide.id,
        "parent_table_code": wide.parent_table_code,
        "parent_table_name": table.name if table else None,
        "parent_pk": wide.parent_pk,
        "child_codes": sorted({str(row.get("child_code")) for row in (wide.child_rows or []) if row.get("child_code")}),
        "status": wide.status,
        "status_label": WIDE_ROW_STATUS_LABELS.get(wide.status, wide.status),
        "parent_row": wide.parent_row or {},
        "child_rows": wide.child_rows or [],
        "child_count": len(wide.child_rows or []),
        "link_basis": wide.link_basis,
        "source_run_id": wide.source_run_id,
        "metric": wide.metric or {},
        "published_at": iso_utc(wide.published_at),
        "updated_at": iso_utc(wide.updated_at),
    }


def tables_by_id(db: Session) -> dict[int, SourceTable]:
    return {table.id: table for table in db.scalars(select(SourceTable)).all()}


# ------------------------------------------------------------------ 小工具

def _is_void(parent: SourceTable, row: dict) -> tuple[bool, str | None]:
    column = parent.parent_status_column or "status"
    value = (row or {}).get(column)
    if value is not None and str(value) in {str(item) for item in (parent.void_statuses or [])}:
        return True, str(value)
    return False, str(value) if value is not None else None


def _aliases(parent: SourceTable, canonical: str, row: dict) -> set[str]:
    aliases = {str(canonical)}
    if parent.pk_column:
        value = (row or {}).get(parent.pk_column)
        if value is not None and str(value) != "":
            aliases.add(str(value))
    # 父单主键常带业务前缀（POL-100000）；从表外键存的是数字部分，补一条尾号别名。
    tail = canonical.rsplit("-", 1)[-1]
    if tail and tail.isdigit() and tail != canonical:
        aliases.add(tail)
    return aliases


def _payload_of(row: dict) -> dict:
    event_time = row.get("last_event_time")
    return {
        "after": row.get("after") or {},
        "last_lsn": int(row.get("last_lsn") or 0),
        "last_event_time": event_time.isoformat() if hasattr(event_time, "isoformat") else event_time,
    }


def _status_text(column: str, value: str | None, *, deleted: bool = False) -> str:
    if deleted:
        return "父单已被删除 d"
    if value is None:
        return f"{column} 无值"
    return f"{column}={value}"


def _basis_hold(parent_code: str | None, key_column: str | None, raw: object) -> str:
    if raw is None or str(raw) == "":
        return f"从表记录缺少外键 {key_column or '?'}，无法解析父单 {parent_code}，保留待关联"
    return f"父单 {parent_code} 上不存在 {key_column}={raw}，保留待关联且不发布"


def _basis_release(parent_code: str, snapshot: dict, key_column: str | None, raw: object) -> str:
    column = snapshot.get("status_column") or "status"
    value = snapshot.get("status_value")
    return (
        f"父单 {parent_code}/{snapshot.get('parent_pk')} 有效（{_status_text(column, value)}）；"
        f"按 {key_column}={raw} 关联放行"
    )


def _basis_withdraw(parent_code: str, snapshot: dict, key_column: str | None, raw: object, *, deleted: bool = False) -> str:
    column = snapshot.get("status_column") or "status"
    value = snapshot.get("status_value")
    return (
        f"父单 {parent_code}/{snapshot.get('parent_pk')} 判为作废（{_status_text(column, value, deleted=deleted)}）；"
        f"按 {key_column}={raw} 关联的从表记录撤回，宽表行重算"
    )


# ------------------------------------------------------------------ 主流程

def reconcile(
    db: Session,
    *,
    folded_by_table: dict[int, list[dict]] | None = None,
    deleted_by_table: dict[int, Iterable[str]] | None = None,
    scope_tables: set[int] | None = None,
    run: MergeRun | None = None,
    actor_email: str = "system",
    actor_role: str = "system",
    source: str = "merge",
    layer: str = "domain",
    audit: bool = True,
) -> dict:
    """拿父/子表的折叠终态重算关联门禁；状态不变时无任何写入。"""
    folded_by_table = folded_by_table or {}
    deleted_by_table = deleted_by_table or {}
    tables = tables_by_id(db)
    children = [table for table in tables.values() if table.parent_table_code]
    parent_codes = {table.parent_table_code for table in children}
    parents = [table for table in tables.values() if table.code in parent_codes]
    scope = scope_tables if scope_tables is not None else set(tables)
    source_run_id = run.id if run else None

    before = _counts(db, children)
    transitions: list[dict] = []
    batch_counts: dict[str, dict] = {
        child.code: {"held": 0, "released": 0, "withdrawn": 0, "rows": 0} for child in children
    }

    # 1) 父表折叠终态 -> 宽表父行（有效发布 / 作废撤回）
    for parent in parents:
        if parent.id not in scope:
            continue
        for row in folded_by_table.get(parent.id, []):
            after = row.get("after") or {}
            is_void, status_value = _is_void(parent, after)
            _upsert_parent_wide(
                db,
                parent=parent,
                canonical=str(row["pk_value"]),
                parent_row=dict(after),
                is_void=is_void,
                status_value=status_value,
                source_run_id=source_run_id,
                transitions=transitions,
                source=source,
            )
        for pk in deleted_by_table.get(parent.id, []):
            _withdraw_parent_wide(
                db,
                parent=parent,
                canonical=str(pk),
                source_run_id=source_run_id,
                transitions=transitions,
                source=source,
            )

    alias_map = _alias_map(db, parents)

    # 2) 本批次内的从表折叠终态 / 删除 -> 关联状态
    processed: dict[int, set[str]] = defaultdict(set)
    for child in children:
        if child.id not in scope:
            continue
        for pk in deleted_by_table.get(child.id, []):
            pk = str(pk)
            processed[child.id].add(pk)
            _transition(
                db,
                child=child,
                pk=pk,
                op="d",
                payload={},
                parent_raw=None,
                target=LinkStatus.WITHDRAWN.value,
                reason=LinkReason.CHILD_DELETED.value,
                basis=f"从表 {child.code}/{pk} 收到删除操作 d，从宽表撤回并重算",
                snapshot=None,
                source_run_id=source_run_id,
                transitions=transitions,
                source=source,
            )
        for row in folded_by_table.get(child.id, []):
            pk = str(row["pk_value"])
            processed[child.id].add(pk)
            after = row.get("after") or {}
            raw = after.get(child.parent_key_column) if child.parent_key_column else None
            state = _resolve(alias_map, raw)
            if state is None:
                target = LinkStatus.PENDING.value
                reason = LinkReason.PARENT_MISSING.value
                basis = _basis_hold(child.parent_table_code, child.parent_key_column, raw)
                snapshot = None
            elif state["valid"]:
                target = LinkStatus.RELEASED.value
                reason = LinkReason.PARENT_LINKED.value
                basis = _basis_release(state["parent_code"], state["snapshot"], child.parent_key_column, raw)
                snapshot = state["snapshot"]
            else:
                target = LinkStatus.WITHDRAWN.value
                reason = LinkReason.PARENT_VOID.value
                basis = _basis_withdraw(state["parent_code"], state["snapshot"], child.parent_key_column, raw)
                snapshot = state["snapshot"]
            _transition(
                db,
                child=child,
                pk=pk,
                op="u",
                payload=_payload_of(row),
                parent_raw=str(raw) if raw is not None else None,
                target=target,
                reason=reason,
                basis=basis,
                snapshot=snapshot,
                source_run_id=source_run_id,
                transitions=transitions,
                source=source,
            )
            batch_counts[child.code]["rows"] += 1
            if target == LinkStatus.PENDING.value:
                batch_counts[child.code]["held"] += 1
            elif target == LinkStatus.RELEASED.value:
                batch_counts[child.code]["released"] += 1
            else:
                batch_counts[child.code]["withdrawn"] += 1

    # 3) 存量待关联 / 已放行记录：父单到达自动放行，父单作废自动撤回
    existing_links = db.scalars(select(PendingLink)).all()
    for link in existing_links:
        child = tables.get(link.table_id)
        if child is None or link.pk_value in processed[link.table_id]:
            continue
        if link.reason == LinkReason.CHILD_DELETED.value:
            continue
        state = _resolve(alias_map, link.parent_pk)
        if state is None:
            if link.status == LinkStatus.RELEASED.value:
                # 父单行曾经存在但已消失（表/订阅变更），按未到处理
                target = LinkStatus.PENDING.value
                reason = LinkReason.PARENT_MISSING.value
                basis = _basis_hold(child.parent_table_code, child.parent_key_column, link.parent_pk)
                snapshot = None
            elif link.status == LinkStatus.WITHDRAWN.value:
                continue
            else:
                target = LinkStatus.PENDING.value
                reason = LinkReason.PARENT_MISSING.value
                basis = _basis_hold(child.parent_table_code, child.parent_key_column, link.parent_pk)
                snapshot = None
        elif state["valid"]:
            target = LinkStatus.RELEASED.value
            reason = LinkReason.PARENT_LINKED.value
            basis = _basis_release(state["parent_code"], state["snapshot"], child.parent_key_column, link.parent_pk)
            snapshot = state["snapshot"]
        else:
            target = LinkStatus.WITHDRAWN.value
            reason = LinkReason.PARENT_VOID.value
            basis = _basis_withdraw(state["parent_code"], state["snapshot"], child.parent_key_column, link.parent_pk)
            snapshot = state["snapshot"]
        _transition(
            db,
            child=child,
            pk=link.pk_value,
            op=link.op,
            payload=link.payload or {},
            parent_raw=link.parent_pk,
            target=target,
            reason=reason,
            basis=basis,
            snapshot=snapshot,
            source_run_id=source_run_id,
            transitions=transitions,
            source=source,
            link=link,
        )

    # 4) 用「已放行」关联重算宽表 child_rows（宽表最终状态以此为准）
    _rebuild_wide_children(db, tables, parents, children)

    # 5) 审计：按动作聚合一条，只统计真正发生迁移的记录；状态不变 -> 零新增。
    link_transitions = [entry for entry in transitions if entry.get("scope") == "link"]
    wide_transitions = [entry for entry in transitions if entry.get("scope") == "wide_row"]
    if audit:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for entry in link_transitions:
            grouped[entry.get("action") or "link.transition"].append(entry)
        for action, entries in sorted(grouped.items()):
            samples = [
                {
                    "pk_value": entry.get("pk_value"),
                    "parent": f"{entry.get('parent_table')}/{entry.get('parent_key')}",
                    "from": entry.get("from_status"),
                    "to": entry.get("to_status"),
                    "basis": entry.get("basis"),
                }
                for entry in entries[:5]
            ]
            audit_service.record(
                db,
                actor_email=actor_email,
                actor_role=actor_role,
                action=action,
                entity="pending_link",
                entity_id=None,
                detail={
                    "count": len(entries),
                    "source": source,
                    "run_id": source_run_id,
                    "from_status": tokens(entries, "from_status"),
                    "to_status": tokens(entries, "to_status"),
                    "samples": samples,
                },
                layer=layer,
            )
        if wide_transitions:
            audit_service.record(
                db,
                actor_email=actor_email,
                actor_role=actor_role,
                action="wide.rebuild",
                entity="wide_row",
                entity_id=None,
                detail={
                    "count": len(wide_transitions),
                    "source": source,
                    "run_id": source_run_id,
                    "samples": [
                        {
                            "parent": f"{entry.get('parent_table')}/{entry.get('parent_pk')}",
                            "from": entry.get("from_status"),
                            "to": entry.get("to_status"),
                        }
                        for entry in wide_transitions[:5]
                    ],
                },
                layer=layer,
            )
    db.flush()

    after = _counts(db, children)
    after["transitions"] = len(link_transitions) + len(wide_transitions)
    after["link_transitions"] = len(link_transitions)
    after["wide_transitions"] = len(wide_transitions)
    after["batch"] = batch_counts
    after["released_delta"] = after["released"] - before["released"]
    after["pending_delta"] = after["pending"] - before["pending"]
    after["withdrawn_delta"] = after["withdrawn"] - before["withdrawn"]
    after["wide_published_delta"] = after["wide_published"] - before["wide_published"]
    after["wide_withdrawn_delta"] = after["wide_withdrawn"] - before["wide_withdrawn"]
    after["source"] = source
    after["run_id"] = source_run_id
    after["orphan_released"] = _orphan_released(db, tables, children)
    return after


# ------------------------------------------------------------------ 内部动作

def counts(db: Session) -> dict:
    children = [table for table in tables_by_id(db).values() if table.parent_table_code]
    return _counts(db, children)


def _counts(db: Session, children: list[SourceTable]) -> dict:
    links = db.execute(select(PendingLink.status, func.count()).group_by(PendingLink.status)).all()
    counts = {status.value: 0 for status in LinkStatus}
    for status, count in links:
        counts[status] = int(count)
    wide_rows = db.execute(select(WideRow.status, func.count()).group_by(WideRow.status)).all()
    wide = {status.value: 0 for status in WideRowStatus}
    for status, count in wide_rows:
        wide[status] = int(count)
    per_child: dict[str, dict] = {
        child.code: {"table_id": child.id, "pending": 0, "released": 0, "withdrawn": 0, "total": 0}
        for child in children
    }
    per_child_rows = db.execute(
        select(PendingLink.table_id, PendingLink.status, func.count()).group_by(
            PendingLink.table_id, PendingLink.status
        )
    ).all()
    child_ids = {child.id for child in children}
    for table_id, status, count in per_child_rows:
        if table_id not in child_ids:
            continue
        child = next(item for item in children if item.id == table_id)
        per_child[child.code][status] = int(count)
        per_child[child.code]["total"] += int(count)
    return {
        "pending": counts.get(LinkStatus.PENDING.value, 0),
        "released": counts.get(LinkStatus.RELEASED.value, 0),
        "withdrawn": counts.get(LinkStatus.WITHDRAWN.value, 0),
        "links_total": sum(counts.values()),
        "wide_published": wide.get(WideRowStatus.PUBLISHED.value, 0),
        "wide_withdrawn": wide.get(WideRowStatus.WITHDRAWN.value, 0),
        "wide_total": sum(wide.values()),
        "per_child": per_child,
    }


def _upsert_parent_wide(
    db: Session,
    *,
    parent: SourceTable,
    canonical: str,
    parent_row: dict,
    is_void: bool,
    status_value: str | None,
    source_run_id: int | None,
    transitions: list[dict],
    source: str,
) -> WideRow:
    target = WideRowStatus.WITHDRAWN.value if is_void else WideRowStatus.PUBLISHED.value
    wide = db.scalar(
        select(WideRow).where(
            WideRow.parent_table_code == parent.code, WideRow.parent_pk == canonical
        )
    )
    if wide is None:
        wide = WideRow(
            parent_table_code=parent.code,
            parent_pk=canonical,
            parent_table_id=parent.id,
            child_rows=[],
            parent_row=parent_row,
            status=target,
            source_run_id=source_run_id,
            link_basis="parent_void" if is_void else "no_child",
            metric={"child_count": 0, "withdrawn_children": 0},
        )
        db.add(wide)
        db.flush()
        transitions.append(
            {
                "scope": "wide_row",
                "parent_table": parent.code,
                "parent_pk": canonical,
                "reason": LinkReason.PARENT_VOID.value if is_void else "parent_created",
                "basis": f"父单 {parent.code}/{canonical} 折叠终态 "
                + _status_text(parent.parent_status_column or "status", status_value, deleted=False),
                "from_status": None,
                "to_status": target,
                "source": source,
                "run_id": source_run_id,
            }
        )
        return wide
    previous = wide.status
    if wide.parent_row != parent_row:
        wide.parent_row = parent_row
        wide.updated_at = utcnow()
    if wide.status != target:
        wide.status = target
        wide.link_basis = "parent_void" if is_void else "no_child"
        if is_void:
            wide.child_rows = []
        transitions.append(
            {
                "scope": "wide_row",
                "parent_table": parent.code,
                "parent_pk": canonical,
                "reason": LinkReason.PARENT_VOID.value if is_void else "parent_valid",
                "basis": f"父单 {parent.code}/{canonical} 状态迁移 "
                + _status_text(parent.parent_status_column or "status", status_value, deleted=False),
                "from_status": previous,
                "to_status": target,
                "source": source,
                "run_id": source_run_id,
            }
        )
    if source_run_id is not None and wide.source_run_id != source_run_id:
        wide.source_run_id = source_run_id
    if is_void:
        desired = {**(wide.metric or {}), "parent_status": status_value}
        if wide.metric != desired:
            wide.metric = desired
            wide.updated_at = utcnow()
    return wide


def _withdraw_parent_wide(
    db: Session,
    *,
    parent: SourceTable,
    canonical: str,
    source_run_id: int | None,
    transitions: list[dict],
    source: str,
) -> None:
    wide = db.scalar(
        select(WideRow).where(
            WideRow.parent_table_code == parent.code, WideRow.parent_pk == canonical
        )
    )
    if wide is None:
        # 父单在本批之前从未发布过，但本批收到删除：仍登记一行「已撤回」，
        # 让指向它的从表记录能被识别为父单作废并撤回（不留有案无单）。
        wide = WideRow(
            parent_table_code=parent.code,
            parent_pk=canonical,
            parent_table_id=parent.id,
            child_rows=[],
            parent_row={},
            status=WideRowStatus.WITHDRAWN.value,
            source_run_id=source_run_id,
            link_basis="parent_void",
            metric={"child_count": 0, "withdrawn_children": 0, "parent_status": "deleted"},
        )
        db.add(wide)
        db.flush()
        transitions.append(
            {
                "scope": "wide_row",
                "parent_table": parent.code,
                "parent_pk": canonical,
                "reason": LinkReason.PARENT_VOID.value,
                "basis": f"父单 {parent.code}/{canonical} 被删除 d，宽表行登记为撤回",
                "from_status": None,
                "to_status": WideRowStatus.WITHDRAWN.value,
                "source": source,
                "run_id": source_run_id,
            }
        )
        return
    previous = wide.status
    if previous != WideRowStatus.WITHDRAWN.value:
        wide.status = WideRowStatus.WITHDRAWN.value
        wide.child_rows = []
        wide.link_basis = "parent_void"
        wide.metric = {**(wide.metric or {}), "parent_status": "deleted"}
        wide.updated_at = utcnow()
        transitions.append(
            {
                "scope": "wide_row",
                "parent_table": parent.code,
                "parent_pk": canonical,
                "reason": LinkReason.PARENT_VOID.value,
                "basis": f"父单 {parent.code}/{canonical} 被删除 d，宽表行撤回并重算",
                "from_status": previous,
                "to_status": WideRowStatus.WITHDRAWN.value,
                "source": source,
                "run_id": source_run_id,
            }
        )
    if source_run_id is not None and wide.source_run_id != source_run_id:
        wide.source_run_id = source_run_id


def _alias_map(db: Session, parents: list[SourceTable]) -> dict[str, dict]:
    parent_by_code = {parent.code: parent for parent in parents}
    alias_map: dict[str, dict] = {}
    for wide in db.scalars(select(WideRow)).all():
        parent = parent_by_code.get(wide.parent_table_code)
        if parent is None:
            continue
        column = parent.parent_status_column or "status"
        value = (wide.parent_row or {}).get(column)
        snapshot = {
            "parent_code": wide.parent_table_code,
            "parent_pk": wide.parent_pk,
            "status_column": column,
            "status_value": value,
            "status": wide.status,
        }
        entry = {
            "parent_code": wide.parent_table_code,
            "canonical": wide.parent_pk,
            "valid": wide.status == WideRowStatus.PUBLISHED.value,
            "snapshot": snapshot,
        }
        for alias in _aliases(parent, wide.parent_pk, wide.parent_row or {}):
            alias_map.setdefault(alias, entry)
    return alias_map


def _resolve(alias_map: dict[str, dict], raw: object) -> dict | None:
    if raw is None or str(raw) == "":
        return None
    return alias_map.get(str(raw))


def tokens(entries: list[dict], key: str) -> list[str]:
    """把一批迁移里的某个字段去重成有序元组，审计里可核对 from -> to 分布。"""
    return sorted({str(entry.get(key)) for entry in entries if entry.get(key) is not None})


def _transition(
    db: Session,
    *,
    child: SourceTable,
    pk: str,
    op: str,
    payload: dict,
    parent_raw: str | None,
    target: str,
    reason: str,
    basis: str,
    snapshot: dict | None,
    source_run_id: int | None,
    transitions: list[dict],
    source: str,
    link: PendingLink | None = None,
) -> PendingLink:
    if link is None:
        link = db.scalar(
            select(PendingLink).where(PendingLink.table_id == child.id, PendingLink.pk_value == pk)
        )
    if link is None:
        link = PendingLink(
            table_id=child.id,
            pk_value=pk,
            event_lsn=0,
            op=op,
            parent_table_code=child.parent_table_code,
            parent_table_id=_table_id_by_code(db, child.parent_table_code),
            parent_key_column=child.parent_key_column,
            status=LinkStatus.PENDING.value,
            reason=LinkReason.PARENT_MISSING.value,
            payload=payload or {},
            parent_snapshot={},
            first_seen_run_id=source_run_id,
        )
        db.add(link)
        db.flush()
        previous = None
    else:
        previous = link.status

    # 只在字段真的变化时写库 + 刷 updated_at，重复执行同一批不产生任何 UPDATE。
    changed = False

    def _set(field: str, value) -> None:
        nonlocal changed
        if getattr(link, field) != value:
            setattr(link, field, value)
            changed = True

    _set("parent_table_code", child.parent_table_code)
    _set("parent_table_id", _table_id_by_code(db, child.parent_table_code))
    _set("parent_key_column", child.parent_key_column)
    _set("parent_pk", parent_raw)
    if payload:
        _set("payload", payload)
    if snapshot is not None:
        _set("parent_snapshot", snapshot)
    _set("op", op)
    _set("reason", reason)
    _set("status", target)

    if target == LinkStatus.RELEASED.value:
        if link.released_at is None:
            link.released_at = utcnow()
            changed = True
        _set("withdrawn_at", None)
        _set("withdraw_basis", None)
        _set("release_basis", basis)
        if source_run_id is not None:
            _set("released_run_id", source_run_id)
    elif target == LinkStatus.WITHDRAWN.value:
        if link.withdrawn_at is None:
            link.withdrawn_at = utcnow()
            changed = True
        _set("withdraw_basis", basis)
    else:
        _set("released_at", None)
        _set("released_run_id", None)
        _set("release_basis", None)
        _set("withdrawn_at", None)
        _set("withdraw_basis", None)

    if changed:
        link.updated_at = utcnow()

    if previous != target:
        transitions.append(
            {
                "scope": "link",
                "link_id": link.id,
                "action": LINK_ACTION_BY_STATUS.get(target, "link.transition"),
                "child_table": child.code,
                "pk_value": pk,
                "parent_table": child.parent_table_code,
                "parent_key": parent_raw,
                "reason": reason,
                "basis": basis,
                "from_status": previous,
                "to_status": target,
                "source": source,
                "run_id": source_run_id,
            }
        )
    return link


def _table_id_by_code(db: Session, code: str | None) -> int | None:
    if not code:
        return None
    table = db.scalar(select(SourceTable).where(SourceTable.code == code))
    return table.id if table else None


def _rebuild_wide_children(
    db: Session, tables: dict[int, SourceTable], parents: list[SourceTable], children: list[SourceTable]
) -> None:
    parent_by_code = {parent.code: parent for parent in parents}
    db.flush()
    released = db.scalars(select(PendingLink).where(PendingLink.status == LinkStatus.RELEASED.value)).all()
    withdrawn = db.scalars(
        select(PendingLink).where(
            PendingLink.status == LinkStatus.WITHDRAWN.value,
            PendingLink.parent_table_code.is_not(None),
        )
    ).all()
    grouped: dict[tuple[str | None, str], list[PendingLink]] = defaultdict(list)
    for link in released:
        canonical = (link.parent_snapshot or {}).get("parent_pk")
        if canonical:
            grouped[(link.parent_table_code, str(canonical))].append(link)
    withdrawn_counts: dict[tuple[str | None, str], int] = defaultdict(int)
    for link in withdrawn:
        canonical = (link.parent_snapshot or {}).get("parent_pk")
        if canonical:
            withdrawn_counts[(link.parent_table_code, str(canonical))] += 1

    for wide in db.scalars(select(WideRow)).all():
        parent = parent_by_code.get(wide.parent_table_code)
        if parent is None:
            continue
        child_rows: list[dict] = []
        if wide.status == WideRowStatus.PUBLISHED.value:
            for link in grouped.get((wide.parent_table_code, wide.parent_pk), []):
                child = tables.get(link.table_id)
                code = child.code if child else link.parent_table_code
                payload = link.payload or {}
                child_rows.append(
                    {
                        "child_code": code,
                        "pk_value": link.pk_value,
                        "after": payload.get("after") or {},
                        "last_lsn": int(payload.get("last_lsn") or 0),
                        "last_event_time": payload.get("last_event_time"),
                        "released_run_id": link.released_run_id,
                    }
                )
        child_rows.sort(key=lambda item: (str(item["child_code"]), str(item["pk_value"])))
        metric = {
            "child_count": len(child_rows),
            "withdrawn_children": withdrawn_counts.get((wide.parent_table_code, wide.parent_pk), 0),
        }
        basis = (
            "parent_void"
            if wide.status == WideRowStatus.WITHDRAWN.value
            else ("linked" if child_rows else "no_child")
        )
        if (wide.child_rows or []) != child_rows:
            wide.child_rows = child_rows
            wide.updated_at = utcnow()
        if (wide.metric or {}) != metric:
            wide.metric = metric
            wide.updated_at = utcnow()
        if wide.link_basis != basis:
            wide.link_basis = basis
            wide.updated_at = utcnow()


def _orphan_released(db: Session, tables: dict[int, SourceTable], children: list[SourceTable]) -> int:
    """不变量：released 状态却找不到已发布父宽表的记录数，应为 0。"""
    count = 0
    for link in db.scalars(select(PendingLink).where(PendingLink.status == LinkStatus.RELEASED.value)).all():
        canonical = (link.parent_snapshot or {}).get("parent_pk")
        wide = db.scalar(
            select(WideRow).where(
                WideRow.parent_table_code == link.parent_table_code,
                WideRow.parent_pk == str(canonical) if canonical is not None else "",
            )
        )
        if wide is None or wide.status != WideRowStatus.PUBLISHED.value:
            count += 1
    return count


# ------------------------------------------------------------------ 重算入口

def recompute_from_events(
    db: Session,
    *,
    run: MergeRun | None = None,
    actor_email: str = "system",
    actor_role: str = "system",
    source: str = "reconcile",
    layer: str = "domain",
    audit: bool = True,
) -> dict:
    """不新增批次，直接用库内事件重算所有表的折叠终态并跑一次门禁（可恢复入口）。"""
    from src.services import merge_run_service, watermark_merge

    tables = tables_by_id(db)
    folded: dict[int, list[dict]] = {}
    deleted: dict[int, list[str]] = {}
    for table_id, table in tables.items():
        events = merge_run_service._event_rows(db, table_id)  # noqa: SLF001 - 同包内部复用
        if not events:
            continue
        result = watermark_merge.merge_events(
            events,
            allowed_lateness_seconds=int(table.allowed_lateness_seconds or 30),
            schema_columns=table.schema_columns or [],
        )
        folded[table_id] = result["rows"]
        deleted[table_id] = result["deleted_pks"]
    return reconcile(
        db,
        folded_by_table=folded,
        deleted_by_table=deleted,
        scope_tables=set(tables),
        run=run,
        source=source,
        layer=layer,
        audit=audit,
        actor_email=actor_email,
        actor_role=actor_role,
    )
