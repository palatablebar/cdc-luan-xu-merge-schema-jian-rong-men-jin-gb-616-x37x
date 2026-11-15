"""跨表关联门禁单测：父单未到先保留、父单到达自动放行、父单作废撤回、重复执行幂等。

用内存 SQLite 建真实 ORM 表，验证 link_gate.reconcile 的端到端行为（不是 mock）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.models import Base
from src.models.audit import AuditLog
from src.models.pending_link import PendingLink
from src.models.source_table import SourceTable
from src.models.wide_row import WideRow
from src.services import link_gate, watermark_merge

BASE = datetime(2026, 11, 14, 2, 0, 0)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _source(db: Session, code: str, *, pk: str, parent: str | None = None, key: str | None = None,
            status_column: str = "status", voids: list[str] | None = None) -> SourceTable:
    table = SourceTable(
        code=code,
        name=code,
        database_name="biz_prod",
        table_name=code.lower(),
        pk_column=pk,
        schema_columns=[],
        allowed_lateness_seconds=30,
        parent_table_code=parent,
        parent_key_column=key,
        parent_status_column=status_column,
        void_statuses=voids or [],
    )
    db.add(table)
    db.flush()
    return table


def _fold(db: Session, table: SourceTable, events: list[dict]) -> dict:
    return watermark_merge.merge_events(events, allowed_lateness_seconds=30, schema_columns=table.schema_columns or [])


def _patch_run(db: Session, parent: SourceTable, child: SourceTable, *,
               parent_events: list[dict], child_events: list[dict]) -> dict:
    p = _fold(db, parent, parent_events)
    c = _fold(db, child, child_events)
    folded = {parent.id: p["rows"], child.id: c["rows"]}
    deleted = {parent.id: p["deleted_pks"], child.id: c["deleted_pks"]}
    return link_gate.reconcile(
        db,
        folded_by_table=folded,
        deleted_by_table=deleted,
        scope_tables={parent.id, child.id},
        source="test",
    )


def _ev(pk: str, lsn: int, op: str, after: dict | None = None, *, minutes: int = 0, ingest_s: int = 0) -> dict:
    return {
        "pk_value": pk,
        "lsn": lsn,
        "op": op,
        "event_time": BASE + timedelta(minutes=minutes),
        "ingest_at": BASE + timedelta(seconds=ingest_s),
        "before": {},
        "after": after or {},
    }


@pytest.fixture()
def pair(db: Session):
    parent = _source(db, "T_POLICY", pk="policy_id", voids=["已退保"])
    child = _source(db, "T_CLAIM", pk="claim_id", parent="T_POLICY", key="policy_id")
    return db, parent, child


def test_child_arrives_before_parent_is_held_not_published(pair):
    db, parent, child = pair
    summary = _patch_run(db, parent, child, parent_events=[], child_events=[
        _ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000, "claim_amount": 500})
    ])
    assert summary["pending"] == 1
    assert summary["released"] == 0
    assert summary["wide_total"] == 0
    link = db.scalar(select(PendingLink))
    assert link.status == "pending"
    assert link.reason == "parent_missing"
    assert "尚未到达" in link.release_basis if link.release_basis else True
    assert link.release_basis is None
    assert link.payload["after"]["claim_id"] == 1


def test_parent_arrival_releases_held_child_automatically(pair):
    db, parent, child = pair
    _patch_run(db, parent, child, parent_events=[], child_events=[
        _ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000})
    ])
    summary = _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})
    ], child_events=[])
    assert summary["released"] == 1
    assert summary["pending"] == 0
    link = db.scalar(select(PendingLink))
    assert link.status == "released"
    assert "关联放行" in (link.release_basis or "")
    wide = db.scalar(select(WideRow))
    assert wide.status == "published"
    assert wide.parent_pk == "POL-100000"
    assert [row["pk_value"] for row in wide.child_rows] == ["CLM-1"]
    assert summary["wide_published"] == 1


def test_parent_void_withdraws_published_wide_row_and_child(pair):
    db, parent, child = pair
    _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})
    ], child_events=[_ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000})])
    assert db.scalar(select(WideRow)).status == "published"

    summary = _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 2, "u", {"policy_id": 100000, "status": "已退保"}, minutes=5)
    ], child_events=[])
    wide = db.scalar(select(WideRow))
    assert wide.status == "withdrawn"
    assert wide.child_rows == []
    link = db.scalar(select(PendingLink))
    assert link.status == "withdrawn"
    assert link.reason == "parent_void"
    assert "作废" in (link.withdraw_basis or "")
    assert summary["wide_withdrawn"] == 1
    assert summary["released"] == 0


def test_parent_delete_withdraws_wide_row(pair):
    db, parent, child = pair
    _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})
    ], child_events=[_ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000})])
    summary = _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 9, "d", {}, minutes=9)
    ], child_events=[])
    assert db.scalar(select(WideRow)).status == "withdrawn"
    assert db.scalar(select(PendingLink)).status == "withdrawn"
    assert summary["wide_withdrawn"] == 1


def test_repeat_same_batch_creates_no_duplicates_or_audit(pair):
    db, parent, child = pair
    parent_events = [_ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})]
    child_events = [
        _ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000}),
        _ev("CLM-2", 1, "c", {"claim_id": 2, "policy_id": 999999}),
    ]
    first = _patch_run(db, parent, child, parent_events=parent_events, child_events=child_events)
    links_after_first = db.scalar(select(func.count()).select_from(PendingLink))
    wide_after_first = db.scalar(select(func.count()).select_from(WideRow))
    audit_after_first = db.scalar(select(func.count()).select_from(AuditLog))
    assert first["transitions"] > 0

    second = _patch_run(db, parent, child, parent_events=parent_events, child_events=child_events)
    assert second["transitions"] == 0
    assert db.scalar(select(func.count()).select_from(PendingLink)) == links_after_first
    assert db.scalar(select(func.count()).select_from(WideRow)) == wide_after_first
    assert db.scalar(select(func.count()).select_from(AuditLog)) == audit_after_first


def test_child_delete_withdraws_its_link(pair):
    db, parent, child = pair
    _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})
    ], child_events=[_ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000})])
    summary = _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})
    ], child_events=[_ev("CLM-1", 5, "d", {})])
    assert summary["withdrawn"] == 1
    wide = db.scalar(select(WideRow))
    assert wide.child_rows == []
    assert wide.status == "published"  # 父单仍有效，宽表行保留、子行清空


def test_prefixed_parent_pk_matches_numeric_foreign_key(pair):
    db, parent, child = pair
    _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"})
    ], child_events=[_ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000})])
    assert db.scalar(select(PendingLink)).status == "released"


def test_business_number_foreign_key_matches_parent_pk(db: Session):
    parent = _source(db, "T_POLICY", pk="policy_id", voids=["已退保"])
    premium = _source(db, "T_PREMIUM", pk="txn_id", parent="T_POLICY", key="policy_no")
    _patch_run(db, parent, premium, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "policy_no": "POL-100000", "status": "已承保"})
    ], child_events=[_ev("TXN-1", 1, "c", {"txn_id": 1, "policy_no": "POL-100000"})])
    assert db.scalar(select(PendingLink)).status == "released"


def test_released_links_all_have_published_parent(pair):
    db, parent, child = pair
    _patch_run(db, parent, child, parent_events=[
        _ev("POL-100000", 1, "c", {"policy_id": 100000, "status": "已承保"}),
        _ev("POL-100001", 1, "c", {"policy_id": 100001, "status": "已退保"}),
    ], child_events=[
        _ev("CLM-1", 1, "c", {"claim_id": 1, "policy_id": 100000}),
        _ev("CLM-2", 1, "c", {"claim_id": 2, "policy_id": 100001}),
    ])
    released = db.scalars(select(PendingLink).where(PendingLink.status == "released")).all()
    published = {
        (wide.parent_table_code, wide.parent_pk)
        for wide in db.scalars(select(WideRow).where(WideRow.status == "published"))
    }
    assert all((link.parent_table_code, link.parent_snapshot["parent_pk"]) in published for link in released)
    withdrawn = db.scalar(select(PendingLink).where(PendingLink.reason == "parent_void"))
    assert withdrawn.status == "withdrawn"
