"""审计动作 -> 中文标签。"""
from __future__ import annotations

ACTIONS: dict[str, str] = {
    "auth.login": "登录",
    "table.create": "登记源表订阅",
    "table.update": "修改源表配置",
    "event.ingest": "注入乱序事件",
    "run.create": "发起合并批次",
    "reject.replay": "处置拒绝行",
    "schema.gate": "门禁复核",
    "schema.reevaluate": "重算兼容性",
    "link.hold": "保留待关联",
    "link.release": "父单到达放行",
    "link.withdraw": "父单作废撤回",
    "link.reconcile": "重算关联门禁",
    "wide.rebuild": "宽表重算",
    "export.merge_runs": "导出合并批次",
    "export.rejects": "导出拒绝行",
    "export.schema_changes": "导出 Schema 门禁",
    "export.link_rows": "导出关联门禁清单",
    "export.wide_rows": "导出宽表状态",
    "http.mutation": "写请求",
}
