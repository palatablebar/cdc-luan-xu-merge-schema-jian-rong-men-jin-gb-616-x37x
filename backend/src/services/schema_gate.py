"""算法 B：Schema 兼容性判定（CDC 上游 DDL 门禁）。

口径
----
输入：变更前 / 变更后的列定义列表 [{"name","type","nullable","default"}]，
      可选的 rename_hints {旧列名: 新列名}。
判定：
  新增可空列 / 新增有默认值列      -> forward       severity=low      action=apply
  新增非空且无默认值                -> breaking      severity=high     action=block
  删除列（未命中重命名）            -> breaking      severity=high     action=block
  类型收窄（bigint->int、varchar(32)->varchar(16)、decimal 精度下降、datetime->date）
                                    -> breaking      severity=high     action=block
  类型放宽（int->bigint、varchar 增长、date->datetime）
                                    -> forward       severity=low      action=apply
  重命名（同位置 + 同类型，或显式映射）
                                    -> needs_mapping severity=medium   action=backfill
  无法判定的类型替换                -> needs_mapping severity=medium   action=backfill
输出：compat / severity / action / change_kind / findings / rollback_sql / summary。
纯函数、无 IO、无随机：列顺序按输入名排序后处理，同样输入两次结果完全一致。
"""
from __future__ import annotations

import re
from typing import Any

from src.constants.enums import ChangeKind, Compat, GateAction, Severity

INT_RANK = {"tinyint": 1, "smallint": 2, "mediumint": 3, "int": 4, "integer": 4, "bigint": 5}
NUMERIC_FLOAT = {"float": 1, "double": 2}
TIME_RANK = {"time": 1, "date": 1, "datetime": 2, "timestamp": 2}
TEXT_RANK = {"char": 1, "varchar": 2, "text": 3, "mediumtext": 3, "longtext": 3}

COMPAT_RANK = {Compat.BREAKING.value: 0, Compat.NEEDS_MAPPING.value: 1, Compat.FORWARD.value: 2}
COMPAT_SEVERITY = {
    Compat.BREAKING.value: Severity.HIGH.value,
    Compat.NEEDS_MAPPING.value: Severity.MEDIUM.value,
    Compat.FORWARD.value: Severity.LOW.value,
}
COMPAT_ACTION = {
    Compat.BREAKING.value: GateAction.BLOCK.value,
    Compat.NEEDS_MAPPING.value: GateAction.BACKFILL.value,
    Compat.FORWARD.value: GateAction.APPLY.value,
}


def parse_type(raw: Any) -> dict:
    text = str(raw or "").strip().lower()
    match = re.match(r"^([a-z]+)\s*(?:\(([^)]*)\))?", text)
    base = match.group(1) if match else text
    args = match.group(2) if match else None
    length = precision = scale = None
    if args:
        parts = [part.strip() for part in args.split(",") if part.strip()]
        if len(parts) == 1 and parts[0].isdigit():
            length = int(parts[0])
        elif len(parts) == 2 and all(part.isdigit() for part in parts):
            precision, scale = int(parts[0]), int(parts[1])
    unsigned = "unsigned" in text
    return {
        "base": base,
        "length": length,
        "precision": precision,
        "scale": scale,
        "unsigned": unsigned,
        "raw": str(raw or "").strip(),
    }


def _family(base: str) -> str:
    if base in INT_RANK:
        return "int"
    if base in NUMERIC_FLOAT or base in {"decimal", "numeric"}:
        return "decimal"
    if base in TEXT_RANK or base in {"json", "blob"}:
        return "text"
    if base in TIME_RANK:
        return "time"
    return "other"


def _default_text(column: dict) -> str:
    default = column.get("default")
    if default is None or default == "":
        return ""
    if isinstance(default, str) and default.upper() == "NULL":
        return ""
    return f" DEFAULT {default}"


def _column_ddl(column: dict) -> str:
    nullable = column.get("nullable", True)
    text = f"`{column.get('name')}` {column.get('type')}"
    text += " NULL" if nullable else " NOT NULL"
    return text + _default_text(column)


def _finding(kind: str, compat: str, column: dict | None, *, detail: dict, message: str) -> dict:
    return {
        "kind": kind,
        "compat": compat,
        "severity": COMPAT_SEVERITY[compat],
        "action": COMPAT_ACTION[compat],
        "column": (column or {}).get("name"),
        "type": (column or {}).get("type"),
        "detail": detail,
        "message": message,
    }


def _type_transition(old: dict, new: dict) -> tuple[str, str]:
    """返回 (change_kind, compat)。"""
    old_parsed, new_parsed = parse_type(old.get("type")), parse_type(new.get("type"))
    old_base, new_base = old_parsed["base"], new_parsed["base"]
    if old_base == new_base:
        if old_base in INT_RANK or old_base in NUMERIC_FLOAT:
            if old_parsed["unsigned"] != new_parsed["unsigned"] and new_parsed["unsigned"]:
                return ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value
            if old_parsed["unsigned"] != new_parsed["unsigned"]:
                return ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value
            return ChangeKind.ADD_COLUMN.value, Compat.FORWARD.value
        if old_base in {"decimal", "numeric"}:
            old_key = (old_parsed["precision"] or 0, old_parsed["scale"] or 0)
            new_key = (new_parsed["precision"] or 0, new_parsed["scale"] or 0)
            if new_key < old_key:
                return ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value
            return ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value
        if old_base in TEXT_RANK:
            old_len = old_parsed["length"] or 0
            new_len = new_parsed["length"] or 0
            if new_len and old_len and new_len < old_len:
                return ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value
            if new_len and old_len and new_len > old_len:
                return ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value
            if TEXT_RANK[new_base] > TEXT_RANK[old_base]:
                return ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value
            if TEXT_RANK[new_base] < TEXT_RANK[old_base]:
                return ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value
            return ChangeKind.ADD_COLUMN.value, Compat.FORWARD.value
        if old_base in TIME_RANK:
            if new_base == old_base:
                return ChangeKind.ADD_COLUMN.value, Compat.FORWARD.value
            if TIME_RANK[new_base] < TIME_RANK[old_base]:
                return ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value
            return ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value
        return ChangeKind.ADD_COLUMN.value, Compat.NEEDS_MAPPING.value

    old_family, new_family = _family(old_base), _family(new_base)
    if old_family == new_family == "int":
        return (
            (ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value)
            if INT_RANK[new_base] < INT_RANK[old_base]
            else (ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value)
        )
    if old_family == new_family == "decimal":
        return (ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value)
    if old_family == "decimal" and new_family == "int":
        return ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value
    if old_family == "int" and new_family == "decimal":
        return ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value
    if old_family == "time" and new_family == "time":
        return (
            (ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value)
            if TIME_RANK.get(new_base, 9) < TIME_RANK.get(old_base, 9)
            else (ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value)
        )
    if old_family == new_family == "text":
        return (
            (ChangeKind.NARROW_TYPE.value, Compat.BREAKING.value)
            if TEXT_RANK.get(new_base, 9) < TEXT_RANK.get(old_base, 9)
            else (ChangeKind.WIDEN_TYPE.value, Compat.FORWARD.value)
        )
    return ChangeKind.ADD_COLUMN.value, Compat.NEEDS_MAPPING.value


def _rollback_sql(finding: dict, table: str, before_index: dict[str, dict], after_index: dict[str, dict]) -> str:
    kind = finding["kind"]
    column = finding.get("column") or ""
    after_column = after_index.get(column) or {}
    before_column = before_index.get(column) or {}
    if kind == ChangeKind.NARROW_TYPE.value and before_column:
        return f"ALTER TABLE `{table}` MODIFY COLUMN {_column_ddl(before_column)};"
    if kind == ChangeKind.DROP_COLUMN.value and before_column:
        return f"ALTER TABLE `{table}` ADD COLUMN {_column_ddl(before_column)};"
    if kind == ChangeKind.ADD_NOT_NULL.value and after_column:
        return f"ALTER TABLE `{table}` DROP COLUMN `{column}`;"
    if kind == ChangeKind.RENAME_COLUMN.value:
        target = (finding.get("detail") or {}).get("to") or ""
        source = before_index.get(finding.get("column") or "") or {}
        return f"ALTER TABLE `{table}` CHANGE COLUMN `{target}` `{column}` {source.get('type', '')};"
    return f"-- {column or '变更'} 向前兼容，无需回滚"


def evaluate_schema_change(
    before_columns: list[dict],
    after_columns: list[dict],
    *,
    table_name: str = "ods_table",
    rename_hints: dict[str, str] | None = None,
    change_key: str | None = None,
) -> dict:
    before = [dict(col) for col in (before_columns or []) if col.get("name")]
    after = [dict(col) for col in (after_columns or []) if col.get("name")]
    before_index = {str(col["name"]): col for col in before}
    after_index = {str(col["name"]): col for col in after}
    hints = dict(rename_hints or {})

    findings: list[dict] = []
    renamed: list[dict] = []

    # 1) 重命名：显式映射优先，其次「同位置 + 同基类型」
    removed = [name for name in before_index if name not in after_index]
    added = [name for name in after_index if name not in before_index]
    used_added: set[str] = set()
    before_pos = {str(col["name"]): idx for idx, col in enumerate(before)}
    after_pos = {str(col["name"]): idx for idx, col in enumerate(after)}

    for old_name in sorted(removed):
        target = hints.get(old_name)
        if target and target in added and target not in used_added:
            used_added.add(target)
            renamed.append({"from": old_name, "to": target})
            old_parsed, new_parsed = parse_type(before_index[old_name].get("type")), parse_type(after_index[target].get("type"))
            same_type = (old_parsed["base"], old_parsed["length"], old_parsed["precision"]) == (
                new_parsed["base"],
                new_parsed["length"],
                new_parsed["precision"],
            )
            findings.append(
                _finding(
                    ChangeKind.RENAME_COLUMN.value,
                    Compat.NEEDS_MAPPING.value,
                    before_index[old_name],
                    detail={"from": old_name, "to": target, "same_type": same_type, "basis": "explicit_mapping"},
                    message=f"列 `{old_name}` 重命名为 `{target}`（显式映射），需同步下游字段映射",
                )
            )
            continue
        # 同位置启发
        position = before_pos[old_name]
        candidates = [
            name
            for name in sorted(after_index)
            if name not in used_added and after_pos[name] == position
        ]
        for candidate in candidates:
            if parse_type(after_index[candidate].get("type"))["base"] == parse_type(before_index[old_name].get("type"))["base"]:
                used_added.add(candidate)
                renamed.append({"from": old_name, "to": candidate})
                findings.append(
                    _finding(
                        ChangeKind.RENAME_COLUMN.value,
                        Compat.NEEDS_MAPPING.value,
                        before_index[old_name],
                        detail={"from": old_name, "to": candidate, "basis": "same_position_same_type"},
                        message=f"列 `{old_name}` 疑似重命名为 `{candidate}`（同位置 + 同类型），需确认字段映射",
                    )
                )
                break

    renamed_from = {item["from"] for item in renamed}
    renamed_to = {item["to"] for item in renamed}

    # 2) 剩余新增列
    for name in sorted(added):
        if name in used_added or name in renamed_to:
            continue
        column = after_index[name]
        nullable = bool(column.get("nullable", True))
        has_default = column.get("default") not in (None, "")
        if nullable or has_default:
            findings.append(
                _finding(
                    ChangeKind.ADD_COLUMN.value,
                    Compat.FORWARD.value,
                    column,
                    detail={"nullable": nullable, "has_default": has_default},
                    message=f"新增列 `{name}`（{'可空' if nullable else '有默认值'}），向下游透传即可",
                )
            )
        else:
            findings.append(
                _finding(
                    ChangeKind.ADD_NOT_NULL.value,
                    Compat.BREAKING.value,
                    column,
                    detail={"nullable": False, "has_default": False},
                    message=f"新增非空无默认列 `{name}`，存量行无法补齐，必须拦截",
                )
            )

    # 3) 剩余删除列
    for name in sorted(removed):
        if name in renamed_from:
            continue
        findings.append(
            _finding(
                ChangeKind.DROP_COLUMN.value,
                Compat.BREAKING.value,
                before_index[name],
                detail={"column": name},
                message=f"删除列 `{name}`，下游 SELECT/宽表会直接报错",
            )
        )

    # 4) 保留列的类型变化
    for name in sorted(set(before_index) & set(after_index)):
        old_column, new_column = before_index[name], after_index[name]
        old_parsed, new_parsed = parse_type(old_column.get("type")), parse_type(new_column.get("type"))
        if old_parsed["raw"].lower().replace(" ", "") == new_parsed["raw"].lower().replace(" ", ""):
            continue
        kind, compat = _type_transition(old_column, new_column)
        if compat == Compat.FORWARD.value and kind == ChangeKind.ADD_COLUMN.value:
            continue  # 无实质变化
        findings.append(
            _finding(
                kind,
                compat,
                new_column,
                detail={"from": old_parsed["raw"], "to": new_parsed["raw"]},
                message=f"列 `{name}` 类型 {old_parsed['raw']} → {new_parsed['raw']}（{kind}）",
            )
        )

    findings.sort(key=lambda item: (COMPAT_RANK[item["compat"]], str(item.get("column") or ""), item["kind"]))
    if not findings:
        findings = [
            _finding(
                ChangeKind.ADD_COLUMN.value,
                Compat.FORWARD.value,
                None,
                detail={"noop": True},
                message="列集合与类型均无变化，无需门禁动作",
            )
        ]

    primary = findings[0]
    compat = primary["compat"]
    severity = COMPAT_SEVERITY[compat]
    action = COMPAT_ACTION[compat]
    rollback = _rollback_sql(primary, table_name, before_index, after_index)
    summary = (
        f"{table_name}: {primary['message']}；判 {compat} / 动作 {action}"
        + (f"；共 {len(findings)} 项判定" if len(findings) > 1 else "")
    )
    return {
        "compat": compat,
        "severity": severity,
        "action": action,
        "change_kind": primary["kind"],
        "column_name": primary.get("column"),
        "target_column": primary.get("detail", {}).get("to") if primary["kind"] == ChangeKind.RENAME_COLUMN.value else None,
        "findings": findings,
        "renamed": renamed,
        "rollback_sql": rollback,
        "summary": summary,
        "counts": {
            "findings": len(findings),
            "breaking": sum(1 for item in findings if item["compat"] == Compat.BREAKING.value),
            "needs_mapping": sum(1 for item in findings if item["compat"] == Compat.NEEDS_MAPPING.value),
            "forward": sum(1 for item in findings if item["compat"] == Compat.FORWARD.value),
        },
        "change_key": change_key,
    }
