import { api } from '../api.js';
import { can, ENUMS, linkTone, reasonTone, replayTone, wideTone } from '../config.js';
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
  metricGrid,
  pct,
  toast,
} from '../ui.js';

const state = { tableId: '', status: '' };

function rejectTable(rejects, editable) {
  if (!rejects.length) return '<p class="muted">这个批次没有拒绝行。</p>';
  return `<div class="table-wrap"><table class="data" data-testid="reject-list" style="min-width:0">
    <thead><tr><th>主键</th><th>原因</th><th>LSN</th><th>事件时间</th><th>处置</th>${editable ? '<th>操作</th>' : ''}</tr></thead>
    <tbody>${rejects
      .map(
        (row) => `<tr data-reject="${row.id}">
          <td class="mono">${esc(row.pk_value)}</td>
          <td><span class="badge ${reasonTone(row.reason)}">${esc(ENUMS.rejectReason[row.reason] || row.reason)}</span></td>
          <td class="mono">${esc(row.event_lsn)}</td>
          <td class="mono">${esc(fmtTimeShort(row.event_time))}</td>
          <td><span class="badge ${replayTone(row.replay_status)}">${esc(row.replay_status_label)}</span></td>
          ${
            editable
              ? `<td class="row-actions">
                  <button class="btn sm ghost" data-act="replayed" data-id="${row.id}">重放</button>
                  <button class="btn sm ghost" data-act="skipped" data-id="${row.id}">跳过</button>
                </td>`
              : ''
          }
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

function perTableRows(metrics) {
  const tables = Object.values(metrics.tables || {});
  if (!tables.length) return '<p class="muted">没有按表拆分的指标。</p>';
  return `<div class="table-wrap"><table class="data" style="min-width:0">
    <thead><tr><th>表</th><th>事件</th><th>upsert</th><th>门禁保留</th><th>已发布</th><th>删除</th><th>迟到</th><th>重复</th><th>拒绝</th></tr></thead>
    <tbody>${tables
      .map(
        (table) => `<tr>
          <td class="mono">${esc(table.table_name)}</td>
          <td class="num">${esc(table.events)}</td>
          <td class="num">${esc(table.upsert_rows)}</td>
          <td class="num ${table.held_rows ? 'warn-text' : ''}">${esc(table.held_rows || 0)}</td>
          <td class="num">${esc(table.published_rows ?? table.upsert_rows)}</td>
          <td class="num">${esc(table.deletes)}</td>
          <td class="num">${esc(table.late_count)}</td>
          <td class="num">${esc(table.dup_skipped)}</td>
          <td class="num">${esc(table.rejected)}</td>
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

function sampleRows(samples) {
  if (!samples || !samples.length) return '<p class="muted">没有可展示的终态样例。</p>';
  return `<div class="table-wrap"><table class="data" data-testid="sample-rows" style="min-width:0">
    <thead><tr><th>表</th><th>主键</th><th>终态字段</th><th>最后事件时间</th><th>LSN</th></tr></thead>
    <tbody>${samples
      .map(
        (row) => `<tr>
          <td class="mono">${esc(row.table)}</td>
          <td class="mono">${esc(row.pk_value)}</td>
          <td class="mono">${esc(Object.entries(row.after || {}).slice(0, 3).map(([k, v]) => `${k}=${v}`).join(' · '))}</td>
          <td class="mono">${esc(fmtTimeShort(row.last_event_time))}</td>
          <td class="mono">${esc(row.last_lsn)}</td>
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

function linkSummaryRows(links) {
  const rows = (links || []).map(
    (row) => `<tr data-link="${row.id}">
      <td><b class="mono">${esc(row.pk_value)}</b><div class="mono muted tiny">${esc(row.table_code || '')}</div></td>
      <td class="mono">${esc(row.parent_table_code || '—')} / ${esc(row.parent_pk || '—')}</td>
      <td><span class="badge ${linkTone(row.status)}">${esc(ENUMS.linkStatus[row.status] || row.status)}</span></td>
      <td class="tiny">${esc(row.basis || row.release_basis || row.withdraw_basis || '—')}</td>
    </tr>`,
  );
  return `<div class="table-wrap"><table class="data" style="min-width:0">
    <thead><tr><th>记录</th><th>父单</th><th>关联状态</th><th>依据</th></tr></thead>
    <tbody>${rows.join('')}</tbody></table></div>`;
}

function batchGateText(batch) {
  const items = Object.values(batch || {});
  if (!items.length) return '本批不涉及从表';
  return items
    .map((row) => `保留 ${row.held ?? 0} / 放行 ${row.released ?? 0} / 撤回 ${row.withdrawn ?? 0}`)
    .join(' ｜ ');
}

function linkGateSection(detail) {
  const gate = detail.link_gate || {};
  const links = detail.links || {};
  const hasAny = (gate.pending_now || 0) + (gate.released_now || 0) + (gate.withdrawn_now || 0) > 0;
  if (!hasAny) {
    return '<p class="muted">本批次不涉及跨表关联（只有父表或未配置父表）。</p>';
  }
  const wide = (detail.wide_rows || []).slice(0, 8);
  return `
    ${metricGrid([
      { label: '待关联（未发布）', value: gate.pending_now ?? 0, hint: '父单未到达，保留待关联', tone: gate.pending_now ? 'warn' : '' },
      { label: '已放行', value: gate.released_now ?? 0, hint: '父单有效，子记录进宽表', tone: 'ok' },
      { label: '已撤回', value: gate.withdrawn_now ?? 0, hint: '父单作废 / 从表删除', tone: gate.withdrawn_now ? 'alert' : '' },
      { label: '宽表已发布', value: gate.wide_published_now ?? 0, hint: '父单行 + 子记录' },
      { label: '宽表已撤回', value: gate.wide_withdrawn_now ?? 0, hint: '子记录清空、指标重算' },
      { label: '本批状态迁移', value: gate.transitions ?? 0, hint: '重复执行同一批应为 0' },
    ])}
    <p class="muted tiny">本批门禁动作：${batchGateText(gate.batch)}；批次级指标与当前累计状态分开核对。</p>
    <h4 class="drawer-h4">待关联（父单未到，不发布）</h4>
    ${(links.pending || []).length ? linkSummaryRows(links.pending) : '<p class="muted">没有待关联记录。</p>'}
    <h4 class="drawer-h4">最近放行 / 撤回依据</h4>
    ${(links.released || []).length ? linkSummaryRows(links.released) : '<p class="muted">没有放行记录。</p>'}
    ${(links.withdrawn || []).length ? linkSummaryRows(links.withdrawn) : ''}
    <h4 class="drawer-h4">宽表最终状态（最近更新）</h4>
    ${wide.length
      ? `<div class="table-wrap"><table class="data" style="min-width:0">
          <thead><tr><th>父单</th><th>状态</th><th>子记录</th><th>依据</th></tr></thead>
          <tbody>${wide
            .map(
              (row) => `<tr>
                <td class="mono">${esc(row.parent_pk)}</td>
                <td><span class="badge ${wideTone(row.status)}">${esc(ENUMS.wideStatus[row.status] || row.status)}</span></td>
                <td class="num">${esc(row.child_count)}</td>
                <td class="tiny">${esc(row.link_basis)}</td>
              </tr>`,
            )
            .join('')}</tbody></table></div>`
      : '<p class="muted">宽表还没有行。</p>'}`;
}

async function openRun(view, ctx, run) {
  const editable = can(ctx.user.role, 'reject:replay');
  let detail;
  try {
    detail = await api.get(`/api/runs/${run.id}/detail`);
  } catch (err) {
    toast(errorMessage(err), 'err');
    return;
  }
  const metrics = detail.run.metrics || {};
  const { root, close } = drawer({
    wide: true,
    title: `合并批次 ${detail.run.run_no}`,
    subtitle: `${detail.run.scope} · ${ENUMS.trigger[detail.run.trigger] || detail.run.trigger} · ${detail.run.operator_email}`,
    body: `
      ${metricGrid([
        { label: '读取事件', value: detail.run.events_in, hint: '批次范围 ' + detail.run.scope },
        { label: 'upsert 行', value: detail.run.upsert_rows, hint: '按 (pk) 折叠后的终态', tone: 'ok' },
        { label: '删除终态', value: detail.run.deleted_rows, hint: '最后一次操作是 d' },
        { label: '迟到事件', value: detail.run.late_count, hint: `迟到率 ${pct(detail.run.late_ratio)}`, tone: 'warn' },
        { label: '重复去重', value: detail.run.dup_skipped, hint: '同 (pk, lsn) 只计首次' },
        { label: '拒绝行', value: detail.run.rejected_count, hint: '重复 / 旧更新 / 类型穿帮', tone: 'alert' },
        { label: '待关联', value: detail.run.link_pending_count, hint: '父单未到，未发布', tone: 'warn' },
        { label: '宽表发布 / 撤回', value: `${detail.run.wide_published_count} / ${detail.run.wide_withdrawn_count}`, hint: '每个父单一行' },
      ])}
      ${kv([
        ['水位线', `<span class="mono">${fmtTimeShort(detail.run.watermark_at)}</span>`],
        ['允许迟到', `${esc(detail.run.allowed_lateness_seconds)} 秒`],
        ['开始 / 结束', `${fmtTime(detail.run.started_at)} → ${fmtTime(detail.run.finished_at)}`],
        ['算法输出摘要', `<span class="mono">${esc(JSON.stringify(metrics.reject_reasons || {}))}</span>`],
      ])}
      <h3 class="drawer-h3">按表拆分（算法 A 逐表折叠）</h3>
      ${perTableRows(metrics)}
      <h3 class="drawer-h3">折叠终态样例（按 event_time + lsn 排序后谁赢）</h3>
      ${sampleRows(metrics.samples)}
      <h3 class="drawer-h3">运行日志</h3>
      <pre class="code">${esc(detail.run.log_text || '（无日志）')}</pre>
      <h3 class="drawer-h3">跨表关联门禁（当前宽表状态）</h3>
      ${linkGateSection(detail)}
      <h3 class="drawer-h3">拒绝行（${esc(detail.rejects.total)} 条）</h3>
      ${rejectTable(detail.rejects.items, editable)}`,
    footer: can(ctx.user.role, 'export:read')
      ? `<div class="drawer-actions">
          <button class="btn ghost" data-export="merge-runs">导出批次 CSV</button>
          <button class="btn ghost" data-export="rejects">导出拒绝行 CSV</button>
        </div>`
      : '<span class="mono muted">只读角色不能导出</span>',
  });

  root.querySelectorAll('[data-act]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await api.patch(`/api/rejects/${button.dataset.id}/replay`, {
          replay_status: button.dataset.act,
          note: `批次 ${detail.run.run_no} 手动处置`,
        });
        toast(button.dataset.act === 'replayed' ? '已标记重放' : '已跳过该拒绝行');
        close();
        await openRun(view, ctx, detail.run);
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
  });
  root.querySelector('[data-export="merge-runs"]')?.addEventListener('click', async () => {
    try {
      await api.download('/api/exports/merge-runs.csv', 'merge-runs.csv');
      toast('已导出合并批次 CSV');
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
  root.querySelector('[data-export="rejects"]')?.addEventListener('click', async () => {
    try {
      await api.download(`/api/exports/rejects.csv?run_id=${detail.run.id}`, 'rejects.csv');
      toast('已导出拒绝行 CSV');
    } catch (err) {
      toast(errorMessage(err), 'err');
    }
  });
}

function openRunForm(view, ctx, tables) {
  const { root, close } = drawer({
    title: '发起合并批次',
    subtitle: '服务端按水位线重新折叠事件，写入指标与拒绝行',
    body: `<form id="run-form" class="form-grid">
      ${field({
        label: '批次范围',
        name: 'table_id',
        options: [{ value: '', label: '全库（3 张表一起折叠）' }, ...tables.map((table) => ({ value: table.id, label: `${table.code} · ${table.name}` }))],
      })}
      ${field({ label: '允许迟到（秒，空则用表配置）', name: 'allowed_lateness_seconds', type: 'number', value: '30', attrs: 'min="0" max="3600"' })}
      ${field({ label: '触发方式', name: 'trigger', value: 'manual', options: [{ value: 'manual', label: '手动' }, { value: 'schedule', label: '定时补偿' }] })}
      <div class="form-error" id="run-error"></div>
      <button class="btn accent" type="submit">开始合并</button>
    </form>`,
  });
  root.querySelector('#run-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    button.textContent = '合并中…';
    try {
      const run = await api.post('/api/runs', {
        table_id: form.table_id.value ? Number(form.table_id.value) : null,
        allowed_lateness_seconds: form.allowed_lateness_seconds.value
          ? Number(form.allowed_lateness_seconds.value)
          : null,
        trigger: form.trigger.value,
      });
      toast(`${run.run_no} 完成：upsert ${run.upsert_rows} / 迟到 ${run.late_count} / 拒绝 ${run.rejected_count}`);
      close();
      await runsPage.render(view, ctx);
    } catch (err) {
      root.querySelector('#run-error').textContent = errorMessage(err);
      button.disabled = false;
      button.textContent = '开始合并';
    }
  });
}

export const runsPage = {
  async render(view, ctx) {
    let runs;
    let tables;
    try {
      [runs, tables] = await Promise.all([
        api.get('/api/runs', { limit: 30, table_id: state.tableId, status: state.status }),
        api.get('/api/tables', { limit: 50 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => runsPage.render(view, ctx));
      return;
    }
    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>合并批次与拒绝行</h2>
          <div class="head-actions">
            <span class="meta">${runs.total} 个批次</span>
            ${can(ctx.user.role, 'run:create') ? '<button class="btn sm accent" id="new-run">发起合并</button>' : ''}
            ${can(ctx.user.role, 'export:read') ? '<button class="btn sm ghost" id="export-runs">导出批次 CSV</button>' : ''}
          </div>
        </div>
        <div class="panel-body">
          ${filterBar({
            id: 'run-filters',
            fields: [
              {
                label: '范围',
                name: 'table_id',
                value: state.tableId,
                options: [{ value: '', label: '全部' }, ...tables.items.map((table) => ({ value: table.id, label: table.code }))],
              },
              {
                label: '状态',
                name: 'status',
                value: state.status,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.runStatus).map(([value, label]) => ({ value, label }))],
              },
            ],
          })}
        </div>
        ${dataTable({
          rows: runs.items,
          testId: 'run-list',
          empty: { title: '还没有合并批次', hint: '管理员可以发起一次全库合并。' },
          columns: [
            { key: 'run_no', label: '批次号', className: 'mono' },
            { key: 'scope', label: '范围' },
            { key: 'status', label: '状态', render: (row) => `<span class="badge ${row.status === 'success' ? 'ok' : 'warn'}">${esc(ENUMS.runStatus[row.status] || row.status)}</span>` },
            { key: 'events_in', label: '事件', className: 'num' },
            { key: 'upsert_rows', label: 'upsert', className: 'num' },
            { key: 'late_count', label: '迟到', className: 'num', render: (row) => (row.late_count ? `<span class="warn-text mono">${row.late_count}</span>` : '0') },
            { key: 'dup_skipped', label: '重复', className: 'num' },
            { key: 'rejected_count', label: '拒绝', className: 'num', render: (row) => (row.rejected_count ? `<span class="danger-text mono">${row.rejected_count}</span>` : '0') },
            { key: 'started_at', label: '开始时间', render: (row) => fmtTime(row.started_at) },
          ],
        })}
      </section>`;

    bindFilterBar(view, 'run-filters', (values) => {
      state.tableId = values.table_id;
      state.status = values.status;
      runsPage.render(view, ctx);
    });
    view.querySelectorAll('table.data tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = runs.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openRun(view, ctx, item);
      });
    });
    view.querySelector('#new-run')?.addEventListener('click', () => openRunForm(view, ctx, tables.items));
    view.querySelector('#export-runs')?.addEventListener('click', async () => {
      try {
        await api.download('/api/exports/merge-runs.csv', 'merge-runs.csv');
        toast('已导出合并批次 CSV');
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
  },
};

export default runsPage;
