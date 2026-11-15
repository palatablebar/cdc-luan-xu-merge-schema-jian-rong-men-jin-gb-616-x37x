import { api } from '../api.js';
import { can, ENUMS, linkTone, wideTone } from '../config.js';
import {
  badge,
  bindFilterBar,
  dataTable,
  drawer,
  errorBox,
  errorMessage,
  esc,
  field,
  filterBar,
  fmtTimeShort,
  kv,
  metricGrid,
  toast,
} from '../ui.js';

const state = { status: '', linkTable: '', linkReason: '', wideStatus: '', wideTable: '', q: '' };

function gateCards(data) {
  return metricGrid([
    {
      label: '待关联（未发布）',
      value: data.pending,
      hint: '父单未到达，保留不发布',
      tone: data.pending ? 'warn' : '',
    },
    {
      label: '已放行（已发布）',
      value: data.released,
      hint: '父单有效，子记录已进宽表',
      tone: 'ok',
    },
    {
      label: '已撤回（父单作废）',
      value: data.withdrawn,
      hint: '父单删除 / 状态作废后撤回',
      tone: data.withdrawn ? 'alert' : '',
    },
    {
      label: '宽表：已发布',
      value: data.wide_published,
      hint: `${data.wide_total} 个父单宽表行`,
      tone: 'ok',
    },
    {
      label: '宽表：已撤回',
      value: data.wide_withdrawn,
      hint: '子记录已清空，指标重算',
    },
    {
      label: '门禁不变量',
      value: data.checks.ok ? '通过' : '异常',
      hint: data.checks.ok
        ? '有案必有单：无孤儿从记录'
        : `孤儿从记录 ${data.checks.orphan_released} · 游离子行 ${data.checks.stray_children}`,
      tone: data.checks.ok ? 'ok' : 'alert',
    },
  ]);
}

function childTable(children) {
  return `<div class="table-wrap"><table class="data" data-testid="child-summary" style="min-width:0">
    <thead><tr><th>从表</th><th>父表</th><th>外键列</th><th>作废状态</th><th>待关联</th><th>已放行</th><th>已撤回</th><th>合计</th></tr></thead>
    <tbody>${children
      .map(
        (child) => `<tr>
          <td class="mono">${esc(child.table_code)}<div class="muted tiny">${esc(child.table_name)}</div></td>
          <td class="mono">${esc(child.parent_table_code)}</td>
          <td class="mono">${esc(child.parent_key_column || '—')}</td>
          <td>${(child.void_statuses || []).map((item) => badge('danger', item)).join(' ') || '<span class="muted">—</span>'}</td>
          <td class="num ${child.pending ? 'warn-text' : ''}">${esc(child.pending)}</td>
          <td class="num">${esc(child.released)}</td>
          <td class="num ${child.withdrawn ? 'danger-text' : ''}">${esc(child.withdrawn)}</td>
          <td class="num">${esc(child.total)}</td>
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

function linkTable(rows, testId) {
  return `<div class="table-wrap"><table class="data" data-testid="${esc(testId)}" style="min-width:0">
    <thead><tr><th>记录</th><th>父单</th><th>判定原因</th><th>放行 / 撤回依据</th><th>批次</th><th>更新时间</th></tr></thead>
    <tbody>${rows
      .map(
        (row) => `<tr>
          <td><b class="mono">${esc(row.pk_value)}</b><div class="mono muted tiny">${esc(row.table_code || '')}</div></td>
          <td class="mono">${esc(row.parent_table_code || '—')} / ${esc(row.parent_pk || '—')}</td>
          <td>${badge(row.status === 'released' ? 'ok' : linkTone(row.status), ENUMS.linkReason[row.reason] || row.reason_label || row.reason)}</td>
          <td class="tiny">${esc(row.basis || row.release_basis || row.withdraw_basis || '—')}</td>
          <td class="mono tiny">${esc(row.released_run_id || row.first_seen_run_id || '—')}</td>
          <td class="mono tiny">${esc(fmtTimeShort(row.updated_at))}</td>
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

function wideTable(rows, testId) {
  return `<div class="table-wrap"><table class="data" data-testid="${esc(testId)}">
    <thead><tr><th>父单</th><th>宽表状态</th><th>保单状态</th><th>关联依据</th><th>子记录</th><th>来源批次</th><th>更新时间</th></tr></thead>
    <tbody>${rows
      .map(
        (row) => `<tr>
          <td><b class="mono">${esc(row.parent_pk)}</b><div class="mono muted tiny">${esc(row.parent_table_code)}</div></td>
          <td>${badge(wideTone(row.status), ENUMS.wideStatus[row.status] || row.status_label)}</td>
          <td class="mono tiny">${esc(row.parent_row.status || '—')}</td>
          <td class="tiny">${esc({ linked: '有关联子记录', no_child: '暂无子记录', parent_void: '父单作废撤回' }[row.link_basis] || row.link_basis)}</td>
          <td class="mono">${esc(row.child_count)}${
            (row.child_rows || []).length
              ? `<div class="muted tiny">${esc((row.child_rows || []).slice(0, 3).map((item) => item.pk_value).join(' · '))}</div>`
              : ''
          }</td>
          <td class="mono tiny">${esc(row.source_run_id || '—')}</td>
          <td class="mono tiny">${esc(fmtTimeShort(row.updated_at))}</td>
        </tr>`,
      )
      .join('')}</tbody></table></div>`;
}

async function openLink(row) {
  let detail = row;
  try {
    detail = await api.get(`/api/link-gate/links/${row.id}`);
  } catch (err) {
    toast(errorMessage(err), 'err');
    return;
  }
  const { root } = drawer({
    wide: true,
    title: `${detail.pk_value} · ${ENUMS.linkStatus[detail.status] || detail.status}`,
    subtitle: `${detail.table_code || ''} → 父单 ${detail.parent_table_code || '—'} / ${detail.parent_pk || '—'}`,
    body: `
      ${kv([
        ['关联状态', badge(linkTone(detail.status), ENUMS.linkStatus[detail.status] || detail.status_label)],
        ['判定原因', esc(ENUMS.linkReason[detail.reason] || detail.reason_label)],
        ['外键列', `<span class="mono">${esc(detail.parent_key_column || '—')}</span> = <span class="mono">${esc(detail.parent_pk || '—')}</span>`],
        ['首次保留批次', `<span class="mono">${esc(detail.first_seen_run_id || '—')}</span>`],
        ['放行批次', `<span class="mono">${esc(detail.released_run_id || '—')}</span>`],
        ['保留依据', esc(detail.hold_basis || '—')],
        ['放行依据', esc(detail.release_basis || '—')],
        ['撤回依据', esc(detail.withdraw_basis || '—')],
        ['保留时间 / 放行时间', `${fmtTimeShort(detail.held_at)} → ${fmtTimeShort(detail.released_at)}`],
        ['事件时间 / LSN', `${fmtTimeShort(detail.event_time)} · <span class="mono">${esc(detail.event_lsn)}</span>`],
      ])}
      <h3 class="drawer-h3">父单快照（放行时的有效状态）</h3>
      <pre class="code">${esc(JSON.stringify(detail.parent_snapshot || {}, null, 2))}</pre>
      <h3 class="drawer-h3">从表折叠终态（门禁保留的 payload）</h3>
      <pre class="code">${esc(JSON.stringify(detail.payload || {}, null, 2))}</pre>
      <h3 class="drawer-h3">最终宽表状态</h3>
      ${
        detail.wide_row
          ? kv([
              ['宽表状态', badge(wideTone(detail.wide_row.status), ENUMS.wideStatus[detail.wide_row.status] || detail.wide_row.status)],
              ['子记录数', esc(detail.wide_row.child_count)],
              ['来源批次', esc(detail.wide_row.source_run_id || '—')],
            ]) + `<pre class="code">${esc(JSON.stringify(detail.wide_row.child_rows || [], null, 2))}</pre>`
          : '<p class="muted">父单尚无宽表行（父单未到达时不发布）。</p>'
      }`,
  });
  return root;
}

async function openWide(row) {
  const { root } = drawer({
    wide: true,
    title: `${row.parent_pk} · 宽表行`,
    subtitle: `${row.parent_table_code} · ${ENUMS.wideStatus[row.status] || row.status}`,
    body: `
      ${kv([
        ['宽表状态', badge(wideTone(row.status), ENUMS.wideStatus[row.status] || row.status_label)],
        ['关联依据', esc(row.link_basis)],
        ['子记录数', esc(row.child_count)],
        ['来源批次', esc(row.source_run_id || '—')],
        ['发布时间', fmtTimeShort(row.published_at)],
      ])}
      <h3 class="drawer-h3">父单终态</h3>
      <pre class="code">${esc(JSON.stringify(row.parent_row || {}, null, 2))}</pre>
      <h3 class="drawer-h3">子记录（仅放行的关联）</h3>
      <pre class="code">${esc(JSON.stringify(row.child_rows || [], null, 2))}</pre>`,
  });
  return root;
}

export const linkPage = {
  async render(view, ctx) {
    let data;
    let links;
    let wide;
    let tables;
    try {
      [data, links, wide, tables] = await Promise.all([
        api.get('/api/link-gate'),
        api.get('/api/link-gate/links', {
          limit: 25,
          status: state.status,
          table_id: state.linkTable,
          reason: state.linkReason,
          q: state.q,
        }),
        api.get('/api/link-gate/wide-rows', { limit: 25, status: state.wideStatus, parent_table_code: state.wideTable }),
        api.get('/api/tables', { limit: 50 }),
      ]);
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => linkPage.render(view, ctx));
      return;
    }

    view.innerHTML = `
      <section class="panel">
        <div class="panel-head">
          <h2>跨表关联门禁</h2>
          <div class="head-actions">
            <span class="meta">父单未到先保留 · 父单到达自动放行 · 父单作废撤回</span>
            ${can(ctx.user.role, 'link:recompute') ? '<button class="btn sm accent" id="recompute">重算关联门禁</button>' : ''}
            ${can(ctx.user.role, 'export:read') ? '<button class="btn sm ghost" id="export-links">导出关联清单</button><button class="btn sm ghost" id="export-wide">导出宽表状态</button>' : ''}
          </div>
        </div>
        <div class="panel-body">${gateCards(data)}</div>
      </section>

      <section class="panel ov-row">
        <div class="panel-head"><h2>从表门禁分布</h2><span class="meta">等父单不是失败，是不发布</span></div>
        <div class="panel-body">
          ${
            data.children.length
              ? childTable(data.children)
              : '<p class="muted">还没有配置父表关联的从表。管理员可在「源表与订阅」里给从表登记父表。</p>'
          }
          <div class="legend mono muted">判定原因：${
            data.reasons.length
              ? data.reasons.map((item) => `${esc(item.label)} ${esc(item.count)}`).join(' ｜ ')
              : '—'
          }</div>
        </div>
      </section>

      <section class="panel ov-row">
        <div class="panel-head"><h2>关联记录</h2><span class="meta">${esc(links.total)} 条 · 待关联 / 放行 / 撤回</span></div>
        <div class="panel-body">
          ${filterBar({
            id: 'link-filters',
            fields: [
              {
                label: '关联状态',
                name: 'status',
                value: state.status,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.linkStatus).map(([value, label]) => ({ value, label }))],
              },
              {
                label: '从表',
                name: 'table_id',
                value: state.linkTable,
                options: [{ value: '', label: '全部' }, ...tables.items.map((table) => ({ value: table.id, label: table.code }))],
              },
              {
                label: '判定原因',
                name: 'reason',
                value: state.linkReason,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.linkReason).map(([value, label]) => ({ value, label }))],
              },
              { label: '搜索', name: 'q', value: state.q, placeholder: '主键 / 父单号' },
            ],
          })}
        </div>
        ${
          links.items.length
            ? linkTable(links.items, 'link-list')
            : '<div class="panel-body"><div class="empty"><div class="mark">没有匹配的关联记录</div><p>换个筛选条件，或先跑一次全库合并。</p></div></div>'
        }
      </section>

      <section class="panel ov-row">
        <div class="panel-head"><h2>宽表最终状态</h2><span class="meta">${esc(wide.total)} 行 · 只发布有父单的子记录</span></div>
        <div class="panel-body">
          ${filterBar({
            id: 'wide-filters',
            fields: [
              {
                label: '宽表状态',
                name: 'status',
                value: state.wideStatus,
                options: [{ value: '', label: '全部' }, ...Object.entries(ENUMS.wideStatus).map(([value, label]) => ({ value, label }))],
              },
              {
                label: '父表',
                name: 'parent_table_code',
                value: state.wideTable,
                options: [{ value: '', label: '全部' }, ...tables.items.map((table) => ({ value: table.code, label: table.code }))],
              },
            ],
          })}
        </div>
        ${
          wide.items.length
            ? wideTable(wide.items, 'wide-list')
            : '<div class="panel-body"><div class="empty"><div class="mark">宽表还没有行</div><p>父单到达且关联放行后，宽表才会出现对应父单行。</p></div></div>'
        }
      </section>`;

    bindFilterBar(view, 'link-filters', (values) => {
      state.status = values.status;
      state.linkTable = values.table_id;
      state.linkReason = values.reason;
      state.q = values.q.trim();
      linkPage.render(view, ctx);
    });
    bindFilterBar(view, 'wide-filters', (values) => {
      state.wideStatus = values.status;
      state.wideTable = values.parent_table_code;
      linkPage.render(view, ctx);
    });
    view.querySelectorAll('[data-testid="link-list"] tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = links.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openLink(item);
      });
    });
    view.querySelectorAll('[data-testid="wide-list"] tbody tr').forEach((row) => {
      row.addEventListener('click', () => {
        const item = wide.items.find((entry) => String(entry.id) === row.dataset.id);
        if (item) openWide(item);
      });
    });
    view.querySelector('#recompute')?.addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = '重算中…';
      try {
        const result = await api.post('/api/link-gate/reconcile');
        const summary = result.summary || {};
        toast(
          `重算完成：待关联 ${summary.pending} · 放行 ${summary.released} · 撤回 ${summary.withdrawn} · 状态迁移 ${summary.transitions}`,
          summary.transitions ? '' : 'ok',
        );
        await linkPage.render(view, ctx);
      } catch (err) {
        toast(errorMessage(err), 'err');
        button.disabled = false;
        button.textContent = '重算关联门禁';
      }
    });
    view.querySelector('#export-links')?.addEventListener('click', async () => {
      try {
        await api.download(`/api/exports/link-rows.csv${state.status ? `?status=${state.status}` : ''}`, 'link-rows.csv');
        toast('已导出关联门禁清单');
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
    view.querySelector('#export-wide')?.addEventListener('click', async () => {
      try {
        await api.download(`/api/exports/wide-rows.csv${state.wideStatus ? `?status=${state.wideStatus}` : ''}`, 'wide-rows.csv');
        toast('已导出宽表状态');
      } catch (err) {
        toast(errorMessage(err), 'err');
      }
    });
  },
};

export default linkPage;
