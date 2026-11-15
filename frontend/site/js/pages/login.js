import { api } from '../api.js';
import { session } from '../store.js';
import { BRAND } from '../config.js';
import { errorMessage, esc, toast } from '../ui.js';

/** 登录：左侧「信号墙」用纯 CSS 画到达顺序 vs 事件顺序，右侧工牌式登录卡。 */
export const loginPage = {
  async render(root, ctx) {
    root.className = 'login-host';
    root.innerHTML = `
      <div class="login">
        <section class="login-brief">
          <div class="login-top">
            <span class="eyebrow">${esc(BRAND.edition)}</span>
            <span class="eyebrow mono">${esc(BRAND.org)}</span>
          </div>
          <h1>${esc(BRAND.title)}<br /><em>${esc(BRAND.short)}</em></h1>
          <p class="tagline">${esc(BRAND.loginLead)}</p>

          <div class="signal-wall" aria-hidden="true">
            <div class="signal-head mono">
              <span></span><span>到达序</span><span>event_time</span><span>lsn</span>
            </div>
            <div class="signal-row"><i class="sig-dot late"></i><span>01</span><span>10:05</span><span class="mono">LSN 90003</span></div>
            <div class="signal-row"><i class="sig-dot"></i><span>02</span><span>10:00</span><span class="mono">LSN 90001</span></div>
            <div class="signal-row"><i class="sig-dot"></i><span>03</span><span>10:02</span><span class="mono">LSN 90002</span></div>
            <div class="signal-fold">
              <span class="mono">fold by (event_time, lsn)</span>
              <b>10:00 → 10:02 → 10:05</b>
            </div>
            <div class="signal-wm"><span class="mono">watermark</span><i></i><span class="mono muted">max(ingest_at) − 30s</span></div>
          </div>

          <div class="brief-points">
            ${BRAND.loginPoints
              .map(([no, text]) => `<div class="brief-point"><i>${esc(no)}</i><span>${esc(text)}</span></div>`)
              .join('')}
          </div>
        </section>
        <section class="login-form-wrap">
          <form class="login-card" id="login-form" novalidate>
            <div class="login-card-head">
              <span class="eyebrow">内部工牌</span>
              <div class="brand">${esc(BRAND.title)}</div>
              <div class="sub">三个演示账号权限不同：门禁审批与合并只有管理员能做，运维专员可重放拒绝行。</div>
            </div>
            <div class="field">
              <label for="email">邮箱</label>
              <input id="email" name="email" type="email" autocomplete="username" value="admin@${esc(BRAND.short)}.io" required />
            </div>
            <div class="field">
              <label for="password">密码</label>
              <input id="password" name="password" type="password" autocomplete="current-password" value="Demo@2026" required />
            </div>
            <div class="form-error" id="login-error" role="alert"></div>
            <button class="btn accent" id="login-submit" type="submit">进入信号台</button>
            <div class="login-hint">
              演示账号（密码 <code>Demo@2026</code>）：<br />
              <code>admin@${esc(BRAND.short)}.io</code> 管理员 · <code>analyst@${esc(BRAND.short)}.io</code> 运维专员 ·
              <code>viewer@${esc(BRAND.short)}.io</code> 只读
            </div>
          </form>
        </section>
      </div>`;

    const form = root.querySelector('#login-form');
    const errorNode = root.querySelector('#login-error');
    const submit = root.querySelector('#login-submit');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      errorNode.textContent = '';
      submit.disabled = true;
      submit.textContent = '正在验证…';
      try {
        const result = await api.post('/api/auth/login', {
          email: form.email.value.trim(),
          password: form.password.value,
        });
        session.set(result.access_token, result.user);
        toast(`欢迎回来，${result.user.display_name}`);
        location.hash = '#/overview';
        await ctx.reload();
      } catch (err) {
        errorNode.textContent = err.code === 'AUTH_INVALID' ? '账号或密码不正确' : errorMessage(err);
      } finally {
        submit.disabled = false;
        submit.textContent = '进入信号台';
      }
    });
  },
};

export default loginPage;
