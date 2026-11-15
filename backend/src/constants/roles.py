"""角色 -> 权限点映射。README 与前端 site/js/config.js 同步。

角色键固定为 admin / analyst / viewer，各项目的展示名与职责不同。
"""
from __future__ import annotations

from src.constants.enums import Role

ROLE_LABELS: dict[str, str] = {
    Role.ADMIN.value: "数据集成管理员",
    Role.ANALYST.value: "同步运维专员",
    Role.VIEWER.value: "业务只读",
}

ROLE_DUTIES: dict[str, str] = {
    Role.ADMIN.value: "维护源表订阅与水位线、跑合并、批 Schema 门禁、导出与审计",
    Role.ANALYST.value: "巡检事件流与拒绝行、重放、导出报表",
    Role.VIEWER.value: "只读查看事件、合并结果与门禁结论",
}

PERMISSIONS: dict[str, set[str]] = {
    Role.ADMIN.value: {
        "table:read", "table:write",
        "event:read", "event:ingest",
        "run:read", "run:create",
        "reject:read", "reject:replay",
        "schema:read", "schema:gate",
        "link:read", "link:recompute",
        "export:read", "audit:read", "overview:read",
    },
    Role.ANALYST.value: {
        "table:read", "event:read",
        "run:read",
        "reject:read", "reject:replay",
        "schema:read", "link:read",
        "export:read", "overview:read",
    },
    Role.VIEWER.value: {
        "table:read", "event:read", "run:read", "reject:read", "schema:read", "link:read", "overview:read",
    },
}

TABLE_WRITE_ROLES = (Role.ADMIN.value,)
EVENT_INGEST_ROLES = (Role.ADMIN.value,)
RUN_CREATE_ROLES = (Role.ADMIN.value,)
REPLAY_ROLES = (Role.ADMIN.value, Role.ANALYST.value)
GATE_ROLES = (Role.ADMIN.value,)
LINK_RECOMPUTE_ROLES = (Role.ADMIN.value, Role.ANALYST.value)
EXPORT_ROLES = (Role.ADMIN.value, Role.ANALYST.value)
AUDIT_ROLES = (Role.ADMIN.value,)
READ_ROLES = (Role.ADMIN.value, Role.ANALYST.value, Role.VIEWER.value)


def has_permission(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())
