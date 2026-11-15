# CDC 乱序合并与 Schema 兼容门禁（`cdcmerge`）

> 数据工程 · 中型多模块项目。原生 HTML/CSS/JS 前端 + Python FastAPI 后端 + Celery worker + MySQL 8.0 + Redis，5 个容器一键起。
> 端口：前端 `18205` / 后端 `19205` / MySQL 映射 `17705`，数据库 `cdcmerge`（MySQL 8.0）。

## 1. 场景

- **模拟客户**：衡远保险 · 数据集成组（8 人）。业务库 MySQL binlog → Kafka → 数仓 ODS，14 张核心表，其中 3 张先接入（车险保单、理赔报案、保费收缴）。
- **现有工作流**：自研同步脚本按「到达顺序」逐条覆盖 ODS 宽表，DDL 变更靠上游邮件通知、人工比对列结构。
- **三个痛点**：
  1. **乱序到达**：车险保单状态倒退（`待核保 → 已承保` 的旧事件晚到，把 `已承保` 覆盖回 `待核保`），历史上出过资损。
  2. **重复事件**：Kafka 至少一次投递 + 重放补偿，同一 `(pk, lsn)` 被写两次。
  3. **上游 DDL 直接打挂同步**：`bigint → int`、新增非空列、列重命名都会让 ODS 写入报错，脚本只能整批失败。
  4. **跨表乱序（有案无单）**：理赔记录先于父保单到达，全库合并会把理赔直接写宽表；保单补到后也不会重关联，
     报表出现「有案无单」。现在父单未到的理赔先保留待关联、不发布，父单到达自动放行，父单作废撤回并重算。
- **上下游**：业务库 binlog → Kafka → **本系统（乱序合并 → 拒绝行判定 → Schema 门禁 → 重放/放行）** → ODS 宽表 / 下游报表。
- **一天的工作剧本**：
  1. 09:40 worker 定时对启用表跑全库合并（`MR…-0001`，2400 条事件）。
  2. 10:02 运维专员打开事件流，看到 `POL-100000` 的 `10:05 待核保` 比 `10:00 已承保` 先到（迟到标注）。
  3. 合并批次里 60 条迟到、20 条重复、27 条拒绝行；拒绝行按原因分诊。
  4. 10:20 上游提 `claim_amount bigint → int`，门禁判 `breaking`，`block` 并给出回滚 SQL。
  5. 11:00 专员重放 8 条 `duplicate` 拒绝行、跳过 1 条 `type_mismatch`。
  6. 11:10 管理员导出 `merge-runs.csv` / `rejects.csv` / `schema-changes.csv` 给数据治理周会。
  7. 11:20 审计里能看到登录、合并、门禁、导出的完整链路。
  8. 11:30 关联门禁页核对：待关联 3 条（父单 `POL-100306` 未到）、放行 368 条、撤回 129 条；
     宽表 185 行已发布 / 65 行已撤回；补一条 `POL-100306` 后重算，3 条理赔自动放行。

## 2. 角色与权限

| 角色 | 账号 | 权限 | 典型动作 |
|---|---|---|---|
| 数据集成管理员 `admin` | `admin@cdcmerge.io` | 订阅维护 + 跑合并 + 批门禁 + 关联重算 + 导出 + 审计 | 登记源表与父表关联、改水位线参数、发起全库合并、放行/拦截 DDL、重算关联门禁、导出 CSV |
| 同步运维专员 `analyst` | `analyst@cdcmerge.io` | 事件与拒绝行只读 + 重放 + 关联重算 + 导出 | 巡检迟到事件、重放/跳过拒绝行、补单后重算关联门禁、导出报表 |
| 业务只读 `viewer` | `viewer@cdcmerge.io` | 只读列表与详情 | 看事件流、批次结果、门禁结论、关联门禁核对 |

密码统一 `Demo@2026`。权限是**后端真实 403**（`require_roles` 依赖），不是前端隐藏：
`viewer` 调 `POST /api/runs`、`PATCH /api/schema-changes/{id}/gate`、`GET /api/audit`、`GET /api/exports/*`、
`PATCH /api/rejects/{id}/replay`、`POST /api/link-gate/reconcile` 返回 403；`analyst` 调 `POST /api/runs`、`GET /api/audit`、
`PATCH .../gate` 返回 403（`POST /api/link-gate/reconcile` 对 analyst 开放）。

## 3. 业务模块（7 个）

| # | 业务模块 | 对应页面 | 后端 services / controller | 主要 API |
|---|---|---|---|---|
| ① | 源表与订阅 | `#/tables` 源表与订阅 | `table_service` / `table_controller` | `GET/POST /api/tables`、`GET/PATCH /api/tables/{id}` |
| ② | 事件接入与乱序注入 | `#/events` 事件流 | `event_service` / `event_controller` | `GET /api/events`、`POST /api/events` |
| ③ | 水位线乱序合并 | `#/runs` 合并批次 | `watermark_merge`(算法 A)+`merge_run_service` / `run_controller` | `GET/POST /api/runs`、`GET /api/runs/{id}/detail` |
| ④ | 拒绝行判定与重放 | `#/runs` 抽屉内拒绝行 | `reject_service` / `reject_controller` | `GET /api/rejects`、`PATCH /api/rejects/{id}/replay` |
| ⑤ | Schema 兼容门禁 | `#/schema` Schema 门禁 | `schema_gate`(算法 B)+`schema_service` / `schema_controller` | `GET /api/schema-changes`、`PATCH .../{id}/gate`、`POST .../{id}/reevaluate` |
| ⑥ | 导出与批次统计 | `#/schema`、`#/runs` 导出按钮 / `#/overview` 总览 | `export_service`+`overview_service` / `export_controller` | `GET /api/exports/{merge-runs,rejects,schema-changes,link-rows,wide-rows}.csv`、`GET /api/overview` |
| ⑦ | 跨表关联门禁 | `#/link` 关联门禁 | `link_gate`(算法 C)+`link_service` / `link_controller` | `GET /api/link-gate`、`GET /api/link-gate/{links,wide-rows}`、`POST /api/link-gate/reconcile` |

横切能力（不计入业务模块）：JWT + 三角色鉴权、审计中间件、限流中间件、CSV 导出机制、worker 心跳。

## 4. 页面（7 个）

| 页面 | 路由 | 说明 | 共用组件 |
|---|---|---|---|
| 登录 | `#/login` | 左「信号墙」示意到达顺序 vs 事件顺序，右工牌式登录卡 | — |
| 总览 | `#/overview` | 当前批次水位线刻度、表级订阅卡、拒绝原因/门禁分布、7 天趋势、admin 审计面板 | 表格/筛选条/抽屉 |
| 源表与订阅 | `#/tables` | 订阅列表（事件/迟到/水位线）、列定义抽屉、登记源表 | `dataTable` + `filterBar` + `drawer` |
| 事件流 | `#/events` | 乱序标注（迟到 −Xs）、按表/操作/仅迟到过滤、注入乱序事件 | `dataTable` + `filterBar` + `drawer` |
| 合并批次 | `#/runs` | 算法 A 指标格（upsert/删除/迟到/重复/拒绝/待关联/宽表）、按表拆分、关联门禁分区、拒绝行处置、导出 | `dataTable` + `filterBar` + `drawer` + `metricGrid` |
| Schema 门禁 | `#/schema` | 兼容性/动作/回滚 SQL、列对照 diff、门禁复核、重算兼容性、导出 | `dataTable` + `filterBar` + `drawer` + `columnDiff` |
| 关联门禁 | `#/link` | 待关联/放行/撤回计数、从表分布、放行依据抽屉、宽表最终状态、重算 | `dataTable` + `filterBar` + `drawer` + `metricGrid` |

> 审计日志是横切能力，UI 入口在总览页底部（仅 admin 可见）；完整查询走 `GET /api/audit`。

## 5. 快速启动

```bash
cp .env.example .env
docker compose up -d --build
# 等 5/5 healthy
open http://localhost:18205
```

首次启动 backend 自动建表并执行一次 seed（`SEED_DEMO=true`）。seed 幂等，可重复执行：

```bash
docker compose exec -T backend python -m scripts.seed_demo
docker compose exec -T backend python -m pytest tests -q
```

## 6. 访问地址

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:18205 |
| 后端 API 文档 | http://localhost:19205/docs |
| 健康检查 | http://localhost:19205/api/health |
| 数据库映射 | localhost:17705 |

## 7. 技术栈

| 层 | 选型 |
|---|---|
| 前端 | 原生 HTML5 + CSS（oklch 令牌 / 响应式三档）+ ES Modules；无框架、无打包、除 Google Fonts 外无 CDN |
| 后端 | Python 3.11、FastAPI 0.115、SQLAlchemy 2.0、PyJWT、passlib(bcrypt)、Pydantic 2 |
| 异步 | Celery 5.4 + Redis（10s 心跳、每日 07:40 全库合并） |
| 数据库 | MySQL 8.0（utf8mb4，JSON 列存 before/after/指标） |
| 部署 | Docker Compose，恰好 5 服务，全部带 healthcheck |

## 8. 核心算法与口径

### 算法 A｜按水位线乱序合并（`backend/src/services/watermark_merge.py`）

- **输入**：一批 CDC 事件 `{pk_value, lsn, op(c|u|d), event_time, ingest_at, before, after}` + 表 schema 列定义。
- **水位线**：`watermark = max(ingest_at) − allowed_lateness_seconds`（默认 30s，可按时段表配置）。
  `event_time < watermark` 的事件计入 **late**，仍参与折叠但带迟到标记与 `late_seconds`。
- **去重**：按到达顺序扫一遍，重复出现的 `(pk, lsn)` 只保留首次，其余计入 `dup_skipped` 并生成拒绝行 `duplicate`。
- **折叠**：同 pk 按 `(event_time, lsn)` 升序；`c` 覆盖整行、`u` 按字段合并、`d` 清空状态；
  若某条 `u` 的事件时间早于一条**先到达**的 `d`，判 `stale_after_delete` 丢弃（否则会把已删除行复活）。
- **类型校验**：`after` 值的字面类型与订阅 schema 不符（整型列传 `"N/A"` 等）判 `type_mismatch` 拒绝。
- **输出**：`upsert_rows / deletes / late_count / dup_skipped / rejected / watermark`、`rows[]`、`rejects[]`、逐表明细。
- **验收复现**：`POL-100000` 的 `u(10:00, 状态=已承保)` 与 `u(10:05, 状态=待核保)` 乱序到达（后者先到），
  合并后终态 `status=待核保`（事件时间 10:05 胜出）。见 `backend/tests/test_watermark_merge.py::test_out_of_order_update_keeps_latest_event_time`。

### 算法 B｜Schema 兼容判定（`backend/src/services/schema_gate.py`）

- **输入**：变更前 / 变更后的列定义 `[{name,type,nullable,default}]`，可选 `rename_hints`。
- **判定**：
  - 新增可空列 / 有默认值 → `forward`，`severity=low`，`action=apply`；
  - 新增非空无默认值 → `breaking`，`action=block`；
  - 删除列 → `breaking`，`action=block`；
  - 类型收窄（`bigint→int`、`varchar(32)→varchar(16)`、`decimal` 精度下降、`datetime→date`）→ `breaking`；
  - 类型放宽（`int→bigint`、`varchar` 增长、`date→datetime`）→ `forward`；
  - 重命名（同位置 + 同基类型，或显式映射）→ `needs_mapping`，`action=backfill`；
  - 无法判定的类型替换 → `needs_mapping`。
- **输出**：`compat / severity / action(apply|backfill|block) / change_kind / findings[] / rollback_sql / summary`。
  `rollback_sql` 是可执行 MySQL DDL（`MODIFY COLUMN` 还原旧类型、`ADD COLUMN` 还原被删列等）。
- **验收复现**：`claim_amount bigint → int` 判 `breaking` + `block`，回滚 SQL 为
  `ALTER TABLE \`t_claim\` MODIFY COLUMN \`claim_amount\` bigint NULL;`
  （见 `test_bigint_to_int_is_breaking_with_rollback`）。

### 算法 C｜跨表关联门禁（`backend/src/services/link_gate.py`）

- **输入**：各表算法 A 的折叠终态 `rows[]` / `deleted_pks[]`；从表配置 `parent_table_code`、
  `parent_key_column`（指向父表主键的外键列）、`parent_status_column`、`void_statuses`。
- **父单解析**：父表折叠终态按主键建立「别名表」——主键本身、父表 `pk_column` 的值、带业务前缀主键的数字尾号
  （`POL-100000` ↔ `100000`）；从表外键命中任一别名即为父单存在。
- **三态迁移**（自然键 `pending_links(table_id, pk_value)`，只迁移不新增）：
  - 父单缺失 / 外键为空 → `pending`（保留待关联，**不写宽表**，留保留依据）；
  - 父单存在且状态不在 `void_statuses` → `released`，写进 `wide_rows.child_rows`，留放行依据与放行批次；
  - 父单删除 d 或状态命中 `void_statuses` → `withdrawn`，宽表行标记撤回、子行清空、指标重算；
  - 从表自身删除 d → `withdrawn`（父单仍有效时宽表行保留、子行移除）。
- **恢复性**：每批合并在同一事务内跑门禁；`POST /api/link-gate/reconcile` 不新增批次，直接用库内事件重算折叠终态再跑门禁，
  覆盖「父单补到」「父单作废」等迟到场景。
- **幂等**：状态不变就不迁移、不写审计；重复执行同一批 → 待关联记录、宽表行、审计均零新增。
  审计按动作聚合（`link.hold / link.release / link.withdraw / wide.rebuild`，带条数与样例）。
- **不变量**：`released` 记录必须对应已发布父宽表行（`checks.orphan_released=0`）；
  已发布宽表 `child_rows` 必须全部来自 `released` 记录（`checks.stray_children=0`）。
  页面「门禁不变量」格直接展示这两条。
- **验收复现**：seed 中 `CLM-200103..105` 的 `policy_id=100306` 越过父表主键范围 → 待关联；
  补 `POL-100306` 事件后 `reconcile` 自动放行；父单 `已退保` 或删除 → 对应子记录撤回。

算法 A/B/C 都是纯 Python、无网络、无随机；同样输入两次输出逐字一致。结果落库后出现在页面（总览/批次抽屉/Schema 抽屉/关联门禁页）
与五类 CSV 导出中，前端不含任何算法结果常量。

## 9. Seed 口径（幂等）

`backend/scripts/seed_demo.py` 的自然键：`users.email`、`source_tables.code`、`cdc_events(table_id, pk_value, lsn, seq)`、
`merge_runs.seed_key`、`schema_changes.change_key`、`audit_logs.seed_key`；存在即跳过，**不覆盖**用户改过的门禁/重放状态。

结束打印一行（连续两次执行逐字一致）：

```
SEED_SUMMARY brand=cdcmerge users=3 sources=3 runs=2 findings=6 pending=2
```

口径：`sources`=源表数、`runs`=合并批次数、`findings`=Schema 变更数、`pending`=待门禁复核数。
数据量：3 张源表 × 800 条事件 = 2400 条（60 迟到、20 重复、8 删除、3 条类型穿帮、4 条 stale），2 个批次
（全库 + T_CLAIM 单表），6 个 Schema 变更（2 breaking / 1 needs_mapping / 3 forward）。
跨表关联：`T_CLAIM`（按 `policy_id`）、`T_PREMIUM`（按 `policy_no`）为从表，父表 `T_POLICY`，`已退保` 视为作废。
全库批次门禁口径：待关联 3（`CLM-200103..105` 的 `policy_id=100306`）、已放行 368、已撤回 129；
宽表 185 行已发布 / 65 行已撤回 / 250 行合计。补齐 `POL-100306` 后重算，3 条理赔自动放行。
`ensure_link_gate` 只对老库（`pending_links` / `wide_rows` 为空）补跑一次，重算本身只在状态迁移时写审计。

## 10. 共享枚举出现位置

| 枚举 | 后端 | 前端 |
|---|---|---|
| 角色 `admin/analyst/viewer` | `backend/src/constants/{roles,enums}.py` | `frontend/site/js/config.js`（`ROLE_LABELS`/`PERMISSIONS`） |
| binlog 操作 `c/u/d` | `backend/src/constants/enums.py::CdcOp` | `config.js::ENUMS.op` |
| 批次状态 `queued…failed` | `backend/src/constants/enums.py::RunStatus` | `config.js::ENUMS.runStatus` |
| 兼容性 `forward/needs_mapping/breaking` | `backend/src/constants/enums.py::Compat` | `config.js::ENUMS.compat` |
| 门禁动作 `apply/backfill/block` | `backend/src/constants/enums.py::GateAction` | `config.js::ENUMS.gateAction` |
| 门禁状态 `pending/approved/blocked` | `backend/src/constants/enums.py::GateStatus` | `config.js::ENUMS.gateStatus` |
| 拒绝原因 `duplicate/stale_after_delete/type_mismatch…` | `backend/src/constants/enums.py::RejectReason` | `config.js::ENUMS.rejectReason` |
| 重放状态 `pending/replayed/skipped` | `backend/src/constants/enums.py::ReplayStatus` | `config.js::ENUMS.replay` |
| 关联状态 `pending/released/withdrawn` | `backend/src/constants/enums.py::LinkStatus` | `config.js::ENUMS.linkStatus` |
| 关联原因 `parent_missing/parent_void/parent_linked/child_deleted` | `backend/src/constants/enums.py::LinkReason` | `config.js::ENUMS.linkReason` |
| 宽表状态 `published/withdrawn` | `backend/src/constants/enums.py::WideRowStatus` | `config.js::ENUMS.wideStatus` |

## 11. 目录

```text
backend/
  src/{config,constants,controllers,middlewares,models,routes,schemas,services,utils}
  src/services/{watermark_merge,schema_gate}.py     # 两个纯函数算法
  src/services/link_gate.py                         # 跨表关联门禁（父/子三态迁移）
  src/models/{pending_link,wide_row}.py              # 待关联记录 / 宽表发布层
  scripts/{seed_demo.py,demo_config.py,worker_healthcheck.sh}
  migrations/001_core_tables.sql                    # 人类可读 DDL（运行时不执行）
  tests/{test_watermark_merge,test_schema_gate,test_security,test_link_gate,test_link_gate_integration}.py
frontend/site/{index.html,css/{tokens,app}.css,js/{config,api,store,ui,app}.js,js/pages/*.js}
docker-compose.yml  .env  .env.example
```

## 12. 环境变量

见 `.env.example`：`COMPOSE_PROJECT_NAME`、`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`DB_*`、`JWT_SECRET`、
`REDIS_URL`、`SEED_DEMO`、`RATE_LIMIT_PER_MINUTE`（300/分钟）、`RATE_LIMIT_WRITE_PER_MINUTE`（60/分钟写）、
`APP_TIMEZONE_OFFSET_MINUTES`（480）。

## 13. Docker 说明

- 5 个服务：`frontend`(Nginx 静态站) / `backend`(uvicorn) / `worker`(Celery) / `db`(MySQL 8.0) / `redis`，全部 healthcheck。
- 命名卷：`db_data`、`redis_data`；`depends_on` 用 `condition: service_healthy`；端口全部 `${VAR:-默认}` 写法。
- Nginx：`location /api/ { proxy_pass http://backend:8000/; }` 剥前缀 + SPA 回落。
- 收尾：`docker compose down -v --remove-orphans`。

## 14. 已知取舍

- 事件进入系统只有 seed 与 `POST /api/events` 两条路径，没有真的连 Kafka（离线演示环境）。
- 合并批次在请求内同步执行（2400 条事件约 100–300ms），未走 Celery 异步队列；worker 只负责心跳与每日定时合并。
- 重放动作只改拒绝行的处置状态并留审计，不会把事件重新投递进合并（真实系统需要 Kafka producer，Demo 中省略）。
- 关联门禁的父单识别依赖「父表折叠终态 + 别名（主键 / pk_column / 数字尾号）」，不做通用 join 引擎；
  多级父链（父-子-孙）需要逐级配置从表，Demo 只覆盖一层。
- 宽表行按父单主键去重，一个父单下多个从表子记录合并进同一行 `child_rows`；不支持一对多子表分列。

## License

内部演示项目，仅用于评测与教学。
