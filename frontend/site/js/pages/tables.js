import { api } from '../api.js';
import { can, ENUMS } from '../config.js';
import {
  bindFilterBar,
  dataTable,
  drawer,
  errorBox,
  errorMessage,
  esc,
  field,
  filterBar,
  fmtTime,
  fmtTimeShort,
  kv,
  toast,
} from '../ui.js';

const state = { q: '', enabled: '' };

function schemaTable(columns) {
  if (!columns || !columns.length) return '<p class="muted">这张表还没有列定义。</p>';
  return `<div class="table-wrap"><table class="data" style="min-width:0">
    <thead><tr><th>列</th><th>类型</th><th>可空</th><th>默认</th></tr></thead>
    <tbody>${columns
      .map(
        (col) => `<tr>
          <td class="mono">${esc(col.name)}</td>
          <td class="mono">${esc(col.type)}</td>
          <td>${col.nullable === false ? '<span class="badge danger">NOT NULL</span>' : '<span class="badge info">NULL</span>'}</td>
          <td class="mono">${esc(col.default || '—')}</td>
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

function openTable(view, ctx, table) {
  const editable = can(ctx.user.role, 'table:write');
  const { root, close } = drawer({
    wide: true,
    title: table.name,
    subtitle: `${table.code} · ${table.database_name}.${table.table_name} · PK ${table.pk_column}`,
    body: `${kv([
      ['源表', `<span class="mono">${esc(table.database_name)}.${esc(table.table_name)}</span>`],
      ['主键列', `<span class="mono">${esc(table.pk_column)}</span>`],
      ['binlog 位点', `<span class="mono">${esc(table.binlog_file || '—')} : ${esc(table.binlog_position)}</span>`],
      ['允许迟到', `${esc(table.allowed_lateness_seconds)} 秒`],
      ['状态', table.enabled ? '<span class="badge ok">订阅中</span>' : '<span class="badge">已暂停</span>'],
      ['事件 / 迟到', `${esc(table.event_count)} / ${esc(table.late_count)}`],
      ['最近事件', fmtTime(table.last_event_at)],
      ['水位线', `<span class="mono">${esc(fmtTimeShort(table.last_event_at))} − ${esc(table.allowed_lateness_seconds)}s</span>`],
      ['负责人', esc(table.owner)],
      ['备注', esc(table.note || '—')],
    ])}
    <h3 class="drawer-h3">列定义（${esc(table.column_count)} 列）</h3>
    ${schemaTable(table.schema_columns)}`,
    footer: editable
      ? `<form id="table-edit" class="inline-form">
          ${field({ label: '允许迟到（秒）', name: 'lateness', type: 'number', value: table.allowed_lateness_seconds, attrs: 'min="0" max="3600"' })}
          <button class="btn accent" type="submit">保存水位参数</button>
          <button class="btn ghost" type="button" data-toggle>${table.enabled ? '暂停订阅' : '恢复订阅'}</button>
        </form>`
      : '<span class="mono muted">只读角色仅可查看</span>',
  });
  root.querySelector('#table-edit')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api.patch(`/api/tables/${table.id}`, { allowed_lateness_seconds: Number(event.target.lateness.value) });
      toast('水位参数已更新');
      close();
      await tablesPage.render(view, ctx);
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
  root.querySelector('[data-toggle]')?.addEventListener('click', async () => {
    try {
      await api.patch(`/api/tables/${table.id}`, { enabled: !table.enabled });
      toast(table.enabled ? '已暂停该表订阅' : '已恢复订阅');
      close();
      await tablesPage.render(view, ctx);
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
}

function openCreate(view, ctx) {
  const { root, close } = drawer({
    title: '登记源表订阅',
    subtitle: '登记后事件才会被合并批次读取；编码重复会返回 409',
    body: `<form id="table-form" class="form-grid">
      ${field({ label: '编码（大写字母/数字/下划线）', name: 'code', placeholder: 'T_CLAIM_DETAIL' })}
      ${field({ label: '显示名', name: 'name', placeholder: '例如：理赔明细表' })}
      ${field({ label: '库名', name: 'database_name', value: 'biz_prod' })}
      ${field({ label: '表名', name: 'table_name', placeholder: 't_claim_detail' })}
      ${field({ label: '主键列', name: 'pk_column', value: 'id' })}
      ${field({ label: '允许迟到（秒）', name: 'allowed_lateness_seconds', type: 'number', value: '30', attrs: 'min="0" max="3600"' })}
      ${field({ label: 'binlog 文件', name: 'binlog_file', placeholder: 'mysql-bin.000418' })}
      <label class="field"><span>列定义（JSON 数组）</span>
        <textarea name="schema_columns" rows="6" class="mono">[{"name":"id","type":"bigint","nullable":false},{"name":"status","type":"varchar(16)","nullable":true}]</textarea>
      </label>
      <div class="form-error" id="table-error"></div>
      <button class="btn accent" type="submit">保存订阅</button>
    </form>`,
  });
  root.querySelector('#table-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    let columns;
    try {
      columns = JSON.parse(form.schema_columns.value || '[]');
    } catch {
      root.querySelector('#table-error').textContent = '列定义不是合法 JSON';
      return;
    }
    try {
      const created = await api.post('/api/tables', {
        code: form.code.value.trim(),
        name: form.name.value.trim(),
        database_name: form.database_name.value.trim() || 'biz_prod',
        table_name: form.table_name.value.trim(),
        pk_column: form.pk_column.value.trim() || 'id',
        allowed_lateness_seconds: Number(form.allowed_lateness_seconds.value || 30),
        binlog_file: form.binlog_file.value.trim() || null,
        schema_columns: columns,
      });
      toast(`已登记 ${created.code}`);
      close();
      await tablesPage.render(view, ctx);
    } catch (err) {
      root.querySelector('#table-error').textContent =
        err.code === 'CONFLICT' ? '编码已存在（409），换一个再提交' : errorMessage(err);
    }
  });
}

export const tablesPage = {
  async render(view, ctx) {
    let data;
    try {
      data = await api.get('/api/tables', { limit: 50, q: state.q, enabled: state.enabled });
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => tablesPage.render(view, ctx));
      return;
    }
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>源表与订阅</h2>
          <div class="head-actions">
            <span class="meta">${data.total} 张</span>
            ${can(ctx.user.role, 'table:write') ? '<button class="btn sm accent" id="new-table">登记源表</button>' : ''}
          </div>
        </div>
        <div class="panel-body">
          ${filterBar({
            id: 'table-filters',
            fields: [
              { label: '搜索', name: 'q', value: state.q, placeholder: '表名 / 编码 / 表' },
              {
                label: '订阅状态',
                name: 'enabled',
                value: state.enabled,
                options: [
                  { value: '', label: '全部' },
                  { value: 'true', label: '订阅中' },
                  { value: 'false', label: '已暂停' },
                ],
              },
            ],
          })}
        </div>
        ${dataTable({
          rows: data.items,
          testId: 'table-list',
          empty: { title: '还没有源表', hint: '管理员可以登记第一张被订阅的 MySQL 表。' },
          columns: [
            { key: 'code', label: '编码', className: 'mono' },
            { key: 'name', label: '源表', render: (row) => `<b>${esc(row.name)}</b><div class="mono muted">${esc(row.database_name)}.${esc(row.table_name)}</div>` },
            { key: 'pk_column', label: '主键', className: 'mono' },
            { key: 'allowed_lateness_seconds', label: '允许迟到', className: 'num', render: (row) => `${row.allowed_lateness_seconds}s` },
            { key: 'event_count', label: '事件', className: 'num' },
            { key: 'late_count', label: '迟到', className: 'num', render: (row) => (row.late_count ? `<span class="warn-text mono">${row.late_count}</span>` : '0') },
            { key: 'last_event_at', label: '最近事件', render: (row) => fmtTime(row.last_event_at) },
            { key: 'enabled', label: '状态', render: (row) => (row.enabled ? '<span class="badge ok">订阅中</span>' : '<span class="badge">已暂停</span>') },
          ],
        })}
      </section>`;

    bindFilterBar(view, 'table-filters', (values) => {
      state.q = values.q.trim();
      state.enabled = values.enabled;
      tablesPage.render(view, ctx);
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openTable(view, ctx, item);
      });
    });
    view.querySelector('#new-table')?.addEventListener('click', () => openCreate(view, ctx));
  },
};

export default tablesPage;
