"""Read-only checks in a dedicated browser session using each saved profile."""
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
from sqlalchemy import select, delete
from app.core.facebook import facebook_url, visible_post_date, current_window
from app.models.entities import Profile, Proxy, BmProfile, BmProxy, FacebookAccount, FacebookPage, now
from app.services.facebook_identity import ensure_personal_identity,FacebookIdentityError

DOM_PATH = Path(__file__).with_name('facebook_dom.js')


class FacebookReadError(ValueError):
    pass


class FacebookLoginRequired(FacebookReadError):
    pass

class FacebookRefreshDeferred(FacebookReadError):
    pass

def public_facebook(session, at=None):
    accounts = {a.profile_id: a for a in session.scalars(select(FacebookAccount))}
    pages = {}
    for row in session.scalars(select(FacebookPage).order_by(FacebookPage.id)):
        fresh = current_window(row, at)
        status = row.status if fresh or row.status == 'checking' else 'unchecked'
        posts = [post for post in json.loads(row.posts) if post.get('date') == row.check_date] if fresh and status == 'posted' else []
        pages.setdefault(row.profile_id, []).append({
            'id': row.id, 'name': row.name, 'url': row.url, 'status': status,
            'checked_at': row.checked_at, 'check_date': row.check_date, 'timezone': row.timezone,
            'posts_today': len(posts) if fresh and status == 'posted' else 0 if fresh and status == 'not_seen' else None,
            'posts': posts, 'error_message': row.error_message if fresh else None,
        })
    return {pid: {'name': a.name, 'url': a.url, 'status': a.status, 'checked_at': a.checked_at,
                  'error_message': a.error_message, 'check_date': a.check_date, 'timezone': a.timezone,
                  'fresh': current_window(a, at), 'pages': pages.get(pid, [])} for pid, a in accounts.items()}


class FacebookManager:
    def __init__(self, db, state, browsers, tasks):
        self.db, self.state, self.browsers, self.tasks = db, state, browsers, tasks
        self.jobs = {}
        self.names_only_jobs = {}
        # Keep scans conservative: one profile at a time, with the normal browser slot/resource limits.
        self.semaphore = asyncio.Semaphore(1)
        with db.session.begin() as session:
            for row in session.scalars(select(FacebookAccount).where(FacebookAccount.status.in_(['queued', 'checking']))):
                row.status, row.error_message = 'cancelled', 'Lần kiểm tra trước bị gián đoạn. Hãy kiểm tra lại.'
            for row in session.scalars(select(FacebookPage).where(FacebookPage.status == 'checking')):
                row.status, row.error_message = 'unknown', 'Lần kiểm tra trước bị gián đoạn.'

    def enqueue(self, body):
        accepted, errors = [], []
        for pid in body.profile_ids:
            try:
                if pid in self.jobs:
                    raise ValueError('Profile đang được kiểm tra Facebook.')
                if pid in self.tasks.profile_jobs or pid in self.browsers.extension_profiles or self.browsers.status(pid) in ('Running', 'Starting', 'Queued'):
                    raise ValueError('Dừng Chrome và tác vụ của profile trước khi kiểm tra Facebook.')
                self.browsers.validate_automation_start(pid)
                if body.names_only:self.validate_names_start(pid)
                with self.db.session.begin() as session:
                    row = session.get(FacebookAccount, pid)
                    if row is None:
                        row = FacebookAccount(profile_id=pid)
                        session.add(row)
                    if not body.names_only:row.status, row.error_message = 'queued', None
                self.browsers.facebook_profiles.add(pid)
                job = asyncio.create_task(self._run(pid, body))
                self.jobs[pid] = job
                if body.names_only:self.names_only_jobs[pid]=job
                def done(task):
                    if not task.cancelled():
                        task.exception()
                job.add_done_callback(done)
                accepted.append(pid)
            except ValueError as error:
                errors.append({'profile_id': pid, 'message': str(error)})
        self.state.emit()
        return {'accepted': accepted, 'errors': errors}

    def validate_names_start(self,pid,*,owned=False):
        with self.db.session() as session:
            profile=session.get(Profile,pid)
            if profile is None or session.get(BmProfile,pid) is not None or not profile.proxy_id or session.get(BmProxy,profile.proxy_id) is not None:
                raise ValueError('Cập nhật tên tự động chỉ dùng Chrome thường có proxy riêng trong danh sách chung.')
            proxy=session.get(Proxy,profile.proxy_id)
            if proxy is None or proxy.status in ('Failed','Timeout'):raise ValueError('Kiểm tra lại proxy trước khi cập nhật tên Facebook.')
        helpers=('extension_profiles','outlook_profiles','recipient_profiles','reel_profiles')
        if pid in self.tasks.profile_jobs or any(pid in getattr(self.browsers,key,set()) for key in helpers) or self.browsers.status(pid) in ('Running','Starting','Queued'):
            raise ValueError('Profile đang bận; lượt cập nhật tên sẽ chờ.')
        if owned and pid not in self.browsers.facebook_profiles:raise ValueError('Lượt cập nhật tên không còn giữ profile.')
        if owned and any(other!=pid for other in self.jobs):raise ValueError('Lượt kiểm tra Facebook khác được ưu tiên trước cập nhật tên.')
        if self.browsers.resources.blocked_reason(self.state.settings):raise ValueError('Đang chờ tài nguyên máy để cập nhật tên.')
        occupied=set(getattr(self.browsers,'contexts',{}))|set(getattr(self.browsers,'reserved',set()))|set(getattr(self.browsers,'launches',{}))
        occupied.update(self.tasks.profile_jobs)
        for key in ('extension_profiles','facebook_profiles','outlook_profiles','recipient_profiles','reel_profiles'):occupied.update(getattr(self.browsers,key,set()))
        if owned:occupied.discard(pid)
        if len(occupied)>=self.state.settings.max_browsers:raise ValueError('Đang chờ lượt Chrome trống để cập nhật tên.')
        self.browsers.validate_automation_start(pid,for_facebook=owned)

    def account_update(self, pid, **values):
        with self.db.session.begin() as session:
            row = session.get(FacebookAccount, pid)
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
        self.state.emit()

    def page_update(self, page_id, **values):
        with self.db.session.begin() as session:
            row = session.get(FacebookPage, page_id)
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
        self.state.emit()

    async def read(self, page, mode, **arguments):
        result = await page.evaluate(DOM_PATH.read_text(encoding='utf-8'), {'mode': mode, **arguments})
        if result.get('blocked'):
            raise FacebookLoginRequired('Facebook yêu cầu đăng nhập/xác minh hoặc đang giới hạn truy cập. Mở profile và xử lý trên Facebook rồi kiểm tra lại.')
        return result

    async def capture_identity_diagnostics(self, page, profile_id, stage='account'):
        """Keep one local rendered-UI diagnostic per profile, never browser secrets."""
        if profile_id is None:
            return
        try:
            data = await self.read(page, 'identity_debug')
            folder = self.browsers.config.root / '.runtime' / 'facebook-debug'
            folder.mkdir(parents=True, exist_ok=True)
            data['captured_at'] = now()
            (folder / f'profile_{profile_id}_{stage}.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            await page.screenshot(path=str(folder / f'profile_{profile_id}_{stage}.png'),
                                  full_page=False, mask=[page.locator('input,textarea,[contenteditable="true"]')], timeout=5000)
            self.state.log('A local Facebook rendered-UI diagnostic was saved. No cookies, tokens or form values were read.', profile_id, 'warning')
        except Exception:
            self.state.log('Facebook rendered-UI diagnostic could not be saved.', profile_id, 'warning')

    async def visit(self, page, url):
        await self.tasks._rate_limit('https://www.facebook.com')
        response = await page.goto(url, wait_until='domcontentloaded', timeout=45000)
        if response and response.status in (401, 403, 429):
            raise FacebookLoginRequired('Facebook từ chối hoặc giới hạn truy cập. Mở profile để kiểm tra phiên đăng nhập.')
        await page.wait_for_timeout(1800)

    async def read_account(self, page, profile_id=None):
        """Facebook can use ARIA headings, a late title or its account menu."""
        result = {}
        for _ in range(12):
            result = await self.read(page, 'account')
            if result.get('is_page'):
                raise FacebookReadError('Facebook vẫn đang dùng danh tính Page. Chưa đọc tên FB hoặc ghi lại danh sách Page cho tới khi chuyển về tài khoản cá nhân.')
            if result.get('name'):
                return result
            await page.wait_for_timeout(750)
        # Opening the account menu reads the signed-in identity without switching it.
        buttons = page.get_by_role('button', name=re.compile(
            r'^(Your profile(?: picture)?|Account|Trang cá nhân của bạn|Ảnh đại diện của bạn|Tài khoản)(?:[:,]\s*.+)?$', re.I))
        for button in await buttons.all():
            if not await button.is_visible():
                continue
            await button.click(timeout=3000)
            try:
                for _ in range(4):
                    result = await self.read(page, 'account', accountMenu=True)
                    if result.get('is_page'):
                        raise FacebookReadError('Facebook vẫn đang dùng danh tính Page. Chưa đọc tên FB hoặc ghi lại danh sách Page cho tới khi chuyển về tài khoản cá nhân.')
                    if result.get('name'):
                        return result
                    await page.wait_for_timeout(500)
            finally:
                await page.keyboard.press('Escape')
            break
        diagnostics = result.get('diagnostics', {})
        await self.capture_identity_diagnostics(page, profile_id)
        self.state.log('Facebook identity not readable: visible main regions=' + str(diagnostics.get('main_count', 0))
                       + ', profile headings=' + str(diagnostics.get('heading_count', 0))
                       + ', profile URL=' + str(bool(diagnostics.get('profile_url')))
                       + '. No login data was recorded.', profile_id, level='warning')
        raise FacebookReadError('Chưa đọc được tên Facebook từ trang cá nhân hoặc menu tài khoản. Mở profile để kiểm tra Facebook đã tải xong, rồi dừng Chrome và thử lại.')

    async def discover(self, page):
        await self.visit(page, 'https://www.facebook.com/pages/?category=your_pages&ref=bookmarks')
        found, stable, recognized, empty = {}, 0, False, False
        for _ in range(12):
            data = await self.read(page, 'pages')
            recognized |= data.get('recognized', False)
            empty |= data.get('empty', False)
            before = len(found)
            # Only associate links when Facebook explicitly labels this as the managed Page list.
            if recognized:
                for item in data.get('pages', []):
                    try:
                        key, url = facebook_url(item['url'])
                    except ValueError:
                        continue
                    found[key] = {'page_key': key, 'url': url, 'name': item['name'][:160]}
            stable = stable + 1 if before == len(found) else 0
            if empty or stable >= 3:
                return list(found.values()), None if recognized and (found or empty) else 'Chưa đọc được danh sách Page quản lý từ giao diện Facebook.'
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1000)
        return list(found.values()), 'Danh sách Page có thể chưa tải hết. Kiểm tra lại để cập nhật thêm.'

    async def check_page(self, page, row, window):
        await self.visit(page, row.url)
        target = datetime.fromisoformat(window.check_date).date()
        found, older, unknown, empty = {}, set(), set(), False
        for _ in range(8):
            data = await self.read(page, 'posts', pageName=row.name)
            empty |= data.get('empty', False)
            observed = datetime.now(timezone.utc)
            for item in data.get('posts', []):
                if not item.get('author_matches'):
                    continue
                dates = [visible_post_date(t, observed, window) for t in item['times']]
                date = next((d for d in dates if d is not None), None)
                if date is None:
                    try:
                        # Hide the previous hover before reading a new timestamp tooltip.
                        await page.mouse.move(0, 0)
                        await page.locator('[role="tooltip"]').wait_for(state='hidden', timeout=1200)
                        await page.locator('[data-cm-time="' + str(item['hover']) + '"]').hover(timeout=1500)
                        tooltip = page.locator('[role="tooltip"]').last
                        await tooltip.wait_for(state='visible', timeout=1200)
                        date = visible_post_date(await tooltip.inner_text(), observed, window)
                    except Exception:
                        pass
                url = self.post_url(item['url'])
                if not url:
                    continue
                if date == target:
                    found[url] = {'url': url, 'date': date.isoformat()}
                    unknown.discard(url)
                elif date is not None and date < target and not item.get('pinned'):
                    older.add(url)
                    unknown.discard(url)
                elif date is None:
                    unknown.add(url)
            if len(older) >= 2 and not unknown or empty:
                break
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1200)
        status = 'posted' if found else 'not_seen' if (older or empty) and not unknown else 'unknown'
        message = 'Số bài là số tối thiểu đã đọc được từ giao diện, có thể còn bài chưa tải.' if found else 'Chưa thấy bài hôm nay trong phần dòng thời gian đã đọc.' if status == 'not_seen' else 'Không đọc đủ tác giả/thời gian bài đăng từ giao diện Facebook.'
        return {'status': status, 'posts': json.dumps(list(found.values())), 'error_message': message}

    @staticmethod
    def post_url(value):
        try:
            p = urlsplit(value)
            if p.scheme != 'https' or p.hostname not in ('www.facebook.com', 'facebook.com', 'm.facebook.com'):
                return None
            if not any(part in p.path for part in ('/posts/', '/videos/', '/reel/', '/permalink.php', '/story.php', '/photo.php', '/photo/')):
                return None
            query = parse_qs(p.query)
            keep = {k: query[k][0] for k in ('story_fbid', 'id', 'fbid') if k in query}
            if p.path in ('/story.php', '/permalink.php') and not keep.get('story_fbid'):
                return None
            if p.path in ('/photo.php', '/photo/') and not keep.get('fbid'):
                return None
            return 'https://www.facebook.com' + p.path.rstrip('/') + ('?' + urlencode(keep) if keep else '')
        except (ValueError, TypeError):
            return None

    async def _run(self, pid, window):
        names_only=bool(getattr(window,'names_only',False));owned_job=self.jobs.get(pid);outcome='error'
        fields = {key: getattr(window, key) for key in ('check_date', 'timezone', 'day_start', 'day_end')}
        current_page = None
        opened = False
        launch_requested = False
        acquired = False
        try:
            await self.semaphore.acquire()
            acquired = True
            if names_only:
                try:
                    if owned_job is None or self.jobs.get(pid) is not owned_job:raise ValueError()
                    self.validate_names_start(pid,owned=True)
                except ValueError:raise FacebookRefreshDeferred('Lượt cập nhật tên đang chờ profile và tài nguyên.') from None
            if not names_only and not current_window(window):
                raise FacebookReadError('Đã sang ngày mới khi chờ kiểm tra. Hãy kiểm tra lại cho hôm nay.')
            # Queue wait is separate from the actual read timeout.
            async with asyncio.timeout(600):
                if not names_only:self.account_update(pid, status='checking', **fields)
                launch_requested=True
                context = await self.browsers.ensure_open(pid, for_automation=True, for_facebook=True, timezone_id=window.timezone)
                opened = True
                page = await context.new_page()
                page.on('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
                await self.visit(page, 'https://www.facebook.com/me')
                with self.db.session() as session:
                    previous=session.get(FacebookAccount,pid)
                    previous_name=previous.name if previous else None
                    previous_url=previous.url if previous else None
                    profile=session.get(Profile,pid)
                    bound_uid=profile.name if profile and re.fullmatch(r'[0-9]{6,30}',profile.name) else None
                expected_url,expected_name=previous_url,previous_name
                if bound_uid:
                    expected_url='https://www.facebook.com/profile.php?id='+bound_uid
                    try:cached_uid=facebook_url(previous_url)[0]
                    except (ValueError,TypeError):cached_uid=None
                    # The old scanner could cache an active Page as the FB
                    # account. Its name is not a personal identity hint unless
                    # the cached URL already agrees with the profile's UID.
                    if cached_uid!=bound_uid:expected_name=None
                try:
                    identity=await ensure_personal_identity(page,expected_url,expected_name)
                except FacebookIdentityError as error:
                    raise FacebookReadError(str(error)) from None
                # Selection can return to the home feed. Reopen /me to read the
                # verified personal profile before discovering its managed Pages.
                if identity.get('switched'):
                    await self.visit(page,'https://www.facebook.com/me')
                account = await self.read_account(page, pid)
                try:
                    account_key, account_url = facebook_url(account['url'])
                except ValueError:
                    # /me can remain the displayed URL after Facebook's SPA renders the name.
                    account_key, account_url = None, None
                if bound_uid:
                    if not account_key:
                        try:account_key,account_url=facebook_url(identity.get('url'))
                        except (ValueError,TypeError):account_key,account_url=None,None
                    if account_key!=bound_uid:
                        raise FacebookReadError('Chưa xác nhận được đúng UID Facebook theo tên Chrome profile. Danh sách Page cũ được giữ lại; chọn đúng tài khoản cá nhân rồi kiểm tra lại.')
                if names_only and previous_url:
                    if not account_key:
                        try:account_key,account_url=facebook_url(identity.get('url'))
                        except (ValueError,TypeError):account_key,account_url=None,None
                    try:previous_key=facebook_url(previous_url)[0]
                    except (ValueError,TypeError):previous_key=None
                    if not previous_key or account_key!=previous_key:
                        raise FacebookReadError('Danh tính Facebook đã thay đổi. Giữ dữ liệu đã lưu và dùng kiểm tra đầy đủ để xác nhận lại.')
                # Keep a successfully read name even when Page discovery later fails.
                self.account_update(pid, name=account['name'][:160], url=account_url)
                discovered, warning = await self.discover(page)
                if warning:
                    await self.capture_identity_diagnostics(page, pid, 'pages')
                if account_key and any(item['page_key'] == account_key for item in discovered):
                    self.account_update(pid,name=previous_name,url=previous_url)
                    raise FacebookReadError('Facebook chưa xác nhận được danh tính cá nhân sau khi chuyển tài khoản. Danh sách Page cũ được giữ lại; chọn đúng tài khoản cá nhân trong Chrome rồi kiểm tra lại.')
                with self.db.session.begin() as session:
                    if not names_only and account_url and previous_url and previous_url != account_url:
                        session.execute(delete(FacebookPage).where(FacebookPage.profile_id == pid))
                    existing = {r.page_key: r for r in session.scalars(select(FacebookPage).where(FacebookPage.profile_id == pid))}
                    for item in discovered:
                        row = existing.get(item['page_key'])
                        if row:
                            row.name, row.url = item['name'], item['url']
                        else:
                            session.add(FacebookPage(profile_id=pid, **item))
                    if warning is None and not names_only:
                        keys = [item['page_key'] for item in discovered]
                        session.execute(delete(FacebookPage).where(FacebookPage.profile_id == pid, FacebookPage.page_key.not_in(keys)))
                    session.flush()
                    # Retain previously discovered pages if a later list is partial.
                    rows = list(session.scalars(select(FacebookPage).where(FacebookPage.profile_id == pid).order_by(FacebookPage.id)))
                partial = bool(warning)
                for row in [] if names_only else rows:
                    current_page = row.id
                    self.page_update(row.id, status='checking', error_message=None, **fields)
                    try:
                        result = await self.check_page(page, row, window)
                        if result['status'] == 'unknown':
                            await self.capture_identity_diagnostics(page, pid, f'page_{row.id}_posts')
                        partial |= result['status'] == 'unknown'
                        self.page_update(row.id, checked_at=now(), **fields, **result)
                    except FacebookLoginRequired:
                        raise
                    except Exception:
                        partial = True
                        self.page_update(row.id, status='unknown', checked_at=now(), error_message='Không tải/đọc được Page. Kiểm tra proxy, quyền xem và giao diện Facebook.', **fields)
                    current_page = None
                outcome='partial' if partial else 'complete'
                if not names_only:self.account_update(pid, status=outcome, checked_at=now(), error_message=warning or ('Một số Page chưa đọc được đầy đủ.' if partial else None), **fields)
                self.state.log('Facebook names refreshed; saved post results were retained.' if names_only else 'Facebook Page check finished. See per-Page results on the dashboard.', pid)
        except asyncio.CancelledError:
            outcome='cancelled'
            if not names_only:self.account_update(pid, status='cancelled', error_message='Đã dừng kiểm tra Facebook.')
        except FacebookRefreshDeferred:
            outcome='deferred'
        except FacebookLoginRequired as error:
            outcome='login_required'
            if not names_only:self.account_update(pid, status='login_required', checked_at=now(), error_message=str(error))
        except FacebookReadError as error:
            outcome='error'
            if not names_only:self.account_update(pid, status='error', checked_at=now(), error_message=str(error))
        except Exception:
            outcome='error'
            if not names_only:self.account_update(pid, status='error', checked_at=now(), error_message='Không hoàn tất kiểm tra. Kiểm tra proxy, phiên Facebook hoặc thử lại khi Chrome đã dừng.')
        finally:
            if current_page:
                self.page_update(current_page, status='unknown', checked_at=now(), error_message='Kiểm tra bị gián đoạn; hãy thử lại.', **fields)
            # Only scan-owned browsers can be closed here: enqueue rejects active browsers/tasks.
            owns=self.jobs.get(pid) is owned_job
            if owns and (opened or pid in self.browsers.launches and (not names_only or launch_requested)):
                try:
                    await self.browsers.stop(pid)
                except ValueError:
                    outcome='error'
                    if not names_only:self.account_update(pid, status='error', error_message='Chrome chưa đóng được. Dừng profile trước khi thử lại.')
            if owns:
                self.browsers.facebook_profiles.discard(pid)
                self.jobs.pop(pid, None)
            if self.names_only_jobs.get(pid) is owned_job:self.names_only_jobs.pop(pid,None)
            if acquired:
                self.semaphore.release()
            self.state.emit()
        return {'status':outcome,'finished_at':now()}

    async def stop(self, pid):
        job = self.jobs.get(pid)
        if job:
            names_only=self.names_only_jobs.get(pid) is job
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            # A task cancelled before its first coroutine step never executes its finally block.
            if self.jobs.get(pid) is job:
                self.jobs.pop(pid, None)
                self.browsers.facebook_profiles.discard(pid)
                if not names_only:self.account_update(pid, status='cancelled', error_message='Đã dừng kiểm tra Facebook.')
            if self.names_only_jobs.get(pid) is job:self.names_only_jobs.pop(pid,None)

    async def shutdown(self):
        await asyncio.gather(*(self.stop(pid) for pid in list(self.jobs)))
