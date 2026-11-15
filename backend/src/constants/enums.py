"""共享枚举（后端唯一定义处）。前端副本见 frontend/site/js/config.js。

README「共享枚举出现位置」一节列出全部出现点，两侧需同步改动。
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class CdcOp(StrEnum):
    """binlog 行操作：插入 / 更新 / 删除。"""

    CREATE = "c"
    UPDATE = "u"
    DELETE = "d"


CDC_OP_LABELS: dict[str, str] = {
    CdcOp.CREATE.value: "插入 c",
    CdcOp.UPDATE.value: "更新 u",
    CdcOp.DELETE.value: "删除 d",
}

CDC_OPS: tuple[str, ...] = (CdcOp.CREATE.value, CdcOp.UPDATE.value, CdcOp.DELETE.value)


class EventStatus(StrEnum):
    """单条事件在接入侧的处置结果。"""

    APPLIED = "applied"
    LATE = "late"
    REJECTED = "rejected"


EVENT_STATUS_LABELS: dict[str, str] = {
    EventStatus.APPLIED.value: "已应用",
    EventStatus.LATE.value: "迟到",
    EventStatus.REJECTED.value: "被拒",
}

EVENT_STATUS_ORDER: list[str] = [
    EventStatus.APPLIED.value,
    EventStatus.LATE.value,
    EventStatus.REJECTED.value,
]


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


RUN_STATUS_LABELS: dict[str, str] = {
    RunStatus.QUEUED.value: "排队中",
    RunStatus.RUNNING.value: "执行中",
    RunStatus.SUCCESS.value: "成功",
    RunStatus.PARTIAL.value: "部分成功",
    RunStatus.FAILED.value: "失败",
}


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    SEED = "seed"


TRIGGER_LABELS: dict[str, str] = {
    TriggerKind.MANUAL.value: "手动",
    TriggerKind.SCHEDULE.value: "定时",
    TriggerKind.SEED.value: "初始化",
}


class ChangeKind(StrEnum):
    """Schema 变更类型（算法 B 的输入分类）。"""

    ADD_COLUMN = "add_column"
    ADD_NOT_NULL = "add_not_null"
    DROP_COLUMN = "drop_column"
    NARROW_TYPE = "narrow_type"
    WIDEN_TYPE = "widen_type"
    RENAME_COLUMN = "rename_column"


CHANGE_KIND_LABELS: dict[str, str] = {
    ChangeKind.ADD_COLUMN.value: "新增可空列",
    ChangeKind.ADD_NOT_NULL.value: "新增非空列",
    ChangeKind.DROP_COLUMN.value: "删除列",
    ChangeKind.NARROW_TYPE.value: "类型收窄",
    ChangeKind.WIDEN_TYPE.value: "类型放宽",
    ChangeKind.RENAME_COLUMN.value: "列重命名",
}


class Compat(StrEnum):
    """兼容性结论（算法 B 输出）。"""

    FORWARD = "forward"
    NEEDS_MAPPING = "needs_mapping"
    BREAKING = "breaking"


COMPAT_LABELS: dict[str, str] = {
    Compat.FORWARD.value: "向前兼容",
    Compat.NEEDS_MAPPING.value: "需字段映射",
    Compat.BREAKING.value: "破坏性变更",
}

COMPAT_ORDER: list[str] = [Compat.BREAKING.value, Compat.NEEDS_MAPPING.value, Compat.FORWARD.value]


class GateAction(StrEnum):
    APPLY = "apply"
    BACKFILL = "backfill"
    BLOCK = "block"


GATE_ACTION_LABELS: dict[str, str] = {
    GateAction.APPLY.value: "直接应用",
    GateAction.BACKFILL.value: "回填映射后应用",
    GateAction.BLOCK.value: "拦截并回滚",
}


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_LABELS: dict[str, str] = {
    Severity.HIGH.value: "高",
    Severity.MEDIUM.value: "中",
    Severity.LOW.value: "低",
}

SEVERITY_ORDER: list[str] = [Severity.HIGH.value, Severity.MEDIUM.value, Severity.LOW.value]


class GateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    BLOCKED = "blocked"


GATE_STATUS_LABELS: dict[str, str] = {
    GateStatus.PENDING.value: "待门禁复核",
    GateStatus.APPROVED.value: "已放行",
    GateStatus.BLOCKED.value: "已拦截",
}

GATE_STATUS_ORDER: list[str] = [
    GateStatus.PENDING.value,
    GateStatus.APPROVED.value,
    GateStatus.BLOCKED.value,
]


class RejectReason(StrEnum):
    """拒绝行判定原因（算法 A 输出）。"""

    DUPLICATE = "duplicate"
    STALE_AFTER_DELETE = "stale_after_delete"
    TYPE_MISMATCH = "type_mismatch"
    UNKNOWN_OP = "unknown_op"
    MISSING_PK = "missing_pk"


REJECT_REASON_LABELS: dict[str, str] = {
    RejectReason.DUPLICATE.value: "重复事件 (pk,lsn)",
    RejectReason.STALE_AFTER_DELETE.value: "删除后到达的旧更新",
    RejectReason.TYPE_MISMATCH.value: "列类型不匹配",
    RejectReason.UNKNOWN_OP.value: "未知操作码",
    RejectReason.MISSING_PK.value: "缺少主键",
}


class ReplayStatus(StrEnum):
    PENDING = "pending"
    REPLAYED = "replayed"
    SKIPPED = "skipped"


REPLAY_STATUS_LABELS: dict[str, str] = {
    ReplayStatus.PENDING.value: "待处理",
    ReplayStatus.REPLAYED.value: "已重放",
    ReplayStatus.SKIPPED.value: "已跳过",
}


class LinkStatus(StrEnum):
    """跨表关联门禁：从表记录相对父单的状态。"""

    PENDING = "pending"  # 父单未到，保留待关联且不发布
    RELEASED = "released"  # 父单已到，放行并发布宽表行
    WITHDRAWN = "withdrawn"  # 父单作废，撤回已发布行


LINK_STATUS_LABELS: dict[str, str] = {
    LinkStatus.PENDING.value: "待关联（未发布）",
    LinkStatus.RELEASED.value: "已放行（已发布）",
    LinkStatus.WITHDRAWN.value: "已撤回（父单作废）",
}

LINK_STATUS_ORDER: list[str] = [
    LinkStatus.PENDING.value,
    LinkStatus.RELEASED.value,
    LinkStatus.WITHDRAWN.value,
]


class LinkReason(StrEnum):
    PARENT_MISSING = "parent_missing"
    PARENT_VOID = "parent_void"
    PARENT_LINKED = "parent_linked"
    CHILD_DELETED = "child_deleted"


LINK_REASON_LABELS: dict[str, str] = {
    LinkReason.PARENT_MISSING.value: "父单未到达",
    LinkReason.PARENT_VOID.value: "父单已作废",
    LinkReason.PARENT_LINKED.value: "父单有效，可关联",
    LinkReason.CHILD_DELETED.value: "从表已删除",
}


class WideRowStatus(StrEnum):
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


WIDE_ROW_STATUS_LABELS: dict[str, str] = {
    WideRowStatus.PUBLISHED.value: "已发布",
    WideRowStatus.WITHDRAWN.value: "已撤回",
}
