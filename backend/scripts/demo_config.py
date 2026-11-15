"""演示账号与种子数据（CDC 事件流按确定性规则生成，无随机数）。

生成口径
--------
- 3 张源表，每表 800 条事件 = 250 个主键 × 3 条（c/u/u）+ 前 50 个主键各 1 条额外 u。
- 事件时间压缩到 30 秒窗口（每 40 条进 1 秒），ingest_at 与 event_time 同窗，
  因此 watermark = max(ingest) - 30s = 窗口起点 - 1s，正常事件不会被判迟到。
- 每表注入：20 条迟到（event_time = 窗口起点 - 6h）、1 条类型穿帮、7/7/6 条重复 (pk,lsn)、3/3/2 条删除，
  并把其中 2/1/1 条「删除之后才到达的旧更新」做成 stale_after_delete。
- 全库批次 run1：事件 2400 / 迟到 60 / 重复 20 / 拒绝 27 / upsert 742 / 删除终态 8。
"""
from __future__ import annotations

from datetime import datetime, timedelta

DEMO_PASSWORD = "Demo@2026"

DEMO_USERS = [
    ("admin@cdcmerge.io", "林可 · 数据集成管理员", "admin"),
    ("analyst@cdcmerge.io", "周棠 · 同步运维专员", "analyst"),
    ("viewer@cdcmerge.io", "陈默 · 业务只读", "viewer"),
]

ORG = "衡远保险 · 数据集成组"

# 事件窗口起点（UTC）：02:00Z = 北京时间 10:00，与验收口径一致
BASE_UTC = datetime(2026, 11, 14, 2, 0, 0)

POLICY_COLUMNS = [
    {"name": "policy_id", "type": "bigint", "nullable": False},
    {"name": "policy_no", "type": "varchar(32)", "nullable": False},
    {"name": "holder_name", "type": "varchar(64)", "nullable": True},
    {"name": "status", "type": "varchar(16)", "nullable": True},
    {"name": "premium_amount", "type": "decimal(12,2)", "nullable": True},
    {"name": "effective_date", "type": "date", "nullable": True},
]

CLAIM_COLUMNS = [
    {"name": "claim_id", "type": "bigint", "nullable": False},
    {"name": "claim_no", "type": "varchar(32)", "nullable": False},
    {"name": "policy_id", "type": "bigint", "nullable": True},
    {"name": "claim_amount", "type": "bigint", "nullable": True},
    {"name": "status", "type": "varchar(16)", "nullable": True},
    {"name": "reported_at", "type": "datetime", "nullable": True},
]

PREMIUM_COLUMNS = [
    {"name": "txn_id", "type": "bigint", "nullable": False},
    {"name": "policy_no", "type": "varchar(16)", "nullable": False},
    {"name": "premium_amt", "type": "decimal(12,2)", "nullable": True},
    {"name": "currency", "type": "varchar(3)", "nullable": True},
    {"name": "paid_at", "type": "datetime", "nullable": True},
]

TABLES = [
    {
        "code": "T_POLICY",
        "name": "车险保单主表",
        "table_name": "t_policy",
        "pk_column": "policy_id",
        "pk_prefix": "POL",
        "id_base": 100000,
        "binlog_file": "mysql-bin.000418",
        "binlog_position": 842133,
        "allowed_lateness_seconds": 30,
        "schema_columns": POLICY_COLUMNS,
        "note": "保单状态类字段最怕乱序：旧值覆盖新值会直接造成承保状态倒退。",
        "statuses": ["已承保", "待核保", "已批改", "已退保"],
        "money_column": "premium_amount",
        "money_scale": 0.37,
        "time_column": "effective_date",
        "int_column": "policy_id",
        "duplicate_count": 7,
        "delete_pks": [100, 101, 102],
        "stale_pks": [100, 101],
        "acceptance_pair": True,
        # 跨表关联门禁：保单是父表；「已退保」视为作废，其名下理赔 / 收缴不进宽表
        "parent_table_code": None,
        "parent_key_column": None,
        "parent_status_column": "status",
        "void_statuses": ["已退保"],
    },
    {
        "code": "T_CLAIM",
        "name": "理赔报案表",
        "table_name": "t_claim",
        "pk_column": "claim_id",
        "pk_prefix": "CLM",
        "id_base": 200000,
        "binlog_file": "mysql-bin.000418",
        "binlog_position": 1288314,
        "allowed_lateness_seconds": 30,
        "schema_columns": CLAIM_COLUMNS,
        "note": "报案金额列历史上被上游从 bigint 收窄过，门禁必须拦住。",
        "statuses": ["待核赔", "核定中", "已赔付"],
        "money_column": "claim_amount",
        "money_scale": 137.0,
        "time_column": "reported_at",
        "int_column": "claim_amount",
        "duplicate_count": 7,
        "delete_pks": [100, 101, 102],
        "stale_pks": [100],
        "acceptance_pair": False,
        # 从表：按数字外键 policy_id 关联保单；父单未到 -> 保留待关联、不发布
        "parent_table_code": "T_POLICY",
        "parent_key_column": "policy_id",
        "parent_status_column": "status",
        "void_statuses": ["已退保"],
        "orphan_claims": 6,
    },
    {
        "code": "T_PREMIUM",
        "name": "保费收缴流水表",
        "table_name": "t_premium",
        "pk_column": "txn_id",
        "pk_prefix": "TXN",
        "id_base": 300000,
        "binlog_file": "mysql-bin.000419",
        "binlog_position": 55210,
        "allowed_lateness_seconds": 30,
        "schema_columns": PREMIUM_COLUMNS,
        "note": "收缴流水 1:1 对应支付回调，重复投递要靠 (pk,lsn) 去重。",
        "statuses": ["已缴", "待缴", "已退费"],
        "money_column": "premium_amt",
        "money_scale": 0.21,
        "time_column": "paid_at",
        "int_column": "txn_id",
        "duplicate_count": 6,
        "delete_pks": [100, 101],
        "stale_pks": [100],
        "acceptance_pair": False,
        # 从表：按业务单号 policy_no 关联保单（父单主键带 POL- 前缀）
        "parent_table_code": "T_POLICY",
        "parent_key_column": "policy_no",
        "parent_status_column": "status",
        "void_statuses": ["已退保"],
    },
]

# Schema 变更：before/after 列快照（算法 B 的输入），门禁初始状态在种子中固定
SCHEMA_CHANGES = [
    {
        "change_key": "SC-20261114-01",
        "table_code": "T_POLICY",
        "gate_status": "approved",
        "rename_hints": {},
        "after_columns": POLICY_COLUMNS + [{"name": "cancel_reason", "type": "varchar(120)", "nullable": True}],
        "note": "上游新增退保原因列（可空），可直接透传 ODS",
    },
    {
        "change_key": "SC-20261114-02",
        "table_code": "T_POLICY",
        "gate_status": "approved",
        "rename_hints": {},
        "after_columns": [
            {**col, "type": "varchar(128)"} if col["name"] == "holder_name" else col for col in POLICY_COLUMNS
        ],
        "note": "投保人姓名由 varchar(64) 放宽到 varchar(128)",
    },
    {
        "change_key": "SC-20261114-03",
        "table_code": "T_CLAIM",
        "gate_status": "blocked",
        "rename_hints": {},
        "after_columns": [
            {**col, "type": "int"} if col["name"] == "claim_amount" else col for col in CLAIM_COLUMNS
        ],
        "note": "理赔金额 bigint → int：历史上这条 DDL 打挂过同步任务，必须拦截并回滚",
    },
    {
        "change_key": "SC-20261114-04",
        "table_code": "T_CLAIM",
        "gate_status": "pending",
        "rename_hints": {},
        "after_columns": CLAIM_COLUMNS + [{"name": "channel", "type": "varchar(16)", "nullable": False}],
        "note": "新增非空无默认值列 channel，存量行无法补齐，待门禁复核",
    },
    {
        "change_key": "SC-20261114-05",
        "table_code": "T_PREMIUM",
        "gate_status": "pending",
        "rename_hints": {"premium_amt": "premium_amount"},
        "after_columns": [
            {**col, "name": "premium_amount"} if col["name"] == "premium_amt" else col for col in PREMIUM_COLUMNS
        ],
        "note": "premium_amt 重命名为 premium_amount，需要补字段映射后再放行",
    },
    {
        "change_key": "SC-20261114-06",
        "table_code": "T_PREMIUM",
        "gate_status": "approved",
        "rename_hints": {},
        "after_columns": [
            {**col, "type": "varchar(8)"} if col["name"] == "currency" else col for col in PREMIUM_COLUMNS
        ],
        "note": "币种由 varchar(3) 放宽到 varchar(8)（支持 ISO 4217 之外的自定义码）",
    },
]

# (seed_key, 表 code 或 None, 触发方式, 操作人)
SEED_RUNS = [
    ("seed-merge-all-20261114", None, "seed", "admin@cdcmerge.io"),
    ("seed-merge-claim-20261114", "T_CLAIM", "manual", "admin@cdcmerge.io"),
]

SEED_AUDIT = [
    ("seed-audit-table", "admin@cdcmerge.io", "admin", "table.create", "source_table", {"code": "T_POLICY"}),
    ("seed-audit-run", "admin@cdcmerge.io", "admin", "run.create", "merge_run", {"scope": "全库"}),
    ("seed-audit-gate", "admin@cdcmerge.io", "admin", "schema.gate", "schema_change", {"gate": "blocked"}),
]


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _iso_seconds(index: int) -> int:
    """事件窗口：每 40 条进 1 秒，最多到 28 秒。"""
    return min(28, index // 40)


def _after_row(spec: dict, n: int, tier: int) -> dict:
    pk = spec["id_base"] + n
    base = BASE_UTC
    statuses = spec["statuses"]
    status = statuses[(n + tier) % len(statuses)]
    if spec["code"] == "T_POLICY":
        return {
            "policy_id": pk,
            "policy_no": f"{spec['pk_prefix']}-{pk}",
            "holder_name": f"被保险人{n:03d}",
            "status": status,
            "premium_amount": round(3200 + (n * 37) % 9800 + tier * 11.5, 2),
            "effective_date": _fmt_dt(base + timedelta(days=n % 28))[:10],
        }
    if spec["code"] == "T_CLAIM":
        return {
            "claim_id": pk,
            "claim_no": f"{spec['pk_prefix']}-{pk}",
            # 第 100~105 个主键指向不存在的保单（编号越过父表 250 个主键），制造「父单未到」
            "policy_id": 100000 + (n if n < 100 else (306 if n < 106 else n)),
            "claim_amount": (n * 137) % 90000 + 500,
            "status": status,
            "reported_at": _fmt_dt(base + timedelta(minutes=n % 600)),
        }
    return {
        "txn_id": pk,
        "policy_no": f"POL-{100000 + n}",
        "premium_amt": round(680 + (n * 13) % 4200 + tier * 3.5, 2),
        "currency": "CNY",
        "paid_at": _fmt_dt(base + timedelta(minutes=n % 600)),
    }


def build_events(spec: dict) -> list[dict]:
    """按确定性规则生成一张表的 800 条事件（含迟到 / 重复 / 删除 / 穿帮 / stale）。"""
    base = BASE_UTC
    pk_prefix = spec["pk_prefix"]
    ids = [f"{pk_prefix}-{spec['id_base'] + n}" for n in range(250)]
    rows: list[dict] = []

    def add(n: int, tier: int, index: int, *, op: str, event_time: datetime, ingest_at: datetime) -> dict:
        after = _after_row(spec, n, tier)
        event = {
            "pk_value": ids[n],
            "lsn": 700000 + n * 10 + tier,
            "op": op,
            "event_time": event_time,
            "ingest_at": ingest_at,
            "after": after if op != "d" else {},
            "before": {},
        }
        rows.append(event)
        return event

    for n in range(250):
        for tier in range(3):
            index = n * 3 + tier
            event_time = base + timedelta(seconds=_iso_seconds(index))
            ingest_at = event_time
            op = "c" if tier == 0 else "u"
            event = add(n, tier, index, op=op, event_time=event_time, ingest_at=ingest_at)
            if spec["acceptance_pair"] and n == 0:
                # 验收场景：POL-100000 的 u(10:00 已承保) 与 u(10:05 待核保) 乱序到达，终态取 10:05
                if tier == 1:
                    event["event_time"] = base
                    event["ingest_at"] = base + timedelta(seconds=20)
                    event["after"]["status"] = "已承保"
                    event["lsn"] = 700001
                elif tier == 2:
                    event["event_time"] = base + timedelta(seconds=300)
                    event["ingest_at"] = base + timedelta(seconds=12)
                    event["after"]["status"] = "待核保"
                    event["lsn"] = 700002
                else:
                    event["lsn"] = 700000

    # 额外 50 条 u；其中前 N 条改造成重复投递（同一 pk 同一 lsn 再投一次）
    for n in range(50):
        index = 750 + n
        event_time = base + timedelta(seconds=_iso_seconds(index))
        ingest_at = event_time
        if n < spec["duplicate_count"]:
            source = rows[n * 3 + 2]
            event_time = source["event_time"]
            ingest_at = base + timedelta(seconds=29)
        event = add(n, 3, index, op="u", event_time=event_time, ingest_at=ingest_at)
        if n < spec["duplicate_count"]:
            event["after"] = dict(rows[n * 3 + 2]["after"])
            event["lsn"] = rows[n * 3 + 2]["lsn"]
            event["is_duplicate"] = True

    # 迟到：20 个主键的第 3 条事件 event_time 回拨 6 小时
    for n in range(50, 70):
        rows[n * 3 + 2]["event_time"] = base - timedelta(hours=6) - timedelta(seconds=1)
        rows[n * 3 + 2]["is_late"] = True

    # 类型穿帮：第 150 个主键的第 2 条事件，整型列传字符串
    mismatch = rows[150 * 3 + 1]
    mismatch["after"] = dict(mismatch["after"])
    mismatch["after"][spec["int_column"]] = "N/A"

    # 删除：第 3 条事件改为 d，ingest 往后挪到 27s，制造「删除先到」
    for n in spec["delete_pks"]:
        delete_event = rows[n * 3 + 2]
        delete_event["op"] = "d"
        delete_event["after"] = {}
        delete_event["ingest_at"] = base + timedelta(seconds=27)

    # stale：这些主键的第 2 条 u 在删除之后才到达
    for n in spec["stale_pks"]:
        stale_event = rows[n * 3 + 1]
        stale_event["ingest_at"] = base + timedelta(seconds=29)

    # 按到达时间排序后写 seq，并补齐 ingest_at 与 event_time 的时区无关表示
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: (pair[1]["ingest_at"], pair[1]["lsn"], pair[0]),
    )
    result: list[dict] = []
    for seq, (_, event) in enumerate(ordered, start=1):
        status = "late" if event.get("is_late") else "applied"
        result.append(
            {
                "pk_value": event["pk_value"],
                "lsn": int(event["lsn"]),
                "seq": seq,
                "op": event["op"],
                "event_time": event["event_time"],
                "ingest_at": event["ingest_at"],
                "before_data": event.get("before") or {},
                "after_data": event.get("after") or {},
                "is_late": bool(event.get("is_late")),
                "late_seconds": 21600 if event.get("is_late") else 0,
                "is_duplicate": bool(event.get("is_duplicate")),
                "status": status,
            }
        )
    return result
