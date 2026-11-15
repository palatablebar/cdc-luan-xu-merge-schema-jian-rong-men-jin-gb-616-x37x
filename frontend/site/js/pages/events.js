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

const state = { tableId: '', op: '', lateOnly: '', q: '' };

function lateCell(row) {
  if (!row.is_late) return '<span class="badge ok">按时</span>';
  const seconds = row.late_seconds || 0;
  const human = seconds >= 3600 ? `${(seconds / 3600).toFixed(1)} h` : `${seconds}s`;
  return `<span class="badge warn" title="迟到 ${seconds}s">迟到 −${esc(human)}</span>`;
}

function openEvent(view, ctx, event) {
  const { root } = drawer({
    wide: true,
    title: `${event.pk_value} · LSN ${event.lsn}`,
    subtitle: `${event.table_code || ''} ${event.table_name || ''} · 操作 ${event.op_label} · 到达序号 #${event.seq}`,
    body: `${kv([
      ['事件时间', `<span class="mono">${fmtTimeShort(event.event_time)}</span>`],
      ['到达时间', `<span class="mono">${fmtTimeShort(event.ingest_at)}</span>`],
      ['乱序判定', lateCell(event)],
      ['状态', `<span class="badge ${event.status === 'late' ? 'warn' : 'info'}">${esc(event.status_label)}</span>`],
      ['重复投递', event.is_duplicate ? '<span class="badge danger">是（同 pk+lsn）</span>' : '否'],
    ])}
    <h3 class="drawer-h3">after 镜像</h3>
    <pre class="code">${esc(JSON.stringify(event.after || {}, null, 2))}</pre>
    <h3 class="drawer-h3">before 镜像</h3>
    <pre class="code">${esc(JSON.stringify(event.before || {}, null, 2) || '{}')}</pre>
    <p class="muted tiny">合并只看 (event_time, lsn)；到达顺序仅用于去重与迟到判定。</p>`,
  });
  return root;
}

function openInject(view, ctx, tables) {
  const { root, close } = drawer({
    title: '注入一条乱序事件',
    subtitle: '事件时间早于当前水位线会被判迟到；同 (pk, lsn) 重复提交返回 409',
    body: `<form id="inject-form" class="form-grid">
      ${field({
        label: '目标源表',
        name: 'table_id',
        options: tables.map((table) => ({ value: table.id, label: `${table.code} · ${table.name}` })),
      })}
      ${field({ label: '主键值', name: 'pk_value', placeholder: 'POL-100000' })}
      ${field({ label: 'LSN', name: 'lsn', type: 'number', placeholder: '900001', attrs: 'min="1"' })}
      ${field({ label: '操作', name: 'op', value: 'u', options: [{ value: 'u', label: 'u 更新' }, { value: 'c', label: 'c 插入' }, { value: 'd', label: 'd 删除' }] })}
      ${field({ label: '事件时间（UTC，可空）', name: 'event_time', placeholder: '2026-11-14T02:00:00' })}
      <label class="field"><span>after JSON</span>
        <textarea name="after" rows="4" class="mono">{"status":"待核保"}</textarea>
      </label>
      <div class="form-error" id="inject-error"></div>
      <button class="btn accent" type="submit">写入事件</button>
    </form>`,
  });
  root.querySelector('#inject-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    let after;
    try {
      after = JSON.parse(form.after.value || '{}');
    } catch {
      root.querySelector('#inject-error').textContent = 'after 不是合法 JSON';
      return;
    }
    try {
      const created = await api.post('/api/events', {
        table_id: Number(form.table_id.value),
        pk_value: form.pk_value.value.trim(),
        lsn: Number(form.lsn.value),
        op: form.op.value,
        event_time: form.event_time.value.trim() || null,
        after,
      });
      toast(created.is_late ? `已写入，判定迟到 ${created.late_seconds}s` : '已写入，判定按时');
      close();
      await eventsPage.render(view, ctx);
    } catch (err) {
      root.querySelector('#inject-error').textContent =
        err.code === 'CONFLICT' ? '该 (主键, LSN) 已存在（409）' : errorMessage(err);
    }
  });
}

export const eventsPage = {
  async render(view, ctx) {
    let data;
    let tables;
    try {
      [data, tables] = await Promise.all([
        api.get('/api/events', {
          limit: 40,
          table_id: state.tableId,
          op: state.op,
          late_only: state.lateOnly === 'true',
          q: state.q,
        }),
        api.get('/api/tables', { limit: 50 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => eventsPage.render(view, ctx));
      return;
    }
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>事件流 · 乱序标注</h2>
          <div class="head-actions">
            <span class="meta">${data.total} 条 · 最新在前</span>
            ${can(ctx.user.role, 'event:ingest') ? '<button class="btn sm accent" id="inject-event">注入乱序事件</button>' : ''}
          </div>
        </div>
        <div class="panel-body">
          ${filterBar({
            id: 'event-filters',
            fields: [
              {
                label: '源表',
                name: 'table_id',
                value: state.tableId,
                options: [{ value: '', label: '全部表' }, ...tables.items.map((table) => ({ value: table.id, label: table.code }))],
              },
              {
                label: '操作',
                name: 'op',
                value: state.op,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.op).map(([value, label]) => ({ value, label }))],
              },
              {
                label: '乱序',
                name: 'late_only',
                value: state.lateOnly,
                options: [{ value: '', label: '全部' }, { value: 'true', label: '仅迟到' }],
              },
              { label: '主键搜索', name: 'q', value: state.q, placeholder: 'POL-100000' },
            ],
          })}
        </div>
        ${dataTable({
          rows: data.items,
          testId: 'event-list',
          empty: { title: '这个筛选下没有事件', hint: '换个源表或清空「仅迟到」再看。' },
          columns: [
            { key: 'seq', label: '到达序', className: 'num' },
            { key: 'pk_value', label: '主键', render: (row) => `<b class="mono">${esc(row.pk_value)}</b><div class="mono muted">${esc(row.table_code || '')}</div>` },
            { key: 'op', label: '操作', render: (row) => `<span class="badge ${row.op === 'd' ? 'danger' : row.op === 'c' ? 'ok' : 'info'}">${esc(ENUMS.op[row.op] || row.op)}</span>` },
            { key: 'event_time', label: '事件时间', className: 'mono', render: (row) => fmtTimeShort(row.event_time) },
            { key: 'ingest_at', label: '到达时间', className: 'mono', render: (row) => fmtTimeShort(row.ingest_at) },
            { key: 'is_late', label: '乱序', render: lateCell },
            { key: 'lsn', label: 'LSN', className: 'mono' },
          ],
        })}
      </section>`;

    bindFilterBar(view, 'event-filters', (values) => {
      state.tableId = values.table_id;
      state.op = values.op;
      state.lateOnly = values.late_only;
      state.q = values.q.trim();
      eventsPage.render(view, ctx);
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = data.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openEvent(view, ctx, item);
      });
    });
    view.querySelector('#inject-event')?.addEventListener('click', () => openInject(view, ctx, tables.items));
  },
};

export default eventsPage;
