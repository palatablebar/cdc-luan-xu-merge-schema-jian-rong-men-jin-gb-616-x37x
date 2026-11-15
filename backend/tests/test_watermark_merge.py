"""算法 A（按水位线乱序合并）单测：正常 / 边界 / 确定性。"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.services.watermark_merge import merge_events

BASE = datetime(2026, 11, 14, 2, 0, 0)
LATE = BASE - timedelta(hours=6)


def event(pk: str, lsn: int, op: str, event_time: datetime, ingest_at: datetime, after: dict | None = None) -> dict:
    return {
        "pk_value": pk,
        "lsn": lsn,
        "op": op,
        "event_time": event_time,
        "ingest_at": ingest_at,
        "before": {},
        "after": after or {},
    }


def test_out_of_order_update_keeps_latest_event_time():
    """验收：u(10:00, 已承保) 与 u(10:05, 待核保) 乱序到达，终态取 10:05。"""
    events = [
        event("POL-1", 2, "u", BASE + timedelta(minutes=5), BASE + timedelta(seconds=10), {"status": "待核保"}),
        event("POL-1", 1, "u", BASE, BASE + timedelta(seconds=20), {"status": "已承保"}),
        event("POL-1", 0, "c", BASE - timedelta(minutes=1), BASE, {"status": "待核保"}),
    ]
    result = merge_events(events, allowed_lateness_seconds=30)
    rows = {row["pk_value"]: row for row in result["rows"]}
    assert rows["POL-1"]["after"]["status"] == "待核保"
    assert result["metrics"]["watermark"] is not None


def test_late_events_are_counted_but_still_folded():
    events = [
        event("POL-2", 0, "c", BASE, BASE, {"status": "待核保"}),
        event("POL-2", 1, "u", LATE, BASE + timedelta(seconds=5), {"status": "已承保"}),
    ]
    result = merge_events(events, allowed_lateness_seconds=30)
    assert result["metrics"]["late_count"] == 1
    assert result["metrics"]["late_seconds_avg"] == 21575.0  # watermark = max(ingest)-30s
    assert len(result["rows"]) == 1


def test_duplicate_pk_lsn_is_deduped_once():
    events = [
        event("POL-3", 7, "u", BASE, BASE, {"status": "已批改"}),
        event("POL-3", 7, "u", BASE, BASE + timedelta(seconds=1), {"status": "已批改"}),
    ]
    result = merge_events(events, allowed_lateness_seconds=30)
    assert result["metrics"]["dup_skipped"] == 1
    assert result["metrics"]["rejected"] == 1
    assert result["rejects"][0]["reason"] == "duplicate"


def test_delete_wins_and_older_upsert_is_rejected():
    """删除先到、旧更新后到：旧 u 必须拒绝，且该 pk 终态是删除。"""
    events = [
        event("POL-4", 0, "c", BASE, BASE, {"status": "待核保"}),
        event("POL-4", 2, "d", BASE + timedelta(minutes=5), BASE + timedelta(seconds=27)),
        event("POL-4", 1, "u", BASE + timedelta(minutes=1), BASE + timedelta(seconds=29), {"status": "已承保"}),
    ]
    result = merge_events(events, allowed_lateness_seconds=30)
    assert result["metrics"]["deletes"] == 1
    assert result["upsert_rows"] == 0
    assert result["metrics"]["stale_after_delete"] == 1
    assert [item["reason"] for item in result["rejects"]] == ["stale_after_delete"]


def test_type_mismatch_is_rejected():
    events = [
        event("CLM-1", 1, "c", BASE, BASE, {"claim_amount": "N/A"}),
    ]
    schema = [{"name": "claim_amount", "type": "bigint", "nullable": True}]
    result = merge_events(events, allowed_lateness_seconds=30, schema_columns=schema)
    assert result["metrics"]["rejected"] == 1
    assert result["rejects"][0]["reason"] == "type_mismatch"
    assert result["rejects"][0]["detail"]["column"] == "claim_amount"


def test_unknown_op_and_empty_input():
    result = merge_events([], allowed_lateness_seconds=30)
    assert result["status"] == "partial"
    assert result["metrics"]["events_in"] == 0
    assert result["rows"] == []

    bad = merge_events([event("X-1", 1, "x", BASE, BASE, {})], allowed_lateness_seconds=30)
    assert bad["rejects"][0]["reason"] == "unknown_op"


def test_deterministic_two_runs_identical():
    events = [
        event("A", 1, "c", BASE, BASE, {"v": 1}),
        event("A", 2, "u", BASE + timedelta(seconds=2), BASE + timedelta(seconds=3), {"v": 2}),
        event("B", 1, "c", BASE, BASE + timedelta(seconds=1), {"v": 9}),
    ]
    first = merge_events(events, allowed_lateness_seconds=30)
    second = merge_events(list(reversed(events)), allowed_lateness_seconds=30)
    assert first["metrics"] == second["metrics"]
    assert first["rows"] == second["rows"]
    assert first["rejects"] == second["rejects"]


def test_lateness_boundary():
    """event_time 恰好等于 watermark 不算迟到；早 1 秒就算。"""
    events = [
        event("A", 0, "c", BASE, BASE + timedelta(seconds=30), {"v": 1}),
        event("B", 0, "c", BASE - timedelta(seconds=1), BASE + timedelta(seconds=30), {"v": 2}),
    ]
    result = merge_events(events, allowed_lateness_seconds=30)
    assert result["metrics"]["late_count"] == 1
