import { api } from '../api.js';
import { can, compatTone, ENUMS, gateTone, severityLabel } from '../config.js';
import {
  bindFilterBar,
  columnDiff,
  dataTable,
  drawer,
  errorBox,
  errorMessage,
  esc,
  field,
  filterBar,
  fmtTime,
  kv,
  toast,
} from '../ui.js';

const state = { tableId: '', compat: '', gateStatus: '' };

function findingsList(findings) {
  if (!findings || !findings.length) return '<p class="muted">没有逐项判定明细。</p>';
  return `<ul class="finding-list">${findings
    .map(
      (item) => `<li>
        <span class="badge ${compatTone(item.compat)}">${esc(ENUMS.compat[item.compat] || item.compat)}</span>
        <span>${esc(item.message || '')}</span>
        <span class="mono muted">动作 ${esc(ENUMS.gateAction[item.action] || item.action)}</span>
      </li>`,
    )
    .join('')}</ul>`;
}

function openChange(view, ctx, change) {
  const canGate = can(ctx.user.role, 'schema:gate');
  const { root, close } = drawer({
    wide: true,
    title: `${change.change_key} · ${change.table_code || ''}`,
    subtitle: `${change.kind_label} · ${change.column_name || '—'}${change.target_column ? ` → ${change.target_column}` : ''}`,
    body: `
      ${kv([
        ['源表', `${esc(change.table_name || '—')}`],
        ['兼容性结论', `<span class="badge ${compatTone(change.compat)}">${esc(change.compat_label)}</span>`],
        ['严重度', `<span class="badge ${change.severity === 'high' ? 'danger' : change.severity === 'medium' ? 'warn' : 'ok'}">${esc(severityLabel(change.severity))}</span>`],
        ['门禁动作', `<b>${esc(change.action_label)}</b>`],
        ['门禁状态', `<span class="badge ${gateTone(change.gate_status)}">${esc(change.gate_status_label)}</span>`],
        ['复核人 / 备注', `${esc(change.reviewer || '—')} · ${esc(change.review_note || '—')}`],
        ['发现时间', fmtTime(change.detected_at)],
        ['判定摘要', esc(change.summary)],
      ])}
      <h3 class="drawer-h3">回滚 SQL（算法 B 输出）</h3>
      <pre class="code" data-testid="rollback-sql">${esc(change.rollback_sql || '—')}</pre>
      <h3 class="drawer-h3">逐项判定</h3>
      ${findingsList((change.detail || {}).findings)}
      <h3 class="drawer-h3">列定义对照（before / after）</h3>
      ${columnDiff(change.before_columns, change.after_columns)}`,
    footer: canGate
      ? `<form id="gate-form" class="inline-form">
          ${field({
            label: '门禁结论',
            name: 'gate_status',
            value: change.gate_status,
            options: Object.entries(ENUMS.gateStatus).map(([value, label]) => ({ value, label })),
          })}
          ${field({ label: '复核备注', name: 'note', value: change.review_note || '', placeholder: '写一句放行/拦截依据' })}
          <button class="btn accent" type="submit">提交门禁</button>
          <button class="btn ghost" type="button" data-reevaluate>重算兼容性</button>
        </form>`
      : '<span class="mono muted">只读角色可以查看，但不能批门禁</span>',
  });

  root.querySelector('#gate-form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api.patch(`/api/schema-changes/${change.id}/gate`, {
        gate_status: form.gate_status.value,
        note: form.note.value.trim() || null,
      });
      toast('门禁结论已保存');
      close();
      await schemaPage.render(view, ctx);
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
  root.querySelector('[data-reevaluate]')?.addEventListener('click', async () => {
    try {
      const result = await api.post(`/api/schema-changes/${change.id}/reevaluate`);
      toast(`重算完成：${result.compat_label} / ${result.action_label}`);
      close();
      await schemaPage.render(view, ctx);
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
}

export const schemaPage = {
  async render(view, ctx) {
    let data;
    let tables;
    try {
      [data, tables] = await Promise.all([
        api.get('/api/schema-changes', {
          limit: 50,
          table_id: state.tableId,
          compat: state.compat,
          gate_status: state.gateStatus,
        }),
        api.get('/api/tables', { limit: 50 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => schemaPage.render(view, ctx));
      return;
    }
    const pending = data.items.filter((item) => item.gate_status === 'pending').length;
    const breaking = data.items.filter((item) => item.compat === 'breaking').length;
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>Schema 变更与导出</h2>
          <div class="head-actions">
            <span class="meta">${data.total} 项 · breaking ${breaking} · 待复核 ${pending}</span>
            ${can(ctx.user.role, 'export:read') ? '<button class="btn sm ghost" id="export-schema">导出 Schema 门禁 CSV</button>' : ''}
          </div>
        </div>
        <div class="panel-body">
          ${filterBar({
            id: 'schema-filters',
            fields: [
              {
                label: '源表',
                name: 'table_id',
                value: state.tableId,
                options: [{ value: '', label: '全部' }, ...tables.items.map((table) => ({ value: table.id, label: table.code }))],
              },
              {
                label: '兼容性',
                name: 'compat',
                value: state.compat,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.compat).map(([value, label]) => ({ value, label }))],
              },
              {
                label: '门禁状态',
                name: 'gate_status',
                value: state.gateStatus,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.gateStatus).map(([value, label]) => ({ value, label }))],
              },
            ],
          })}
        </div>
        ${dataTable({
          rows: data.items,
          testId: 'schema-list',
          empty: { title: '还没有 Schema 变更', hint: '上游 DDL 变化会在这里排队等门禁。' },
          columns: [
            { key: 'change_key', label: '变更键', className: 'mono' },
            { key: 'table_code', label: '源表', className: 'mono' },
            { key: 'kind_label', label: '类型' },
            { key: 'column_name', label: '列', className: 'mono', render: (row) => `${esc(row.column_name || '—')}${row.target_column ? ` → ${esc(row.target_column)}` : ''}` },
            { key: 'compat', label: '兼容性', render: (row) => `<span class="badge ${compatTone(row.compat)}">${esc(row.compat_label)}</span>` },
            { key: 'action', label: '动作', render: (row) => `<b>${esc(row.action_label)}</b>` },
            { key: 'gate_status', label: '门禁', render: (row) => `<span class="badge ${gateTone(row.gate_status)}">${esc(row.gate_status_label)}</span>` },
            { key: 'detected_at', label: '发现时间', render: (row) => fmtTime(row.detected_at) },
          ],
        })}
      </section>`;

    bindFilterBar(view, 'schema-filters', (values) => {
      state.tableId = values.table_id;
      state.compat = values.compat;
      state.gateStatus = values.gate_status;
      schemaPage.render(view, ctx);
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openChange(view, ctx, item);
      });
    });
    view.querySelector('#export-schema')?.addEventListener('click', async () => {
      try {
        await api.download('/api/exports/schema-changes.csv', 'schema-changes.csv');
        toast('已导出 Schema 门禁 CSV');
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
  },
};

export default schemaPage;
