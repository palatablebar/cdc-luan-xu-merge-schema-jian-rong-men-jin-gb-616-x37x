/** 无框架 UI 组件：表格 / 抽屉 / 筛选条 / 水位线刻度 / 统计条 / 图表 / toast。
 *  复用约定：tables / events / runs / schema 四个业务页共用 dataTable + filterBar + drawer。 */

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

export function fmtTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return esc(iso);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function fmtTimeShort(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return esc(iso);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

export function badge(tone, text) {
  return `<span class="badge ${esc(tone)}">${esc(text)}</span>`;
}

export function statStrip(items) {
  return `<div class="stat-strip">${items
    .map(
      (item) => `<div class="stat">
        <span class="k">${esc(item.label)}</span>
        <span class="v">${esc(item.value)}</span>
        <span class="hint">${esc(item.hint || '')}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function emptyState({ title, hint, actionText, actionId }) {
  return `<div class="empty" data-testid="empty-state">
    <div class="mark">${esc(title)}</div>
    <p>${esc(hint || '')}</p>
    ${actionText ? `<button class="btn ghost" id="${esc(actionId || 'empty-action')}">${esc(actionText)}</button>` : ''}
  </div>`;
}

export function errorBox(message, retryId = 'retry-btn') {
  return `<div class="error-box"><span>${esc(message)}</span><button class="btn sm ghost" id="${esc(retryId)}">重试</button></div>`;
}

/** 共享业务表格组件：全站 4 个业务页共用（行 id + data 属性 + 可访问 caption）。 */
export function dataTable({ columns, rows, empty, testId = 'data-table' }) {
  if (!rows || rows.length === 0) {
    return emptyState(empty || { title: '暂无记录', hint: '调整筛选条件，或先发起一次合并。' });
  }
  const head = columns.map((col) => `<th>${esc(col.label)}</th>`).join('');
  const body = rows
    .map(
      (row) => `<tr data-id="${esc(row.id)}" tabindex="0">
        ${columns
          .map((col) => `<td class="${esc(col.className || '')}" data-col="${esc(col.key || '')}">${
            col.render ? col.render(row) : esc(row[col.key])
          }</td>`)
          .join('')}
      </tr>`,
    )
    .join('');
  return `<div class="table-wrap"><table class="data" data-testid="${esc(testId)}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** 共享筛选条组件：返回 HTML，调用 bindFilterBar 绑定 change 事件。 */
export function filterBar({ id, fields, actions = '' }) {
  return `<div class="filter-bar" id="${esc(id)}">
    ${fields.map((item) => field(item)).join('')}
    <div class="spacer"></div>
    ${actions}
  </div>`;
}

export function bindFilterBar(root, id, onChange) {
  const bar = root.querySelector(`#${id}`);
  if (!bar) return null;
  const collect = () => {
    const values = {};
    bar.querySelectorAll('[name]').forEach((node) => {
      values[node.name] = node.value;
    });
    return values;
  };
  bar.addEventListener('change', () => onChange(collect()));
  bar.querySelectorAll('input[name]').forEach((input) => {
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') onChange(collect());
    });
  });
  return collect;
}

/** 水位线刻度：把迟到事件画在 watermark 左侧，直观看出「晚了多少」。 */
export function watermarkTrack({ watermark, lateness, lateCount, events, total }) {
  if (!watermark) {
    return `<div class="wm-box"><span class="mono muted">还没有事件，无法计算水位线</span></div>`;
  }
  const late = Math.max(0, Number(lateCount) || 0);
  const totalCount = Math.max(1, Number(total) || 1);
  const share = Math.min(78, Math.max(18, Math.round((late / totalCount) * 100)));
  const dots = (events || []).slice(0, 18);
  return `<div class="wm-box">
    <div class="wm-head">
      <span class="eyebrow">watermark</span>
      <span class="mono">${esc(fmtTimeShort(watermark))}</span>
      <span class="mono muted">lateness ${esc(lateness)}s</span>
      <span class="badge warn">迟到 ${esc(late)} / ${esc(totalCount)} · ${pct(late / totalCount)}</span>
    </div>
    <div class="wm-track">
      <div class="wm-late-zone" style="width:${share}%">
        ${dots
          .map(
            (dot, index) =>
              `<i class="wm-dot" style="left:${10 + index * 4}%;bottom:${8 + (index % 3) * 8}px" title="${esc(dot)}"></i>`,
          )
          .join('')}
        <span class="wm-zone-label">迟到区</span>
      </div>
      <div class="wm-ontime"><span>按时区 · watermark 右侧</span></div>
    </div>
    <div class="wm-foot mono muted">
      watermark = max(ingest_at) − ${esc(lateness)}s ｜ 迟到事件仍参与折叠，但标记并计入批次指标
    </div>
  </div>`;
}

/** 合并批次指标格：算法 A 的真实输出。 */
export function metricGrid(items) {
  return `<div class="metric-grid">${items
    .map(
      (item) => `<div class="metric ${esc(item.tone || '')}">
        <span class="k">${esc(item.label)}</span>
        <b>${esc(item.value)}</b>
        <span class="hint">${esc(item.hint || '')}</span>
      </div>`,
    )
    .join('')}</div>`;
}

/** Schema 列对照：before/after 两列 diff（算法 B 的输入快照）。 */
export function columnDiff(before, after) {
  const beforeMap = new Map((before || []).map((col) => [col.name, col]));
  const afterMap = new Map((after || []).map((col) => [col.name, col]));
  const names = [...new Set([...beforeMap.keys(), ...afterMap.keys()])];
  const rows = names.map((name) => {
    const oldCol = beforeMap.get(name);
    const newCol = afterMap.get(name);
    let state = 'same';
    if (!oldCol) state = 'add';
    else if (!newCol) state = 'drop';
    else if (oldCol.type !== newCol.type) state = 'type';
    const label = { same: '不变', add: '新增', drop: '删除', type: '改类型' }[state];
    return `<tr class="diff-${state}">
      <td class="mono">${esc(name)}</td>
      <td class="mono">${esc(oldCol ? oldCol.type : '—')}</td>
      <td class="mono">${esc(newCol ? newCol.type : '—')}</td>
      <td><span class="badge ${state === 'same' ? 'info' : state === 'add' ? 'ok' : 'danger'}">${label}</span></td>
    </tr>`;
  });
  return `<div class="table-wrap"><table class="data diff-table" style="min-width:0">
    <thead><tr><th>列</th><th>变更前</th><th>变更后</th><th>判定</th></tr></thead>
    <tbody>${rows.join('')}</tbody></table></div>`;
}

export function drawer({ title, subtitle, body, footer, wide = false }) {
  const backdrop = document.createElement('div');
  backdrop.className = 'drawer-backdrop';
  backdrop.innerHTML = `<aside class="drawer ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" data-testid="drawer">
    <div class="drawer-head">
      <div>
        <h2>${esc(title)}</h2>
        <div class="mono muted">${esc(subtitle || '')}</div>
      </div>
      <button class="btn sm ghost" data-close>关闭</button>
    </div>
    <div class="drawer-body">${body}</div>
    ${footer ? `<div class="drawer-foot">${footer}</div>` : ''}
  </aside>`;
  const close = () => {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (event) => {
    if (event.key === 'Escape') close();
  };
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop || event.target.closest('[data-close]')) close();
  });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(backdrop);
  return { root: backdrop, close };
}

export function kv(items) {
  return `<dl class="kv">${items
    .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${value === undefined || value === null ? '—' : value}</dd>`)
    .join('')}</dl>`;
}

export function barList(items, tone = '') {
  const max = Math.max(1, ...items.map((item) => item.count));
  return `<div class="bars">${items
    .map(
      (item) => `<div class="bar-row">
        <span title="${esc(item.label)}">${esc(item.label)}</span>
        <div class="bar-track"><div class="bar-fill ${esc(tone)}" style="width:${Math.round((item.count / max) * 100)}%"></div></div>
        <span class="mono">${esc(item.count)}</span>
      </div>`,
    )
    .join('')}</div>`;
}

export function trendChart(points) {
  const max = Math.max(1, ...points.map((point) => point.events ?? point.runs ?? 0));
  return `<div class="trend">${points
    .map((point) => {
      const value = point.events ?? point.runs ?? 0;
      const late = point.late || 0;
      const latePct = value ? Math.round((late / value) * 100) : 0;
      return `<div class="col" title="${esc(point.date)} · ${esc(value)} 条事件 · 迟到 ${esc(late)}">
        <div class="stem" style="height:${Math.max(2, Math.round((value / max) * 68))}px">
          ${latePct ? `<i class="late-cap" style="height:${latePct}%"></i>` : ''}
        </div>
        <span class="lbl">${esc(point.date.slice(5))}</span>
      </div>`;
    })
    .join('')}</div>`;
}

export function field({ label, name, type = 'text', value = '', placeholder = '', options = null, attrs = '', hint = '' }) {
  const control = options
    ? `<select name="${esc(name)}" id="${esc(name)}" ${attrs}>${options
        .map((opt) => `<option value="${esc(opt.value)}" ${String(opt.value) === String(value) ? 'selected' : ''}>${esc(opt.label)}</option>`)
        .join('')}</select>`
    : `<input name="${esc(name)}" id="${esc(name)}" type="${esc(type)}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${attrs} />`;
  return `<div class="field"><label for="${esc(name)}">${esc(label)}</label>${control}${hint ? `<span class="tiny muted">${esc(hint)}</span>` : ''}</div>`;
}

export function toast(message, tone = '') {
  const stack = document.getElementById('toast-stack');
  const node = document.createElement('div');
  node.className = `toast ${tone}`;
  node.textContent = message;
  stack.appendChild(node);
  setTimeout(() => node.remove(), 3600);
}

export function errorMessage(err) {
  if (!err) return '未知错误';
  if (err.code === 'FORBIDDEN') return '当前角色没有这个操作的权限（后端已返回 403）';
  if (err.code === 'CONFLICT') return '自然键冲突：这条记录已经存在（409）';
  if (err.code === 'RATE_LIMITED') return '请求过于频繁，稍后再试（429）';
  return err.message || '请求失败';
}

export function pct(value, digits = 2) {
  return `${(Number(value || 0) * 100).toFixed(digits)}%`;
}
