"""算法 B（Schema 兼容判定）单测：正常 / 边界 / 确定性。"""
from __future__ import annotations

from src.services.schema_gate import evaluate_schema_change

BASE = [
    {"name": "claim_id", "type": "bigint", "nullable": False},
    {"name": "claim_no", "type": "varchar(32)", "nullable": True},
    {"name": "claim_amount", "type": "bigint", "nullable": True},
]


def test_add_nullable_column_forward():
    after = BASE + [{"name": "channel", "type": "varchar(16)", "nullable": True}]
    result = evaluate_schema_change(BASE, after, table_name="t_claim")
    assert result["compat"] == "forward"
    assert result["action"] == "apply"
    assert result["severity"] == "low"


def test_add_not_null_without_default_breaking():
    after = BASE + [{"name": "channel", "type": "varchar(16)", "nullable": False}]
    result = evaluate_schema_change(BASE, after, table_name="t_claim")
    assert result["compat"] == "breaking"
    assert result["action"] == "block"
    assert result["change_kind"] == "add_not_null"


def test_bigint_to_int_is_breaking_with_rollback():
    """验收：bigint -> int 判 breaking，并给出可执行回滚 SQL。"""
    after = [
        {**col, "type": "int"} if col["name"] == "claim_amount" else col for col in BASE
    ]
    result = evaluate_schema_change(BASE, after, table_name="t_claim")
    assert result["compat"] == "breaking"
    assert result["action"] == "block"
    assert result["change_kind"] == "narrow_type"
    assert result["column_name"] == "claim_amount"
    assert "MODIFY COLUMN" in result["rollback_sql"]
    assert "bigint" in result["rollback_sql"]


def test_drop_column_breaking():
    after = [col for col in BASE if col["name"] != "claim_no"]
    result = evaluate_schema_change(BASE, after, table_name="t_claim")
    assert result["compat"] == "breaking"
    assert "ADD COLUMN" in result["rollback_sql"]
    assert "`claim_no`" in result["rollback_sql"]


def test_rename_needs_mapping():
    before = BASE
    after = [
        {**col, "name": "claim_amount_total"} if col["name"] == "claim_amount" else col for col in BASE
    ]
    result = evaluate_schema_change(before, after, table_name="t_claim")
    assert result["compat"] == "needs_mapping"
    assert result["action"] == "backfill"
    assert result["change_kind"] == "rename_column"
    assert result["target_column"] == "claim_amount_total"
    assert "CHANGE COLUMN" in result["rollback_sql"]


def test_widen_varchar_and_int_forward():
    before = [
        {"name": "id", "type": "bigint", "nullable": False},
        {"name": "name", "type": "varchar(64)", "nullable": True},
    ]
    after = [
        {"name": "id", "type": "bigint", "nullable": False},
        {"name": "name", "type": "varchar(128)", "nullable": True},
    ]
    result = evaluate_schema_change(before, after, table_name="t_policy")
    assert result["compat"] == "forward"
    assert result["action"] == "apply"
    assert result["rollback_sql"].startswith("--")


def test_varchar_shrink_is_breaking():
    before = [{"name": "policy_no", "type": "varchar(32)", "nullable": False}]
    after = [{"name": "policy_no", "type": "varchar(16)", "nullable": False}]
    result = evaluate_schema_change(before, after, table_name="t_policy")
    assert result["compat"] == "breaking"
    assert result["change_kind"] == "narrow_type"


def test_no_change_is_forward_noop():
    result = evaluate_schema_change(BASE, [dict(col) for col in BASE], table_name="t_claim")
    assert result["compat"] == "forward"
    assert result["counts"]["breaking"] == 0


def test_deterministic():
    after = [
        {**col, "type": "int"} if col["name"] == "claim_amount" else col for col in BASE
    ] + [{"name": "channel", "type": "varchar(16)", "nullable": False}]
    first = evaluate_schema_change(BASE, after, table_name="t_claim")
    second = evaluate_schema_change(BASE, [dict(col) for col in after], table_name="t_claim")
    assert first == second
