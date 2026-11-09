'use strict';

// Fresh Chrome contexts, actual built dashboard, intercepted HTTP/WS fixture.
// The shared fixture resolves every API request by its own session cookie.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { chromium } = require('../frontend/node_modules/playwright');

const dist = path.resolve(process.env.CM_UI_DIST || path.join(__dirname, '../frontend/dist'));
const auth = path.resolve(__dirname, '../../via-auth/via_auth/static');
const pageAssets = path.resolve(__dirname, '../../meta-page-manager/frontend');
const origin = 'http://127.0.0.1:41797';
const stamp = new Date().toISOString();
const password = 'isolated-fixture-password-2026';
const labels = ['QA Alpha', 'QA Beta', 'QA Gamma'];
const actors = labels.map((label, index) => {
  const profiles = [1, 2, 3, 4].map(id => {
    const name = `${label} ${id < 3 ? 'Chrome' : 'BM'} ${id}`;
    const octet = 20 * index + id;
    const proxy = { id, host: `192.0.2.${octet}`, port: 8000 + id, protocol: 'http', status: 'Working',
      ip_address: `198.51.100.${octet}`, latency_ms: 3, last_checked: stamp, error_message: null,
      has_credentials: false, profile_name: name };
    const bm = id < 3 ? null : { note: `${name} note`, updated_at: stamp, mobile_enabled: id === 4,
      portfolios: [1, 2].map(number => ({ bm_id: String(80000 + 100 * (id - 2) + number), name: `${name} portfolio ${number}` })) };
    const page = { id, name: `${name} Page`, url: `https://www.facebook.com/profile.php?id=${500 + id}`,
      status: 'unchecked', posts: [], posts_today: null, checked_at: null, check_date: null,
      timezone: null, error_message: null };
    return { id, name, proxy, proxy_id: id, profile_path: `fixture/${index}/${id}`, revision: 1, bm,
      browser_status: 'Stopped', browser_mode: null, browser_error: null, task: null, outlook: null,
      facebook: bm ? null : { name: `${name} account`, status: 'checked', pages: [page], page_count: 1 } };
  });
  return { label, profiles, selectedId: index === 1 ? 2 : 1, csrf: randomUUID(), sockets: new Set(),
    user: { id: index + 1, username: `qa_${label.split(' ')[1].toLowerCase()}`, role: index ? 'operator' : 'admin',
      active: true, revision: 1, must_change_password: false }, revision: 0, launches: [], pageReads: [] };
});

const sessions = new Map();
const errors = [];
let inFlight = 0, maximumInFlight = 0, releaseLaunches;
const simultaneousLaunches = new Promise(resolve => { releaseLaunches = resolve; });
const getActor = cookie => sessions.get(/(?:^|;\s*)via_fixture=([^;]+)/.exec(cookie || '')?.[1]);
const nav = (page, label) => page.getByRole('navigation', { name: 'Điều hướng chính' }).getByRole('button', { name: label, exact: true });
const settingsTab = (page, label) => page.getByRole('tab', { name: label, exact: true });
const row = (page, profile) => page.getByRole('row').filter({ hasText: profile.name });
const endpoint = profile => `${profile.proxy.host}:${profile.proxy.port}`;

function snapshot(actor) {
  const running = actor.profiles.filter(profile => profile.browser_status === 'Running').length;
  return { cpu_percent: 1, memory_percent: 20, memory_used_mb: 2048, memory_total_mb: 16384,
    memory_available_mb: 14336, total_profiles: 4, running_browsers: running, queued_browsers: 0,
    stopped_browsers: 4 - running, working_proxies: 4, failed_proxies: 0, total_proxies: 4,
    running_tasks: 0, pending_tasks: 0, max_browsers: 4, checking_proxies: 0, resource_warning: null };
}
function publish(actor) {
  const update = JSON.stringify({ system: snapshot(actor), workspace_revision: String(actor.revision) });
  for (const socket of actor.sockets) socket.send(update);
}
function envelope(items, params, summary) {
  const limit = Number(params.get('limit')), offset = Number(params.get('offset'));
  assert.ok(limit >= 1 && limit <= 100 && offset >= 0);
  return { items: items.slice(offset, offset + limit), total: items.length, limit, offset, summary };
}

async function install(context) {
  await context.routeWebSocket('**/ws*', async socket => {
    const cookie = (await context.cookies(origin)).find(item => item.name === 'via_fixture');
    const actor = sessions.get(cookie?.value);
    if (!actor) return socket.close();
    actor.sockets.add(socket);
    socket.onClose(() => actor.sockets.delete(socket));
    socket.send(JSON.stringify({ system: snapshot(actor), workspace_revision: String(actor.revision) }));
  });
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), routePath = url.pathname;
    assert.equal(url.origin, origin, 'The fixture serves every request locally');
    const json = (value, status = 200, headers) => route.fulfill({ json: value, status, headers });
    if (routePath === '/auth/api/login') {
      const body = request.postDataJSON();
      const actor = actors.find(value => value.user.username === body.username && body.password === password);
      if (!actor) return json({ detail: 'Unknown fixture login' }, 401);
      const session = randomUUID();
      sessions.set(session, actor);
      return json({ user: actor.user }, 200, { 'set-cookie': `via_fixture=${session}; Path=/; HttpOnly; SameSite=Lax` });
    }
    if (routePath.startsWith('/api/') || routePath === '/auth/api/me') {
      const actor = getActor(await request.headerValue('cookie'));
      if (!actor) return json({ code: 'authentication_required' }, 401);
      if (routePath === '/auth/api/me') return json({ user: actor.user, csrf_token: actor.csrf });
      if (routePath === '/api/session') return json({ rest_api_version: 1 });
      assert.ok(routePath.startsWith('/api/v1/'), 'Chrome business requests must use v1');
      const name = routePath.slice('/api/v1/'.length), params = url.searchParams;
      const regular = actor.profiles.filter(profile => !profile.bm);
      if (request.method() === 'POST') {
        if (await request.headerValue('x-via-csrf') !== actor.csrf) return json({ detail: 'Wrong session CSRF' }, 403);
        const body = request.postDataJSON();
        const bmOpen = /^bm-profiles\/(\d+)\/open$/.exec(name);
        const selectedId = bmOpen ? Number(bmOpen[1]) : body.profile_ids?.[0];
        const profile = actor.profiles.find(item => item.id === selectedId);
        if (bmOpen) {
          if (!body.bm_id) return json({ detail: 'Choose a specific BM' }, 422);
          if (!profile?.bm?.portfolios.some(item => item.bm_id === body.bm_id)) return json({ detail: 'BM belongs to another profile' }, 404);
        } else {
          assert.equal(name, 'profiles/start-selected');
          assert.deepEqual(body.profile_ids, [actor.selectedId]);
          assert.ok(profile && !profile.bm);
        }
        inFlight++;
        maximumInFlight = Math.max(maximumInFlight, inFlight);
        if (inFlight === 9) releaseLaunches();
        await simultaneousLaunches;
        inFlight--;
        profile.browser_status = 'Running';
        profile.browser_mode = profile.bm?.mobile_enabled ? 'mobile' : 'manual';
        actor.launches.push({ id: profile.id, bmId: body.bm_id || null, proxy: endpoint(profile) });
        actor.revision++;
        publish(actor);
        return json({ accepted: [profile.id], profile_id: profile.id, status: 'opened' }, 202);
      }
      assert.equal(request.method(), 'GET');
      if (name === 'profiles' || name === 'profile-options') {
        const group = params.get('scope') === 'bm' ? actor.profiles.filter(profile => profile.bm) : regular;
        return json(envelope(group, params, { all: group.length, running: group.filter(profile => profile.browser_status === 'Running').length,
          stopped: group.filter(profile => profile.browser_status === 'Stopped').length,
          portfolios: group.reduce((total, profile) => total + (profile.bm?.portfolios.length || 0), 0) }));
      }
      if (name === 'profile-ids') {
        assert.equal(params.get('scope'), 'proxy');
        const ids = regular.map(profile => profile.id).filter(id => params.get('ids').split(',').includes(String(id)));
        return json({ ids, total: ids.length });
      }
      if (/^profiles\/\d+$/.test(name)) return json(actor.profiles.find(profile => profile.id === Number(name.split('/')[1])));
      if (name === 'proxies') return json(envelope(regular.map(profile => profile.proxy), params, { all: regular.length }));
      if (name === 'system/status') return json(snapshot(actor));
      if (name === 'summary') return json({ pages: 2, accounts: 2, posted: 0, missing: 0, pending: 2, active: 0 });
      if (name === 'reels/config') return json({ max_upload_bytes: 1073741824, accepted_extensions: ['.mp4'], default_timezone: 'UTC', scheduler_running: true });
      if (name === 'reel-assets') return json(envelope([], params));
      if (name === 'reel-page-options' || name === 'reels/page-eligibility') {
        const profile = name === 'reel-page-options' ? regular.find(item => item.id === Number(params.get('profile_id')))
          : regular.find(item => item.facebook.pages[0].url === params.get('page_url'));
        assert.ok(profile, 'Reel choices refer to a regular profile in the authenticated workspace');
        const page = profile.facebook.pages[0];
        const policy = { page_id: String(500 + profile.id), can_publish: true, information_status: 'updated', posting_block_reason: null };
        if (name === 'reels/page-eligibility') return json(policy);
        actor.pageReads.push(profile.id);
        return json(envelope([{ ...policy, page_name: page.name, page_url: page.url, source: 'facebook_scan' }], params));
      }
      if (name === 'reels') {
        const profile = regular.find(item => item.id === actor.selectedId), page = profile.facebook.pages[0];
        return json(envelope([{ id: 1, profile_id: profile.id, profile_name: profile.name, page_url: page.url,
          page_name: page.name, asset_id: 'a'.repeat(32), asset_name: `${actor.label} saved video`, asset_size: 1024,
          caption: `${actor.label} saved caption`, scheduled_at: stamp, timezone: 'UTC', status: 'completed',
          step: 'completed', message: 'Fixture complete', created_at: stamp, updated_at: stamp,
          publish_attempted: true, can_verify_link: false, can_cancel: false, can_resume: false, can_archive: false, can_restore: false }], params));
      }
      throw new Error(`Unhandled fixture API ${name}`);
    }
    const staticFile = routePath === '/auth/login' ? path.join(auth, 'index.html') :
      routePath === '/auth/static/fonts.css' ? path.join(pageAssets, 'fonts.css') :
      /^\/auth\/static\/fonts\/geist-(latin|latin-ext|vietnamese)-wght-normal\.woff2$/.test(routePath) ? path.join(pageAssets, 'fonts', path.basename(routePath)) :
      ['/auth/static/app.js', '/auth/static/style.css'].includes(routePath) ? path.join(auth, path.basename(routePath)) :
      routePath === '/' || routePath.startsWith('/chrome/') ? path.join(dist, 'index.html') : path.resolve(dist, '.' + routePath);
    if (!fs.existsSync(staticFile) || !fs.statSync(staticFile).isFile()) return route.fulfill({ status: 404 });
    const types = { '.js': 'application/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.html': 'text/html' };
    return route.fulfill({ path: staticFile, contentType: types[path.extname(staticFile)] });
  });
}

function observe(page) {
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => errors.push(error.message));
}
async function assertOwn(page, actor) {
  assert.match(await page.locator('.account').innerText(), new RegExp(actor.user.username));
  const contents = await page.locator('main').innerText();
  for (const label of labels.filter(value => value !== actor.label)) assert.ok(!contents.includes(label));
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const clients = await Promise.all(actors.map(async actor => {
      const context = await browser.newContext({ offline: true, serviceWorkers: 'block', viewport: { width: 1600, height: 1000 } });
      await install(context);
      const dashboard = await context.newPage(); observe(dashboard);
      await dashboard.goto(origin + '/auth/login?next=/');
      await dashboard.getByLabel('Tên đăng nhập', { exact: true }).fill(actor.user.username);
      await dashboard.getByLabel('Mật khẩu', { exact: true }).fill(password);
      await dashboard.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
      const ordinary = actor.profiles.find(profile => profile.id === actor.selectedId);
      await row(dashboard, ordinary).getByText(endpoint(ordinary), { exact: true }).waitFor();
      await dashboard.getByRole('checkbox', { name: 'Select ' + ordinary.name, exact: true }).check();
      await dashboard.locator('.account').getByText(actor.user.username, { exact: false }).waitFor();
      assert.equal(await dashboard.locator('.profile-table tbody tr').count(), 2);
      await assertOwn(dashboard, actor);
      const bm = await context.newPage(); observe(bm);
      await bm.goto(origin + '/chrome/settings?tab=bm');
      for (const profile of actor.profiles.filter(item => item.bm)) await row(bm, profile).getByText(endpoint(profile), { exact: true }).waitFor();
      await bm.locator('.account').getByText(actor.user.username, { exact: false }).waitFor();
      assert.equal(await bm.locator('.bm-profile-table tbody tr').count(), 2);
      assert.equal(await bm.getByRole('button', { name: 'Mở BM', exact: true }).count(), 0);
      await assertOwn(bm, actor);
      return { context, actor, dashboard, bm, ordinary };
    }));
    assert.equal(sessions.size, 3, 'Each login establishes its own browser session');
    await Promise.all(clients.map(async ({ actor, dashboard, bm }) => {
      await dashboard.getByRole('button', { name: 'Start Selected', exact: true }).click();
      for (const profile of actor.profiles.filter(item => item.bm)) {
        const chosen = profile.bm.portfolios[(actor.user.id + profile.id) % 2];
        await row(bm, profile).getByRole('button', { name: `Mở BM ${chosen.bm_id} trong ${profile.name}`, exact: true }).click();
      }
    }));
    let launchTimeout;
    try {
      await Promise.race([simultaneousLaunches, new Promise((_, reject) => {
        launchTimeout = setTimeout(() => reject(new Error(`Only ${maximumInFlight} of 9 fixture launch requests overlapped`)), 5000);
      })]);
    } finally { clearTimeout(launchTimeout); }
    for (const { actor, dashboard, bm, ordinary } of clients) {
      await dashboard.bringToFront();
      await row(dashboard, ordinary).getByRole('button', { name: 'Stop', exact: true }).waitFor();
      await bm.bringToFront();
      for (const profile of actor.profiles.filter(item => item.bm)) await row(bm, profile).getByRole('button', { name: 'Dừng ' + profile.name, exact: true }).waitFor();
      assert.deepEqual(actor.launches.sort((a, b) => a.id - b.id), [ordinary, ...actor.profiles.filter(profile => profile.bm)].map(profile => ({
        id: profile.id, bmId: profile.bm?.portfolios[(actor.user.id + profile.id) % 2].bm_id || null, proxy: endpoint(profile),
      })));
      await settingsTab(bm, 'Proxy Manager').click();
      await bm.getByText(endpoint(ordinary), { exact: true }).waitFor();
      for (const profile of actor.profiles.filter(item => item.bm)) assert.equal(await bm.getByText(endpoint(profile), { exact: true }).count(), 0);
      await assertOwn(bm, actor);
      await settingsTab(bm, 'BM Profiles').click();
      await row(bm, actor.profiles[2]).getByText(endpoint(actor.profiles[2]), { exact: true }).waitFor();
      await dashboard.bringToFront();
      await nav(dashboard, 'Lịch đăng Page').click();
      const form = dashboard.locator('.reels-form-panel');
      await form.getByRole('textbox', { name: 'Caption Reel', exact: true }).fill(actor.label + ' draft');
      const pageName = ordinary.facebook.pages[0].name;
      await form.getByRole('option', { name: new RegExp(pageName) }).waitFor({ state: 'attached' });
      assert.equal(await form.getByRole('combobox', { name: 'Profile đăng Reel', exact: true }).inputValue(), String(ordinary.id));
      assert.deepEqual(actor.pageReads, [ordinary.id]);
      await nav(dashboard, 'Lịch đã lưu').click();
      await dashboard.getByRole('article', { name: 'Lịch Reel #1', exact: true }).getByText(pageName, { exact: false }).first().waitFor();
      await assertOwn(dashboard, actor);
      await nav(dashboard, 'Lịch đăng Page').click();
      assert.equal(await form.getByRole('textbox', { name: 'Caption Reel', exact: true }).inputValue(), actor.label + ' draft');
      await nav(dashboard, 'Dashboard').click();
      assert.equal(await dashboard.getByRole('checkbox', { name: 'Select ' + ordinary.name, exact: true }).isChecked(), true);
      await assertOwn(dashboard, actor);
    }
    assert.equal(maximumInFlight, 9, 'The three accounts have nine overlapping open requests');
    for (const { actor, dashboard, bm } of clients) {
      await bm.bringToFront(); await assertOwn(bm, actor);
      await dashboard.bringToFront(); await assertOwn(dashboard, actor);
    }
    assert.deepEqual(errors, []);
    console.log('PASS: 3 concurrent fixture logins, 6 real Chrome tabs, overlapping profile/BM/schedule IDs, 9 simultaneous open requests, exact proxy and BM bindings, isolated navigation and Reel drafts.');
  } finally { releaseLaunches(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
