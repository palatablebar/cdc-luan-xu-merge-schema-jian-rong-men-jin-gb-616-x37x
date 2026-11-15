import { api } from '../api.js';
import { ENUMS } from '../config.js';
import { barList, emptyState, errorBox, errorMessage, esc, fmtTime, fmtTimeShort, pct, trendChart, watermarkTrack } from '../ui.js';

function tableCards(tables) {
  if (!tables.length) return emptyState({ title: '还没有源表', hint: '管理员在「源表与订阅」登记第一张表后，事件才能进来。' });
  return `<div class="table-cards">${tables
    .map(
      (table) => `<article class="table-card">
        <header>
          <span class="mono">${esc(table.code)}</span>
          <b>${esc(table.name)}</b>
        </header>
        <div class="tc-body">
          <div><span class="k">事件</span><b>${esc(table.events)}</b></div>
          <div><span class="k">迟到</span><b class="${table.late ? 'warn-text' : ''}">${esc(table.late)}</b></div>
          <div><span class="k">水位线</span><b class="mono small">${esc(fmtTimeShort(table.watermark))}</b></div>
        </div>
        <footer class="mono muted">${esc(table.table_name)} · lateness ${esc(table.lateness)}s</footer>
      </article>`,
    )
    .join('')}</div>`;
}

export const overviewPage = {
  async render(view, ctx) {
    view.innerHTML = '<div class="empty"><div class="mark">读取水位线…</div></div>';
    try {
      const data = await api.get('/api/overview');
      const latest = data.latest_run;
      view.innerHTML = `
        <section class="ov-hero">
          <div class="ov-hero-main">
            <div class="eyebrow">当前批次水位线</div>
            ${
              latest
                ? watermarkTrack({
                    watermark: latest.watermark_at,
                    lateness: data.tables[0] ? data.tables[0].lateness : 30,
                    lateCount: latest.late_count,
                    total: latest.events_in,
                    events: data.tables.map((table) => `${table.code} · ${table.late} 迟到`),
                  })
                : emptyState({ title: '还没有合并批次', hint: '去「合并批次」跑一次全库合并。' })
            }
          </div>
          <div class="ov-hero-side">
            <div class="hero-stat">
              <span class="k">源表 / 订阅</span><b>${esc(data.tables_total)}</b>
              <span class="hint">启用 ${esc(data.tables_enabled)} 张 · MySQL binlog</span>
            </div>
            <div class="hero-stat">
              <span class="k">事件总量</span><b>${esc(data.events_total)}</b>
              <span class="hint">迟到 ${esc(data.late_total)} 条 · ${pct(data.late_ratio)}</span>
            </div>
            <div class="hero-stat">
              <span class="k">拒绝行</span><b>${esc(data.reject_total)}</b>
              <span class="hint">待处置 ${esc(data.reject_pending)} 条 · 重复 ${esc(data.dup_total)}</span>
            </div>
            <div class="hero-stat ${data.gate_breaking ? 'alert' : ''}">
              <span class="k">Schema 门禁</span><b>${esc(data.gate_total)}</b>
              <span class="hint">breaking ${esc(data.gate_breaking)} · 待复核 ${esc(data.gate_pending)}</span>
            </div>
          </div>
        </section>

        <section class="panel ov-row">
          <div class="panel-head"><h2>表级订阅与水位</h2><span class="meta">${esc(data.tables.length)} tables · MySQL binlog</span></div>
          <div class="panel-body">${tableCards(data.tables)}</div>
        </section>

        <div class="grid-3 ov-row">
          <section class="panel">
            <div class="panel-head"><h2>拒绝原因分布</h2><span class="meta">rejects</span></div>
            <div class="panel-body">${
              data.reject_reasons.length
                ? barList(data.reject_reasons, 'warn')
                : emptyState({ title: '还没有拒绝行', hint: '跑一次合并后按原因归类。' })
            }</div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>门禁兼容性</h2><span class="meta">compat</span></div>
            <div class="panel-body">${
              data.compat.length
                ? barList(data.compat.map((item) => ({ ...item, label: ENUMS.compat[item.key] || item.label })), 'danger')
                : emptyState({ title: '还没有 Schema 变更', hint: '上游 DDL 变化会在这里排队。' })
            }</div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>门禁状态</h2><span class="meta">gate</span></div>
            <div class="panel-body">${
              data.gate_status.length
                ? barList(data.gate_status.map((item) => ({ ...item, label: ENUMS.gateStatus[item.key] || item.label })), 'ok')
                : emptyState({ title: '门禁空闲', hint: '暂无待复核变更。' })
            }</div>
          </section>
        </div>

        <div class="grid-2 ov-row">
          <section class="panel">
            <div class="panel-head"><h2>最近合并批次</h2><span class="meta">${esc(data.runs_total)} runs</span></div>
            <div class="panel-body">
              ${
                data.recent_runs.length
                  ? `<ul class="run-list">${data.recent_runs
                      .map(
                        (run) => `<li>
                          <span class="mono">${esc(run.run_no)}</span>
                          <span class="badge ${run.status === 'success' ? 'ok' : 'warn'}">${esc(run.status_label)}</span>
                          <span class="mono muted">事件 ${esc(run.events_in)} · 迟到 ${esc(run.late_count)} · 拒绝 ${esc(run.rejected_count)}</span>
                          <span class="mono muted">${esc(fmtTime(run.started_at))}</span>
                        </li>`,
                      )
                      .join('')}</ul>`
                  : emptyState({ title: '还没有批次', hint: '管理员可在「合并批次」发起一次全库合并。' })
              }
              <div class="legend mono muted">binlog 操作分布：${data.ops
                .map((item) => `${ENUMS.op[item.key] || item.label} ${item.count}`)
                .join(' ｜ ')}</div>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>近 7 天事件与迟到</h2><span class="meta">events / day</span></div>
            <div class="panel-body">
              ${trendChart(data.trend)}
              <div class="legend mono muted">柱高＝当日事件数，柱底红段＝迟到占比；今天 ${esc(fmtTimeShort(data.tables[0] ? data.tables[0].last_ingest : null))} 仍在进事件</div>
            </div>
          </section>
        </div>

        ${
          ctx.user.role === 'admin'
            ? `<section class="panel">
                <div class="panel-head"><h2>最近审计（横切能力）</h2><span class="meta">/api/audit · admin only</span></div>
                <div class="panel-body">
                  <ul class="audit-list">${data.recent_audit
                    .map(
                      (row) => `<li>
                        <span class="mono muted">${esc(fmtTime(row.created_at))}</span>
                        <span class="mono">${esc(row.actor_email)}</span>
                        <span>${esc(row.action_label)}</span>
                        <span class="badge ${row.layer === 'seed' ? 'info' : ''}">${esc(row.entity)}</span>
                      </li>`,
                    )
                    .join('')}</ul>
                  <p class="muted tiny">完整审计查询走 <code>GET /api/audit</code>（viewer / analyst 均返回 403）。</p>
                </div>
              </section>`
            : ''
        }`;
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => overviewPage.render(view, ctx));
    }
  },
};

export default overviewPage;
