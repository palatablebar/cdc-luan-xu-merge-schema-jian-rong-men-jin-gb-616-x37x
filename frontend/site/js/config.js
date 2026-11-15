/**
 * 项目品牌、导航、角色权限与枚举文案（前端唯一定义处）。
 * 后端对应：backend/src/constants/{roles,enums}.py；README 列出全部出现位置。
 */
export const BRAND = {
  short: 'cdcmerge',
  title: 'CDC 乱序合并与 Schema 兼容门禁',
  org: '衡远保险 · 数据集成组',
  edition: 'BINLOG → ODS · 2026',
  subtitle: '按水位线折叠乱序事件，用门禁拦住会打挂同步的 DDL',
  loginLead: '旧值覆盖新值出过资损。这里的每条事件都带 (pk, lsn, event_time)，合并只认事件时间，不认到达顺序。',
  loginPoints: [
    ['01', '水位线 = max(ingest_at) − 允许迟到；迟到事件照收，但必须在批次里留痕。'],
    ['02', '拒绝行只三种来路：重复投递、删除后到达的旧更新、列类型穿帮。'],
    ['03', 'Schema 变更先过算法门禁：breaking 直接 block 并给出回滚 SQL。'],
  ],
};

export const NAV = [
  { to: '#/overview', no: '01', label: '总览', permission: 'overview:read' },
  { to: '#/tables', no: '02', label: '源表与订阅', permission: 'table:read' },
  { to: '#/events', no: '03', label: '事件流', permission: 'event:read' },
  { to: '#/runs', no: '04', label: '合并批次', permission: 'run:read' },
  { to: '#/schema', no: '05', label: 'Schema 门禁', permission: 'schema:read' },
];

export const ROLE_LABELS = {
  admin: '数据集成管理员',
  analyst: '同步运维专员',
  viewer: '业务只读',
};

export const ROLE_DUTIES = {
  admin: '维护源表订阅与水位线、跑合并、批门禁、导出与审计',
  analyst: '巡检事件流与拒绝行、重放、导出报表',
  viewer: '只读查看事件、合并结果与门禁结论',
};

export const PERMISSIONS = {
  admin: [
    'overview:read', 'table:read', 'table:write', 'event:read', 'event:ingest',
    'run:read', 'run:create', 'reject:read', 'reject:replay',
    'schema:read', 'schema:gate', 'export:read', 'audit:read',
  ],
  analyst: [
    'overview:read', 'table:read', 'event:read', 'run:read',
    'reject:read', 'reject:replay', 'schema:read', 'export:read',
  ],
  viewer: ['overview:read', 'table:read', 'event:read', 'run:read', 'reject:read', 'schema:read'],
};

export const ENUMS = {
  // 共享枚举 1：binlog 行操作
  op: { c: '插入 c', u: '更新 u', d: '删除 d' },
  // 共享枚举 2：合并批次状态
  runStatus: { queued: '排队中', running: '执行中', success: '成功', partial: '部分成功', failed: '失败' },
  // 共享枚举 3：Schema 兼容性结论
  compat: { forward: '向前兼容', needs_mapping: '需字段映射', breaking: '破坏性变更' },
  // 共享枚举 4：门禁动作
  gateAction: { apply: '直接应用', backfill: '回填映射', block: '拦截回滚' },
  gateStatus: { pending: '待门禁复核', approved: '已放行', blocked: '已拦截' },
  // 共享枚举 5：拒绝行原因
  rejectReason: {
    duplicate: '重复事件',
    stale_after_delete: '删除后旧更新',
    type_mismatch: '列类型不匹配',
    unknown_op: '未知操作码',
    missing_pk: '缺少主键',
  },
  replay: { pending: '待处理', replayed: '已重放', skipped: '已跳过' },
  eventStatus: { applied: '已应用', late: '迟到', rejected: '被拒' },
  changeKind: {
    add_column: '新增可空列',
    add_not_null: '新增非空列',
    drop_column: '删除列',
    narrow_type: '类型收窄',
    widen_type: '类型放宽',
    rename_column: '列重命名',
  },
  trigger: { manual: '手动', schedule: '定时', seed: '初始化' },
  severity: { high: '高', medium: '中', low: '低' },
};

export function can(role, permission) {
  return (PERMISSIONS[role] || []).includes(permission);
}

export function compatTone(compat) {
  return { breaking: 'danger', needs_mapping: 'warn', forward: 'ok' }[compat] || 'info';
}

export function gateTone(status) {
  return { blocked: 'danger', pending: 'warn', approved: 'ok' }[status] || 'info';
}

export function reasonTone(reason) {
  return { duplicate: 'info', stale_after_delete: 'warn', type_mismatch: 'danger', unknown_op: 'danger', missing_pk: 'danger' }[
    reason
  ] || 'info';
}

export function replayTone(status) {
  return { pending: 'warn', replayed: 'ok', skipped: 'info' }[status] || 'info';
}

export function severityLabel(severity) {
  return ENUMS.severity[severity] || severity;
}
