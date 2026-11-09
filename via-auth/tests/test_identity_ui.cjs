'use strict';

// Runs the shipped auth UI in a fresh, temporary browser context. Every HTTP
// request is fulfilled locally: this test never connects to a manager/profile.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../chrome-manager/frontend/node_modules/playwright');
const staticRoot = path.resolve(__dirname, '../via_auth/static');
const dashboardRoot = path.resolve(__dirname, '../../meta-page-manager/frontend');
const origin = 'http://127.0.0.1:8000';
const fixturePassword = 'fixture-password-1234';
const newPassword = 'fixture-new-password-5678';
const temporaryPassword = 'fixture-temporary-password-9012';

const user = (id, username, role = 'operator', mustChange = false) => ({
  id, username, role, active: true, revision: 1, must_change_password: mustChange,
});

async function fixture(browser, actor = null, app = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const state = {
    actor,
    csrf: 'fixture-csrf-first',
    users: [user(1, 'admin', 'admin'), user(2, 'operator1'), user(3, 'operator2')],
    writes: [],
    errors: [],
    app,
    tokens: [],
    issuedTokens: [],
  };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    assert.equal(url.origin, origin, 'fixture must not contact external origins');
    const pathname = url.pathname;
    if (pathname.startsWith('/auth/api/')) {
      const endpoint = pathname.slice('/auth/api/'.length);
      const method = request.method();
      const body = method === 'GET' ? null : request.postDataJSON();
      if (method !== 'GET') {
        state.writes.push({ endpoint, method, body, csrf: request.headers()['x-via-csrf'] });
        if (endpoint !== 'login') assert.equal(request.headers()['x-via-csrf'], state.csrf, 'mutation sends the current CSRF token');
      }
      if (endpoint === 'me') {
        return route.fulfill({ status: state.actor ? 200 : 401,
          json: state.actor ? { user: state.actor, csrf_token: state.csrf, app: state.app } : { detail: 'Đăng nhập để tiếp tục.' } });
      }
      if (endpoint === 'login') {
        if (body.password !== fixturePassword) return route.fulfill({ status: 401, json: { detail: 'Tên đăng nhập hoặc mật khẩu chưa đúng.' } });
        state.actor = state.users.find(item => item.username === body.username) || user(2, body.username);
        return route.fulfill({ json: { user: state.actor } });
      }
      if (endpoint === 'password') {
        assert.deepEqual(body, { current_password: fixturePassword, new_password: newPassword });
        state.actor = { ...state.actor, must_change_password: false, revision: state.actor.revision + 1 };
        state.csrf = 'fixture-csrf-after-password-change';
        return route.fulfill({ json: { ok: true } });
      }
      if (endpoint === 'logout') {
        state.actor = null;
        return route.fulfill({ json: { ok: true } });
      }
      if (endpoint === 'users') return route.fulfill({ json: { users: state.users } });
      if (endpoint === 'mcp-tokens' && method === 'GET') {
        return route.fulfill({ json: { tokens: state.tokens, mcp_url: state.app.mcp_url } });
      }
      if (endpoint === 'mcp-tokens' && method === 'POST') {
        assert.ok(state.actor && !state.actor.must_change_password);
        const item = { id: '00000000-0000-4000-8000-' + String(state.actor.id).padStart(12, '0'),
          name: body.name, scopes: body.scopes, audience: state.app.mcp_url,
          expires_at: Math.floor(Date.now() / 1000) + body.days * 86400,
          created_at: Math.floor(Date.now() / 1000), revoked_at: null, last_used_at: null, valid_revision: true };
        const token = 'via_pat_' + String(state.actor.id).repeat(43);
        state.tokens.push(item);
        state.issuedTokens.push(token);
        return route.fulfill({ status: 201, json: { ...item, token } });
      }
      if (endpoint.startsWith('mcp-tokens/') && method === 'DELETE') {
        const token = state.tokens.find(item => item.id === endpoint.split('/')[1]);
        assert.ok(token, 'revoke refers to an owned token');
        token.revoked_at = Math.floor(Date.now() / 1000);
        return route.fulfill({ json: { ok: true } });
      }
      if (endpoint === 'audit') return route.fulfill({ json: { total: 1, items: [{
        at: 1_800_000_000, actor: '<img src=x onerror="window.identityXss=true">',
        action: 'POST', route: '/api/fixture', status: 200,
      }] } });
      if (endpoint.startsWith('users/') && method === 'PATCH') {
        const target = state.users.find(item => item.id === Number(endpoint.split('/')[1]));
        assert.ok(target);
        assert.equal(body.revision, target.revision);
        assert.notEqual(target.id, state.actor.id, 'self-password reset must not be offered by UI');
        Object.assign(target, { ...body, revision: target.revision + 1 });
        if (body.reset_password) target.must_change_password = true;
        return route.fulfill({ json: { user: target, ...(body.reset_password ? { temporary_password: temporaryPassword } : {}) } });
      }
      throw new Error(`Unexpected fixture API: ${method} ${pathname}`);
    }
    if (pathname === '/auth/static/app.js' || pathname === '/auth/static/style.css') {
      return route.fulfill({ path: path.join(staticRoot, path.basename(pathname)),
        contentType: pathname.endsWith('.js') ? 'application/javascript' : 'text/css' });
    }
    if (pathname === '/auth/static/fonts.css') {
      return route.fulfill({ path: path.join(dashboardRoot, 'fonts.css'), contentType: 'text/css' });
    }
    if (/^\/auth\/static\/fonts\/geist-(?:latin|latin-ext|vietnamese)-wght-normal\.woff2$/.test(pathname)) {
      return route.fulfill({ path: path.join(dashboardRoot, 'fonts', path.basename(pathname)), contentType: 'font/woff2' });
    }
    if (['/auth/login', '/auth/account', '/auth/users'].includes(pathname)) {
      return route.fulfill({ path: path.join(staticRoot, 'index.html'), contentType: 'text/html' });
    }
    if (['/', '/pages/'].includes(pathname)) return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Workspace fixture</title><p>Workspace fixture</p>' });
    throw new Error(`Unexpected fixture resource: ${pathname}`);
  });
  return { page, context, state };
}

async function loginFlow(browser) {
  const { page, context, state } = await fixture(browser);
  try {
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto(`${origin}/auth/login?next=/pages/`);
    await page.getByRole('heading', { name: 'Đăng nhập Via', exact: true }).waitFor();
    const fonts = await page.getByRole('heading', { name: 'Đăng nhập Via', exact: true }).evaluate(async heading => {
      const load = async text => (await document.fonts.load('600 28px Geist', text)).some(face => face.status === 'loaded');
      return { family: getComputedStyle(heading).fontFamily.split(',')[0], latin: await load('Via'), vietnamese: await load('Đậ') };
    });
    assert.deepEqual(fonts, { family: 'Geist', latin: true, vietnamese: true });
    await page.getByLabel('Tên đăng nhập', { exact: true }).fill('operator1');
    const password = page.getByLabel('Mật khẩu', { exact: true });
    await password.fill('fixture-invalid-password');
    assert.equal(await password.getAttribute('type'), 'password');
    await page.getByRole('button', { name: 'Hiện mật khẩu', exact: true }).click();
    assert.equal(await password.getAttribute('type'), 'text');
    assert.equal(await password.inputValue(), 'fixture-invalid-password');
    await page.getByRole('button', { name: 'Ẩn mật khẩu', exact: true }).click();
    assert.equal(await password.getAttribute('type'), 'password');
    assert.equal(state.writes.length, 0, 'toggling visibility does not submit the form');
    await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Tên đăng nhập hoặc mật khẩu chưa đúng.' }).waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
      'the login page and error message fit a 360px viewport');
    assert.equal(new URL(page.url()).pathname, '/auth/login');
    await password.fill(fixturePassword);
    await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await page.waitForURL(`${origin}/pages/`);
    assert.equal(state.writes.length, 2);
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
}

async function passwordFlow(browser) {
  const { page, context, state } = await fixture(browser, user(2, 'operator1', 'operator', true),
    { mcp_url: 'https://via.example.test/mcp', portal_only: false });
  try {
    await page.goto(`${origin}/auth/account`);
    await page.getByText('Đổi mật khẩu ban đầu để mở workspace.', { exact: false }).waitFor();
    assert.equal(await page.getByRole('link', { name: 'Chrome Manager', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Tạo token riêng', exact: true }).count(), 0);
    await page.getByLabel('Mật khẩu hiện tại', { exact: true }).fill(fixturePassword);
    await page.getByLabel('Mật khẩu mới', { exact: true }).fill(newPassword);
    await page.getByLabel('Nhập lại mật khẩu mới', { exact: true }).fill('fixture-does-not-match');
    await page.getByRole('button', { name: 'Lưu mật khẩu mới', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Hai mật khẩu mới chưa khớp.' }).waitFor();
    assert.equal(state.writes.length, 0, 'confirmation mismatch remains client-side');
    await page.getByLabel('Nhập lại mật khẩu mới', { exact: true }).fill(newPassword);
    await page.getByRole('button', { name: 'Lưu mật khẩu mới', exact: true }).click();
    await page.getByRole('link', { name: 'Chrome Manager', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Tạo token riêng', exact: true }).waitFor();
    assert.equal(await page.getByLabel('Mật khẩu hiện tại', { exact: true }).inputValue(), '');
    await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await page.waitForURL(`${origin}/auth/login`);
    assert.equal(state.writes.at(-1).csrf, 'fixture-csrf-after-password-change');
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
}

async function adminFlow(browser) {
  const { page, context, state } = await fixture(browser, user(1, 'admin', 'admin'));
  try {
    await page.goto(`${origin}/auth/users`);
    await page.getByRole('heading', { name: 'Quản lý người dùng', exact: true }).waitFor();
    const admin = page.locator('.account-grid section').filter({ has: page.getByRole('heading', { name: 'admin', exact: true }) });
    assert.equal(await admin.getByRole('button', { name: 'Đặt lại mật khẩu', exact: true }).count(), 0, 'admin uses account password change for self');
    const operator = page.locator('.account-grid section').filter({ has: page.getByRole('heading', { name: 'operator1', exact: true }) });
    page.on('dialog', dialog => dialog.accept());
    await operator.getByRole('button', { name: 'Đặt lại mật khẩu', exact: true }).click();
    await operator.locator('output').filter({ hasText: temporaryPassword }).waitFor();
    assert.equal(new URL(page.url()).pathname, '/auth/users');
    assert.equal(state.writes.at(-1).body.revision, 1);
    await operator.locator('input[name="username"]').fill('operator-renamed');
    await operator.getByRole('button', { name: 'Lưu thay đổi', exact: true }).click();
    await page.getByRole('heading', { name: 'operator-renamed', exact: true }).waitFor();
    assert.equal(state.writes.at(-1).body.revision, 2, 'reset updates the revision before another edit');
    assert.equal(await page.locator('img').count(), 0, 'audit/user strings render as inert text');
    assert.equal(await page.evaluate(() => window.identityXss), undefined);
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
}

async function operatorUsersDenied(browser) {
  const { page, context, state } = await fixture(browser, user(2, 'operator1'));
  try {
    await page.goto(`${origin}/auth/users`);
    await page.getByRole('heading', { name: 'Tài khoản của bạn', exact: true }).waitFor();
    assert.equal(await page.getByRole('heading', { name: 'Quản lý người dùng', exact: true }).count(), 0);
    assert.equal(await page.getByRole('link', { name: 'Quản lý 3 tài khoản', exact: true }).count(), 0);
    assert.deepEqual(state.errors, []);
  } finally { await context.close(); }
}

async function codexTokenFlow(browser) {
  for (const actor of [user(1, 'admin', 'admin'), user(2, 'operator1'), user(3, 'operator2')]) {
    const { page, context, state } = await fixture(browser, actor,
      { mcp_url: 'https://via.example.test/mcp', portal_only: false });
    try {
      await page.goto(`${origin}/auth/account`);
      await page.getByRole('heading', { name: 'Kết nối Codex trên máy của bạn', exact: true }).waitFor();
      const name = '<img src=x onerror=window.patXss=1>';
      await page.getByLabel('Tên máy hoặc kết nối', { exact: true }).fill(name);
      await page.getByLabel('Quyền kết nối', { exact: true }).selectOption('write');
      await page.getByLabel('Thời hạn token', { exact: true }).selectOption('30');
      await page.getByRole('button', { name: 'Tạo token riêng', exact: true }).click();
      const issued = 'via_pat_' + String(actor.id).repeat(43);
      await page.locator('output').filter({ hasText: issued }).waitFor();
      assert.deepEqual(state.writes.at(-1).body, { name, scopes: ['via:read', 'via:write'], days: 30 });
      assert.equal(state.writes.at(-1).csrf, state.csrf);
      assert.equal(state.issuedTokens.length, 1);
      assert.equal('token' in state.tokens[0], false, 'list metadata never stores the raw token');
      assert.equal(await page.locator('img').count(), 0, 'connection names render as inert text');
      assert.equal(await page.evaluate(() => window.patXss), undefined);
      assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
      assert.equal(new URL(page.url()).search, '');
      await page.reload();
      await page.getByRole('button', { name: 'Thu hồi ' + name, exact: true }).waitFor();
      assert.equal(await page.locator('output').count(), 0, 'raw token disappears after reload');
      assert.equal((await page.locator('body').textContent()).includes(issued), false);
      await page.getByRole('button', { name: 'Thu hồi ' + name, exact: true }).click();
      await page.getByRole('status').filter({ hasText: 'Đã thu hồi kết nối Codex.' }).waitFor();
      assert.equal(state.writes.at(-1).method, 'DELETE');
      assert.equal(state.writes.at(-1).endpoint, 'mcp-tokens/' + state.tokens[0].id);
      assert.equal(await page.getByRole('button', { name: 'Thu hồi ' + name, exact: true }).count(), 0);
      assert.deepEqual(state.errors, []);
    } finally { await context.close(); }
  }
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    await loginFlow(browser);
    await passwordFlow(browser);
    await adminFlow(browser);
    await operatorUsersDenied(browser);
    await codexTokenFlow(browser);
    console.log('PASS: 7 isolated identity UI flows (login, password change, admin, operator, and per-user PAT creation/revoke for all 3 accounts)');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
