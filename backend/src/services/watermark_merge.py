"""算法 A：按水位线（watermark）做 CDC 乱序合并。

口径
----
输入：一批 CDC 事件（同一源表或全库），每条含
  pk_value / lsn / op(c|u|d) / event_time / ingest_at / before / after
水位：watermark = max(ingest_at) - allowed_lateness_seconds；
  event_time < watermark 的事件计入 late（迟到），仍参与合并但被标记。
去重：到达顺序下重复出现的 (pk, lsn) 只保留首次，其余计入 dup_skipped 并出拒绝行。
折叠：同 pk 按 (event_time, lsn) 升序折叠；c/u/d 依次应用，u 按字段合并，d 清空状态；
  若某条 u 的事件时间早于一条「更早到达」的 d，则该 u 判为 stale_after_delete 丢弃。
类型：after 值的字面类型与订阅 schema 不符 -> type_mismatch 拒绝。
输出：upsert_rows / deletes / late_count / dup_skipped / rejected / watermark / rejects / rows。

纯函数、无 IO、无随机：同样输入两次结果完全一致。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from src.constants.enums import CdcOp, RejectReason

INTEGER_TYPES = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}
NUMERIC_TYPES = INTEGER_TYPES | {"decimal", "numeric", "float", "double"}
TEXT_TYPES = {"char", "varchar", "text", "mediumtext", "longtext", "json", "enum"}
TIME_TYPES = {"datetime", "timestamp", "date", "time"}


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "").replace("T", " ")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def base_type(column: dict | str) -> str:
    raw = column if isinstance(column, str) else (column or {}).get("type", "")
    text = str(raw or "").strip().lower()
    for marker in ("(", " "):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text


def _type_family(column: dict | str) -> str:
    name = base_type(column)
    if name in INTEGER_TYPES:
        return "int"
    if name in {"decimal", "numeric", "float", "double"}:
        return "decimal"
    if name in TEXT_TYPES:
        return "text"
    if name in TIME_TYPES:
        return "time"
    return "other"


def _value_matches(column_type: str, value: Any) -> bool:
    """宽松类型校验：只拦明显穿帮的值（例如 int 列出现任意字符串）。"""
    if value is None:
        return True
    family = _type_family(column_type)
    if family == "int":
        if isinstance(value, bool):
            return True
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            return value.is_integer()
        if isinstance(value, str):
            text = value.strip()
            return text.lstrip("-").isdigit()
        return False
    if family == "decimal":
        if isinstance(value, bool):
            return True
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value)
                return True
            except ValueError:
                return False
        return False
    if family == "time":
        return _as_dt(value) is not None
    if family == "text":
        return isinstance(value, (str, int, float))
    return True


def _reject(event: dict, reason: str, detail: dict | None = None) -> dict:
    return {
        "pk_value": str(event.get("pk_value") or ""),
        "op": str(event.get("op") or "")[:4],
        "reason": reason,
        "event_lsn": int(event.get("lsn") or 0),
        "event_time": _as_dt(event.get("event_time")),
        "detail": detail or {},
    }


def _stale_delete_mark(event: dict, delete_marks: list[tuple]) -> tuple | None:
    """判断一条 u 是否「删除之后才到达的旧更新」。

    条件：存在同 pk 的 d，其 (event_time, lsn) 更大（删除更新），
    但它的 ingest_at 更早（先到达）。这种 u 一旦补写就会把已删除的行复活。
    """
    event_time = _as_dt(event.get("event_time"))
    ingest_at = _as_dt(event.get("ingest_at"))
    lsn = int(event.get("lsn") or 0)
    if event_time is None or ingest_at is None:
        return None
    for del_lsn, del_time, del_ingest in delete_marks:
        if del_time is None or del_ingest is None:
            continue
        if (del_time, del_lsn) > (event_time, lsn) and del_ingest < ingest_at:
            return (del_lsn, del_time)
    return None



def merge_events(
    events: Iterable[dict],
    *,
    allowed_lateness_seconds: int = 30,
    schema_columns: list[dict] | None = None,
    watermark: datetime | None = None,
) -> dict:
    """执行一次乱序合并，返回确定性结果字典。"""
    items = list(events)
    schema = {str(col.get("name")): col for col in (schema_columns or []) if col.get("name")}
    lateness = max(0, int(allowed_lateness_seconds or 0))

    from datetime import timedelta

    ingest_times = [dt for dt in (_as_dt(e.get("ingest_at")) for e in items) if dt is not None]
    if watermark is None:
        basis = max(ingest_times) if ingest_times else None
        watermark = basis - timedelta(seconds=lateness) if basis is not None else None
    else:
        watermark = _as_dt(watermark)

    ordered = sorted(
        items,
        key=lambda e: (
            _as_dt(e.get("ingest_at")) or datetime.min,
            int(e.get("lsn") or 0),
            str(e.get("pk_value") or ""),
        ),
    )

    late_count = 0
    late_seconds_total = 0
    seen: set[tuple[str, int]] = set()
    rejects: list[dict] = []
    dup_skipped = 0
    usable: list[dict] = []

    for event in ordered:
        pk = str(event.get("pk_value") or "")
        lsn = int(event.get("lsn") or 0)
        event_time = _as_dt(event.get("event_time"))
        if not pk:
            rejects.append(_reject(event, RejectReason.MISSING_PK.value))
            continue
        if event_time is not None and watermark is not None and event_time < watermark:
            late_count += 1
            late_seconds_total += int((watermark - event_time).total_seconds())
        key = (pk, lsn)
        if key in seen:
            dup_skipped += 1
            rejects.append(_reject(event, RejectReason.DUPLICATE.value, {"first_seen": True}))
            continue
        seen.add(key)
        if str(event.get("op")) not in {op.value for op in CdcOp}:
            rejects.append(_reject(event, RejectReason.UNKNOWN_OP.value, {"op": event.get("op")}))
            continue
        usable.append(event)

    # 按 pk 分组
    grouped: dict[str, list[dict]] = {}
    for event in usable:
        grouped.setdefault(str(event.get("pk_value")), []).append(event)

    rows: list[dict] = []
    deleted = 0
    applied_ops = {"c": 0, "u": 0, "d": 0}
    stale_rejects = 0

    for pk in sorted(grouped):
        timeline = sorted(
            grouped[pk],
            key=lambda e: (_as_dt(e.get("event_time")) or datetime.min, int(e.get("lsn") or 0)),
        )
        # 先算该 pk 上最晚的一条删除（事件时间维度）
        delete_marks = [
            (int(e.get("lsn") or 0), _as_dt(e.get("event_time")), _as_dt(e.get("ingest_at")))
            for e in timeline
            if str(e.get("op")) == CdcOp.DELETE.value
        ]

        state: dict | None = None
        last_key: tuple | None = None
        for event in timeline:
            op = str(event.get("op"))
            event_time = _as_dt(event.get("event_time"))
            lsn = int(event.get("lsn") or 0)
            after = event.get("after") or {}

            if op == CdcOp.UPDATE.value:
                stale = _stale_delete_mark(event, delete_marks)
                if stale is not None:
                    rejects.append(
                        _reject(
                            event,
                            RejectReason.STALE_AFTER_DELETE.value,
                            {"delete_lsn": stale[0], "delete_event_time": stale[1].isoformat() if stale[1] else None},
                        )
                    )
                    stale_rejects += 1
                    continue

            mismatch = None
            if op in {CdcOp.CREATE.value, CdcOp.UPDATE.value} and schema:
                for field, value in after.items():
                    column = schema.get(field)
                    if column is None:
                        continue
                    if not _value_matches(str(column.get("type") or ""), value):
                        mismatch = {"column": field, "value": value, "expected": column.get("type")}
                        break
            if mismatch is not None:
                rejects.append(_reject(event, RejectReason.TYPE_MISMATCH.value, mismatch))
                continue

            if op == CdcOp.DELETE.value:
                state = None
                applied_ops["d"] += 1
                last_key = (event_time, lsn)
            else:
                if state is None:
                    state = {}
                for field, value in after.items():
                    state[field] = value
                applied_ops[op] += 1
                last_key = (event_time, lsn)

        if state is None:
            deleted += 1
        else:
            rows.append(
                {
                    "pk_value": pk,
                    "after": {k: state[k] for k in sorted(state)},
                    "last_lsn": last_key[1] if last_key else 0,
                    "last_event_time": last_key[0] if last_key else None,
                }
            )

    rejects.sort(key=lambda item: (item["pk_value"], item["event_lsn"], item["reason"]))
    late_ratio = round(late_count / len(items), 4) if items else 0.0
    metrics = {
        "events_in": len(items),
        "upsert_rows": len(rows),
        "deletes": deleted,
        "late_count": late_count,
        "late_ratio": late_ratio,
        "late_seconds_avg": round(late_seconds_total / late_count, 1) if late_count else 0.0,
        "dup_skipped": dup_skipped,
        "rejected": len(rejects),
        "stale_after_delete": stale_rejects,
        "ops": applied_ops,
        "pks": len(grouped),
        "watermark": watermark.isoformat() if watermark else None,
        "allowed_lateness_seconds": lateness,
    }
    log = (
        f"读取 {metrics['events_in']} 条事件，水位 {metrics['watermark'] or '—'}（允许迟到 {lateness}s）；"
        f"迟到 {late_count} 条、重复去重 {dup_skipped} 条、拒绝 {metrics['rejected']} 条；"
        f"折叠出 {metrics['upsert_rows']} 行 upsert、{metrics['deletes']} 个删除终态"
    )
    return {
        "rows": rows,
        "rejects": rejects,
        "metrics": metrics,
        "watermark": watermark,
        "late_count": late_count,
        "dup_skipped": dup_skipped,
        "rejected": len(rejects),
        "upsert_rows": len(rows),
        "deletes": deleted,
        "log": log,
        "status": "success" if items else "partial",
    }
