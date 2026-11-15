"""关联门禁集成测试：用 demo seed 的真实事件量跑完整合并批次。

覆盖：父单未到先保留、父单到达放行、父单作废撤回、重复执行同一批零新增。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from scripts.demo_config import CLAIM_COLUMNS, POLICY_COLUMNS, PREMIUM_COLUMNS, build_events
from src.models import Base
from src.models.audit import AuditLog
from src.models.cdc_event import CdcEvent
from src.models.merge_run import MergeRun
from src.models.pending_link import PendingLink
from src.models.source_table import SourceTable
from src.models.wide_row import WideRow
from src.services import link_service, merge_run_service


@pytest.fixture()
def seeded():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    specs = {
        "T_POLICY": dict(pk="policy_id", columns=POLICY_COLUMNS, parent=None, key=None, voids=["已退保"]),
        "T_CLAIM": dict(pk="claim_id", columns=CLAIM_COLUMNS, parent="T_POLICY", key="policy_id", voids=["已退保"]),
        "T_PREMIUM": dict(pk="txn_id", columns=PREMIUM_COLUMNS, parent="T_POLICY", key="policy_no", voids=["已退保"]),
    }
    for code, spec in specs.items():
        table = SourceTable(
            code=code,
            name=code,
            table_name=code.lower(),
            pk_column=spec["pk"],
            schema_columns=spec["columns"],
            allowed_lateness_seconds=30,
            parent_table_code=spec["parent"],
            parent_key_column=spec["key"],
            parent_status_column="status",
            void_statuses=spec["voids"],
        )
        db.add(table)
        db.flush()
        event_spec = _event_spec(code)
        for row in build_events(event_spec):
            db.add(CdcEvent(table_id=table.id, **row))
        db.flush()
    db.commit()
    try:
        yield db
    finally:
        db.close()


def _event_spec(code: str) -> dict:
    from scripts.demo_config import TABLES

    return next(spec for spec in TABLES if spec["code"] == code)


def test_seed_merge_holds_missing_parent_claims(seeded):
    db = seeded
    run = merge_run_service.create_and_execute(
        db, table_id=None, trigger="seed", operator_email="admin@cdcmerge.io"
    )
    db.commit()
    links = db.execute(select(PendingLink.status, func.count()).group_by(PendingLink.status)).all()
    counts = {status: count for status, count in links}
    assert counts.get("pending", 0) > 0, "应存在父单未到的理赔记录"
    assert counts.get("released", 0) > 0
    assert counts.get("withdrawn", 0) > 0, "父单已作废（已退保 / 删除）的从表记录应撤回"
    assert run.link_pending_count == counts.get("pending", 0)
    assert run.wide_published_count > 0

    # 待关联的理赔记录一定不在任何宽表 child_rows 里（有案无单）
    held = db.scalars(select(PendingLink).where(PendingLink.status == "pending")).all()
    held_keys = {(link.table_id, link.pk_value) for link in held}
    assert held_keys
    for wide in db.scalars(select(WideRow)).all():
        for child in wide.child_rows or []:
            assert (child.get("_table_id"), child["pk_value"]) not in held_keys


def test_wide_rows_only_contain_released_children(seeded):
    db = seeded
    merge_run_service.create_and_execute(db, table_id=None, trigger="seed", operator_email="admin@cdcmerge.io")
    db.commit()
    overview = link_service.overview(db)
    assert overview["checks"]["ok"] is True
    assert overview["checks"]["orphan_released"] == 0
    assert overview["wide_published"] > 0
    assert overview["children"]
    for child in overview["children"]:
        assert child["total"] == child["pending"] + child["released"] + child["withdrawn"]
    # 放行依据可核对
    released = overview["samples"]["released"]
    assert released and all(item["release_basis"] for item in released)


def test_repeat_same_batch_no_duplicates_or_audit(seeded):
    db = seeded
    first = merge_run_service.create_and_execute(
        db, table_id=None, trigger="seed", operator_email="admin@cdcmerge.io"
    )
    db.commit()
    snap = {
        "links": db.scalar(select(func.count()).select_from(PendingLink)),
        "wide": db.scalar(select(func.count()).select_from(WideRow)),
        "audit": db.scalar(select(func.count()).select_from(AuditLog)),
        "wide_children": db.scalar(select(func.sum(func.length(WideRow.child_rows)))),
    }
    second = merge_run_service.create_and_execute(
        db, table_id=None, trigger="seed", operator_email="admin@cdcmerge.io"
    )
    db.commit()
    assert second.link_pending_count == first.link_pending_count
    assert second.link_released_count == first.link_released_count
    assert db.scalar(select(func.count()).select_from(PendingLink)) == snap["links"]
    assert db.scalar(select(func.count()).select_from(WideRow)) == snap["wide"]
    assert db.scalar(select(func.count()).select_from(AuditLog)) == snap["audit"]
    assert db.scalar(select(func.sum(func.length(WideRow.child_rows)))) == snap["wide_children"]


def test_recompute_endpoint_is_idempotent(seeded):
    db = seeded
    merge_run_service.create_and_execute(db, table_id=None, trigger="seed", operator_email="admin@cdcmerge.io")
    db.commit()
    first = link_service.recompute(db, principal={"email": "analyst@cdcmerge.io", "role": "analyst"})
    db.commit()
    assert first["summary"]["transitions"] == 0
    before = {
        "links": db.scalar(select(func.count()).select_from(PendingLink)),
        "wide": db.scalar(select(func.count()).select_from(WideRow)),
        "runs": db.scalar(select(func.count()).select_from(MergeRun)),
    }
    second = link_service.recompute(db, principal={"email": "analyst@cdcmerge.io", "role": "analyst"})
    db.commit()
    assert second["summary"]["transitions"] == 0
    assert db.scalar(select(func.count()).select_from(PendingLink)) == before["links"]
    assert db.scalar(select(func.count()).select_from(WideRow)) == before["wide"]
    assert db.scalar(select(func.count()).select_from(MergeRun)) == before["runs"]


def test_missing_parent_claim_released_when_policy_arrives(seeded):
    """先跑全库（父单缺失理赔被保留），再补一条父保单事件，重算后自动放行。"""
    from datetime import datetime

    db = seeded
    merge_run_service.create_and_execute(db, table_id=None, trigger="seed", operator_email="admin@cdcmerge.io")
    db.commit()
    policy = db.scalar(select(SourceTable).where(SourceTable.code == "T_POLICY"))
    held = db.scalar(
        select(PendingLink).where(
            PendingLink.status == "pending", PendingLink.parent_table_code == "T_POLICY"
        )
    )
    assert held is not None
    db.add(
        CdcEvent(
            table_id=policy.id,
            pk_value=f"POL-{held.parent_pk}",
            lsn=999001,
            seq=9999,
            op="c",
            event_time=datetime(2026, 11, 14, 2, 0, 10),
            ingest_at=datetime(2026, 11, 14, 2, 0, 30),
            before_data={},
            after_data={"policy_id": int(held.parent_pk), "status": "已承保"},
        )
    )
    db.commit()
    summary = link_service.recompute(db, principal={"email": "analyst@cdcmerge.io", "role": "analyst"})
    db.commit()
    assert summary["summary"]["released_delta"] >= 1
    link = db.get(PendingLink, held.id)
    assert link.status == "released"
    assert "关联放行" in (link.release_basis or "")
