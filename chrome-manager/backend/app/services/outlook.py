"""Assisted Microsoft sign-in and fresh-code reading from a user-selected web inbox."""
import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from sqlalchemy import select
from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeoutError
from app.core.outlook import OutlookStart, reader_provider
from app.models.entities import OutlookLogin, now

DOM_PATH = Path(__file__).with_name('outlook_dom.js')
AUTH_HOSTS = {'login.live.com','login.microsoftonline.com','account.live.com'}
OUTLOOK_URL = 'https://outlook.live.com/mail/'
OUTLOOK_SIGN_IN_URL = 'https://go.microsoft.com/fwlink/p/?linkid=2125442&clcid=0x409&culture=en-us&country=us'
ACTIVE = {'queued','opening','entering_email','entering_password','requesting_code','waiting_code','verifying','needs_user'}


def navigation_in_progress(error):
    text=str(error).lower()
    if any(message in text for message in ('has been closed','target closed','browser closed')):
        return False
    return any(message in text for message in (
        'execution context was destroyed','cannot find context with specified id',
        'cannot find execution context','most likely because of a navigation',
        'interrupted by another navigation','net::err_aborted',
    ))


def failure_category(error):
    # Classify in memory. Exception bodies can contain URLs and form values; never persist them.
    if isinstance(error,BrowserTimeoutError):return 'browser_timeout'
    if isinstance(error,TimeoutError):return 'time_budget'
    if isinstance(error,BrowserError):
        text=str(error).lower()
        if navigation_in_progress(error):return 'navigation_changed'
        if any(value in text for value in ('has been closed','target closed','browser closed')):return 'browser_closed'
        if any(value in text for value in ('net::err_','connection','proxy')):return 'network'
        return 'browser_error'
    return 'unexpected'


def is_outlook_landing(url):
    parsed=urlsplit(url)
    return parsed.scheme=='https' and parsed.hostname in {'microsoft.com','www.microsoft.com'} and bool(
        re.match(r'^/(?:[a-z]{2}-[a-z]{2}/)?microsoft-365/outlook(?:/|$)',parsed.path,re.I))


def outlook_signin_url(value):
    """Only follow Microsoft's known Outlook sign-in destinations, within the same tab."""
    try:
        url=urlsplit(value)
        if url.scheme=='https' and not url.username and not url.password and url.port in (None,443):
            if url.hostname in AUTH_HOSTS:
                return value
            if url.hostname=='outlook.live.com' and re.match(r'^/(mail|owa)(/|$)',url.path):
                return value
            if url.hostname=='go.microsoft.com' and url.path.rstrip('/')=='/fwlink/p' and parse_qs(url.query).get('linkid')==['2125442']:
                return value
    except (ValueError,TypeError):
        pass
    return OUTLOOK_SIGN_IN_URL


def extract_code(text):
    """Only accept an unambiguous Microsoft security/verification code."""
    if not re.search(r'microsoft|accountprotection\.microsoft\.com',text,re.I):
        return None
    if not re.search(r'security code|verification code|single.use code|mã (?:bảo mật|xác minh)|code de (?:sécurité|vérification)|sicherheitscode',text,re.I):
        return None
    codes=set(re.findall(r'(?<!\d)(\d{6,8})(?!\d)',text))
    codes.update(m.replace(' ','').replace('-','') for m in re.findall(r'(?<!\d)\d{3}[ -]\d{3}(?!\d)',text))
    return next(iter(codes)) if len(codes)==1 else None


def message_key(item):
    return hashlib.sha256(item['key'].encode()).hexdigest()


def public_outlook(session):
    return {row.profile_id:{'email':row.email,'status':row.status,'message':row.message,'updated_at':row.updated_at,'active':row.status in ACTIVE}
            for row in session.scalars(select(OutlookLogin))}


class WebInboxReader:
    def __init__(self,page,config):
        self.page,self.config=page,config
        self.baseline=set()
        self.ready=False
        self.started_at=None
        self.form_address=''

    async def inspect(self):
        return await self.page.evaluate(DOM_PATH.read_text(encoding='utf-8'),{'mode':'reader','email':self.config.recovery_email,'formAddress':self.form_address})

    async def open_fvia(self):
        """FVia uses a username plus a separate public domain list, not a full-email field."""
        provider=reader_provider(self.config.recovery_email)
        if not provider or provider['name']!='FVia Inboxes' or urlsplit(self.page.url).hostname!='fviainboxes.com':
            return False
        field=self.page.locator('input#username[name="username"]')
        domain_button=self.page.locator('button[aria-haspopup="listbox"]')
        if not await field.is_visible() or not await domain_button.is_visible():
            return False
        username,domain=self.config.recovery_email.rsplit('@',1)
        await field.fill(username)
        await domain_button.click(timeout=3000)
        await self.page.get_by_role('option',name=domain.lower(),exact=True).click(timeout=3000)
        await self.page.get_by_role('button',name='Get Email',exact=True).click(timeout=3000)
        self.form_address=self.config.recovery_email
        await self.page.wait_for_timeout(1500)
        return True

    async def open_inboxes(self):
        """Open the requested address using Inboxes' Add Inbox form, including existing sessions."""
        provider=reader_provider(self.config.recovery_email)
        if not provider or provider['name']!='Inboxes.com' or urlsplit(self.page.url).hostname!='inboxes.com':
            return False
        field=self.page.get_by_placeholder('Enter username',exact=True)
        if not await field.is_visible():
            for button in await self.page.get_by_role('button',name=re.compile(r'^(Get my first inbox!|Add Inbox)$')).all():
                if await button.is_visible():
                    await button.click(timeout=3000)
                    break
            await field.wait_for(state='visible',timeout=5000)
        if urlsplit(self.page.url).hostname!='inboxes.com':
            return False
        username,domain=self.config.recovery_email.rsplit('@',1)
        await field.fill(username)
        await self.page.locator('select:visible').select_option(domain.lower(),timeout=3000)
        # Scope to the form containing Cancel; the background also has an Add Inbox button.
        form=field.locator('xpath=ancestor::div[.//button[normalize-space(.)="Cancel"]][1]')
        await form.get_by_role('button',name='Add Inbox',exact=True).click(timeout=3000)
        await self.page.wait_for_timeout(1500)
        # The public site's interstitial can consume the first Add Inbox click.
        if urlsplit(self.page.url).hostname=='inboxes.com' and urlsplit(self.page.url).fragment=='google_vignette':
            await self.page.frame_locator('iframe[id*="GoogleInterstitial"]').locator('#dismiss-button').click(timeout=5000)
            if await field.is_visible():
                await form.get_by_role('button',name='Add Inbox',exact=True).click(timeout=3000)
                await self.page.wait_for_timeout(1500)
        return True

    async def prepare(self):
        if not self.config.recovery_url:
            return
        # A second Microsoft mailbox needs an isolated session; do not sign the target account out.
        if urlsplit(self.config.inbox_url()).hostname in AUTH_HOSTS|{'outlook.live.com','outlook.office.com'}:
            return
        try:
            await self.page.goto(self.config.inbox_url(),wait_until='domcontentloaded',timeout=30000)
            submitted=False
            for _ in range(20):
                data=await self.inspect()
                if data.get('inbox') and not data.get('loading'):
                    self.baseline={message_key(item) for item in data['items']}
                    self.ready=True
                    return
                if not data.get('inbox') and not submitted:
                    submitted=await self.open_fvia()
                    if not submitted:
                        submitted=await self.open_inboxes()
                    if submitted:
                        continue
                    # Some supplied links lead to a public reader's address form.
                    for field in await self.page.locator('input[type="email"],input[name="email"]').all():
                        if not await field.is_visible():
                            continue
                        buttons=await self.page.get_by_role('button',name=re.compile(r'^(View inbox|Open inbox|Read mail|Check mail|Xem hộp thư|Đọc mail|Lấy mã)$',re.I)).all()
                        for button in buttons:
                            if await button.is_visible():
                                await field.fill(self.config.recovery_email)
                                await button.click(timeout=3000)
                                submitted=True
                                break
                        if submitted:break
                await self.page.wait_for_timeout(750)
        except Exception:
            return

    def requested(self):
        if self.started_at is None:
            self.started_at=datetime.now(timezone.utc)

    async def poll(self):
        if not self.ready or self.started_at is None:
            return None
        data=await self.inspect()
        if not data.get('inbox'):
            return None
        for item in data['items']:
            key=message_key(item)
            if key in self.baseline:
                continue
            if item.get('received_at'):
                try:
                    received=datetime.fromisoformat(item['received_at'].replace('Z','+00:00'))
                    if received.tzinfo is None or received < self.started_at-timedelta(seconds=10):
                        self.baseline.add(key)
                        continue
                except ValueError:
                    continue
            code=extract_code(item['text'])
            if code:
                self.baseline.add(key)
                return code
            # Open only a newly arrived Microsoft message, never an old message.
            await self.page.locator(item['selector']).click(timeout=3000)
            await self.page.wait_for_timeout(500)
            detail=await self.inspect()
            code=extract_code(detail.get('detail',''))
            self.baseline.add(key)
            if code:
                return code
        return None


class OutlookManager:
    def __init__(self,db,state,browsers,tasks):
        self.db,self.state,self.browsers,self.tasks=db,state,browsers,tasks
        self.jobs,self.codes={},{}
        with db.session.begin() as session:
            for row in session.scalars(select(OutlookLogin).where(OutlookLogin.status.in_(ACTIVE))):
                row.status,row.message='interrupted','Lượt đăng nhập trước bị gián đoạn. Nhập lại thông tin để tiếp tục.'

    def update(self,pid,status,message):
        with self.db.session.begin() as session:
            row=session.get(OutlookLogin,pid)
            if row and (row.status!=status or row.message!=message):
                row.status,row.message,row.updated_at=status,message,now()
        self.state.emit()

    def start(self,body:OutlookStart):
        pid=body.profile_id
        if pid in self.jobs or pid in self.tasks.profile_jobs or pid in self.browsers.extension_profiles or self.browsers.status(pid) in ('Running','Starting','Queued'):
            raise ValueError('Dừng Chrome và các tác vụ của profile trước khi đăng nhập Outlook.')
        self.browsers.validate_automation_start(pid)
        with self.db.session.begin() as session:
            row=session.get(OutlookLogin,pid)
            if row is None:
                row=OutlookLogin(profile_id=pid,email=body.email)
                session.add(row)
            row.email,row.status,row.message=body.email,'queued','Đã xếp hàng đăng nhập Outlook.'
        self.browsers.outlook_profiles.add(pid)
        self.codes[pid]=asyncio.Queue(maxsize=1)
        job=asyncio.create_task(self.run(body))
        self.jobs[pid]=job
        job.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        self.state.emit()
        return {'profile_id':pid,'status':'queued'}

    def provide_code(self,pid,code):
        with self.db.session() as session:
            row=session.get(OutlookLogin,pid)
            if not row or row.status!='waiting_code' or pid not in self.codes:
                raise ValueError('Profile hiện không chờ mã Outlook.')
        queue=self.codes[pid]
        if queue.full():
            raise ValueError('Đã nhận mã; đợi Microsoft kiểm tra.')
        queue.put_nowait(code)

    async def inspect(self,page,body,*,password_entered=False):
        try:
            return await page.evaluate(DOM_PATH.read_text(encoding='utf-8'),{'mode':'login','recoveryEmail':body.recovery_email,'email':body.email,'passwordEntered':password_entered})
        except BrowserError as error:
            if navigation_in_progress(error) or isinstance(error,BrowserTimeoutError):
                return {'stage':'loading'}
            raise

    async def navigate(self,pid,page,url):
        try:
            # A committed response is enough. Microsoft can redirect or keep scripts loading after it.
            await page.goto(url,wait_until='commit',timeout=60000)
        except BrowserError as error:
            if not isinstance(error,BrowserTimeoutError) and not navigation_in_progress(error):
                raise
            # Do not repeat navigation or close a page which is still loading. The DOM loop follows it.
            self.update(pid,'opening','Outlook đang tải hoặc chuyển trang; tiếp tục chờ trang đăng nhập Microsoft.')
            self.state.log('Outlook navigation is still in progress; continuing to observe the same tab.',pid)

    def diagnose(self,pid,phase,error,page=None):
        category=failure_category(error)
        host=urlsplit(getattr(page,'url','')).hostname
        allowed=AUTH_HOSTS|{'outlook.live.com','outlook.office.com','outlook.office365.com','www.microsoft.com','microsoft.com'}
        phases={'launch','reader','opening','inspect','landing','account','email','password_choice','password','recovery','proof','code_wait','code_fill','session'}
        data={'profile_id':pid,'phase':phase if phase in phases else 'unknown','category':category,
              'host':host if host in allowed else 'other','updated_at':now()}
        try:
            directory=self.browsers.config.root/'.runtime'/'outlook-debug'
            directory.mkdir(parents=True,exist_ok=True)
            (directory/f'profile_{pid}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:
            pass
        self.state.log('Outlook sign-in stopped; sanitized step diagnostics are available in .runtime/outlook-debug/.',pid,level='error')
        return category

    @staticmethod
    async def fill_value(page,selector,value):
        if urlsplit(page.url).hostname not in AUTH_HOSTS:
            raise ValueError('Unexpected credential destination')
        try:
            await page.locator(selector).fill(value,timeout=10000)
            return True
        except BrowserError as error:
            if navigation_in_progress(error) or isinstance(error,BrowserTimeoutError):
                # Filling is idempotent. Re-inspect the new document before trying again; never resubmit here.
                await page.wait_for_timeout(500)
                return False
            raise

    @staticmethod
    async def click_submit(element):
        try:
            await element.click(timeout=3000)
            return True
        except BrowserError as error:
            # A click may already have submitted when navigation interrupts its acknowledgement.
            if navigation_in_progress(error):return True
            if isinstance(error,BrowserTimeoutError):return False
            raise

    @staticmethod
    async def submit(page,negative=False):
        try:
            return await OutlookManager._submit_current(page,negative)
        except BrowserError as error:
            if navigation_in_progress(error):return True
            if isinstance(error,BrowserTimeoutError):return False
            raise

    @staticmethod
    async def _submit_current(page,negative=False):
        selectors=['#idBtn_Back'] if negative else ['#idSIButton9','#idSubmit_SAOTCS_SendCode','#idSubmit_SAOTCC_Continue','button[type="submit"]','input[type="submit"]']
        for selector in selectors:
            for element in await page.locator(selector).all():
                if await element.is_visible():
                    return await OutlookManager.click_submit(element)
        labels=r'^(No|Non|Không|Nein)$' if negative else r'^(Next|Sign in|Send code|Verify|Continue|Yes|Suivant|Se connecter|Envoyer (?:le|un) code|Vérifier|Continuer|Oui|Tiếp theo|Đăng nhập|Gửi mã|Xác minh|Tiếp tục|Có)$'
        for element in await page.get_by_role('button',name=re.compile(labels,re.I)).all():
            if await element.is_visible():
                return await OutlookManager.click_submit(element)
        return False

    async def wait_code(self,pid,reader,page):
        self.update(pid,'waiting_code','Đang chờ mã mới từ mail khôi phục. Bạn cũng có thể nhập mã lấy thủ công.')
        for index in range(180):
            queue=self.codes[pid]
            if not queue.empty():
                return queue.get_nowait()
            if index%3==0:
                try:
                    code=await reader.poll()
                    if code:return code
                except BrowserError as error:
                    if not navigation_in_progress(error) and not isinstance(error,BrowserTimeoutError):
                        reader.ready=False
                except Exception:
                    reader.ready=False
            await page.wait_for_timeout(1000)
        raise TimeoutError()

    async def wait_for_saved_session(self,pid):
        job=self.jobs.get(pid)
        if job is None:return
        with self.db.session() as session:
            row=session.get(OutlookLogin,pid)
            signed_in=bool(row and row.status=='signed_in')
        if signed_in:
            try:
                # Success can be visible a moment before the controlled browser finishes saving/closing.
                await asyncio.wait_for(asyncio.shield(job),timeout=30)
            except TimeoutError:
                raise ValueError('Đăng nhập đã thành công nhưng Chrome còn đang lưu phiên. Chờ một chút rồi mở Outlook.') from None

    async def run(self,body):
        pid=body.profile_id
        opened=False
        page=None
        phase='launch'
        keep_browser_open=False
        attempted=set()
        try:
            async with asyncio.timeout(420):
                self.update(pid,'opening','Đang mở phiên Outlook của profile.')
                context=await self.browsers.ensure_open(pid,for_automation=True,for_outlook=True)
                opened=True
                page,inbox=await context.new_page(),await context.new_page()
                reader=WebInboxReader(inbox,body)
                phase='reader'
                await reader.prepare()
                phase='opening'
                await self.navigate(pid,page,OUTLOOK_URL)
                await page.bring_to_front()
                code_submitted=False
                pending_code=None
                loading_steps=0
                for _ in range(300):
                    phase='inspect'
                    data=await self.inspect(page,body,password_entered='password' in attempted)
                    stage=data['stage']
                    loading_steps=loading_steps+1 if stage=='loading' else 0
                    if stage=='signed_in':
                        if data.get('account_email') and data['account_email'].casefold()!=body.email.casefold():
                            self.update(pid,'failed','Outlook đang hiển thị email khác với email đã nhập. Kiểm tra lại tài khoản trong Chrome profile.')
                            return
                        if 'email' in attempted or data.get('account_email','').casefold()==body.email.casefold():
                            self.update(pid,'signed_in','Đăng nhập Outlook thành công. Phiên được giữ trong đúng Chrome profile.')
                            self.state.log('Outlook sign-in completed for this profile. No password or verification code was logged.',pid)
                            return
                        if 'account_check' not in attempted:
                            phase='account'
                            attempted.add('account_check')
                            for element in await page.locator('#O365_MainLink_Me, #mectrl_main_trigger').all():
                                if await element.is_visible():
                                    await element.click(timeout=3000)
                                    break
                        self.update(pid,'needs_user','Outlook đã mở nhưng chưa xác nhận được đúng email đã nhập. Kiểm tra tài khoản đang hiển thị trong cửa sổ Chrome.')
                    elif stage=='outlook_landing':
                        if not is_outlook_landing(page.url):
                            raise ValueError('Unexpected Outlook landing host')
                        if stage not in attempted:
                            phase='landing'
                            attempted.add(stage)
                            self.update(pid,'opening','Đang chuyển từ trang giới thiệu Outlook sang trang đăng nhập Microsoft.')
                            # The official product-page link targets _blank. Navigate this controlled tab instead.
                            await self.navigate(pid,page,outlook_signin_url(data.get('signin_url','')))
                            await page.bring_to_front()
                        else:
                            self.update(pid,'needs_user','Microsoft vẫn hiển thị trang giới thiệu Outlook. Bấm Sign in trong Chrome để tiếp tục; chương trình không lặp chuyển trang.')
                    elif stage=='rejected':
                        self.update(pid,'failed','Microsoft từ chối thông tin đăng nhập hoặc đang giới hạn tài khoản. Kiểm tra trực tiếp trong profile.')
                        return
                    elif stage=='use_password':
                        if urlsplit(page.url).hostname not in AUTH_HOSTS:
                            raise ValueError('Unexpected password-choice host')
                        if 'password' not in attempted and stage not in attempted:
                            phase='password_choice'
                            attempted.add(stage)
                            self.update(pid,'entering_password','Đang chọn Use your password để nhập mật khẩu Outlook đã cấu hình.')
                            if not await self.click_submit(page.locator(data['selector'])):
                                self.update(pid,'needs_user','Chưa chuyển được sang nhập mật khẩu. Chọn Use your password trong cửa sổ Microsoft để tiếp tục.')
                    elif stage in ('email','password','recovery') and stage not in attempted:
                        # Never fill Microsoft credentials on a recovery website or an unexpected redirect.
                        if urlsplit(page.url).hostname not in AUTH_HOSTS:
                            raise ValueError('Unexpected sign-in host')
                        phase=stage
                        if stage=='email':
                            self.update(pid,'entering_email','Đang nhập email trên Microsoft.')
                            value=body.email
                        elif stage=='password':
                            self.update(pid,'entering_password','Đang nhập mật khẩu trên Microsoft.')
                            value=body.password.get_secret_value()
                        else:
                            self.update(pid,'requesting_code','Đang xác nhận địa chỉ email khôi phục đã cấu hình.')
                            value=body.recovery_email
                        if not await self.fill_value(page,data['selector'],value):
                            continue
                        attempted.add(stage)
                        reader.requested()
                        if not await self.submit(page):
                            self.update(pid,'needs_user','Đã điền thông tin. Hoàn thành bước tiếp theo trong cửa sổ Microsoft.')
                    elif stage=='choose_recovery' and stage not in attempted:
                        phase='proof'
                        attempted.add(stage)
                        reader.requested()
                        self.update(pid,'requesting_code','Đang chọn đúng email khôi phục để nhận mã.')
                        if not await self.click_submit(page.locator(data['selector'])):
                            self.update(pid,'needs_user','Chưa chọn được phương thức xác minh. Kiểm tra bước đang hiển thị trong Chrome.')
                    elif stage=='code' and not code_submitted:
                        phase='code_wait'
                        reader.requested()
                        if pending_code is None:
                            pending_code=await self.wait_code(pid,reader,page)
                        if urlsplit(page.url).hostname not in AUTH_HOSTS:
                            raise ValueError('Unexpected code destination')
                        self.update(pid,'verifying','Đã nhận mã mới; đang xác minh với Microsoft.')
                        phase='code_fill'
                        if not await self.fill_value(page,data['selector'],pending_code):
                            continue
                        pending_code=None
                        code_submitted=True
                        await self.submit(page)
                    elif stage=='stay_signed_in' and stage not in attempted:
                        phase='session'
                        attempted.add(stage)
                        await self.submit(page,negative=not body.keep_signed_in)
                    elif stage=='needs_user':
                        self.update(pid,'needs_user','Microsoft cần thao tác trực tiếp trong Chrome (chọn phương thức xác minh hoặc hoàn thành yêu cầu bổ sung). Chương trình đang chờ bạn.')
                    elif stage=='loading' and loading_steps>=30:
                        self.update(pid,'needs_user','Trang Microsoft chưa hiển thị bước đăng nhập có thể nhận diện. Kiểm tra cửa sổ Chrome hoặc bấm Sign in để tiếp tục.')
                    await page.wait_for_timeout(1000)
                raise TimeoutError()
        except asyncio.CancelledError:
            self.update(pid,'cancelled','Đã dừng đăng nhập Outlook.')
        except TimeoutError as error:
            keep_browser_open=opened
            self.diagnose(pid,phase,error,page)
            detail=' Chrome được giữ mở để kiểm tra; dừng profile trước khi chạy lại.' if opened else ' Profile chưa mở được; kiểm tra trạng thái profile trước khi chạy lại.'
            self.update(pid,'failed','Hết thời gian chờ đăng nhập/xác minh. Không tự yêu cầu gửi lại mã.'+detail)
        except Exception as error:
            category=self.diagnose(pid,phase,error,page)
            keep_browser_open=opened and category!='browser_closed' and not isinstance(error,ValueError)
            messages={
                'browser_timeout':'Trang hoặc ô đăng nhập Microsoft chưa sẵn sàng trong thời gian chờ.',
                'navigation_changed':'Microsoft chuyển trang trong lúc đang thao tác.',
                'browser_closed':'Cửa sổ Chrome hoặc tab đăng nhập đã bị đóng.',
                'network':'Không tải được trang Microsoft do lỗi kết nối.',
                'browser_error':'Không thao tác được trên trang Microsoft hiện tại.',
                'unexpected':'Có lỗi khi xử lý bước đăng nhập; đã lưu chẩn đoán theo bước.',
            }
            phases={'launch':'mở Chrome','reader':'mở mail khôi phục','opening':'mở Outlook','inspect':'đọc trang Microsoft',
                    'landing':'chuyển sang đăng nhập','account':'xác nhận tài khoản','email':'nhập email','password_choice':'chọn đăng nhập bằng mật khẩu','password':'nhập mật khẩu',
                    'recovery':'xác nhận mail khôi phục','proof':'chọn phương thức xác minh','code_wait':'chờ mã','code_fill':'nhập mã','session':'giữ phiên đăng nhập'}
            suffix=' Chrome được giữ mở để kiểm tra. Dừng profile trước khi chạy lại.' if keep_browser_open else ' Kiểm tra trạng thái profile trước khi chạy lại.'
            self.update(pid,'failed','Bước '+phases.get(phase,'đăng nhập')+': '+messages[category]+suffix)
        finally:
            try:
                if (opened and not keep_browser_open) or pid in self.browsers.launches:
                    await self.browsers.stop(pid)
            except ValueError:
                self.update(pid,'failed','Chrome chưa đóng được. Dừng profile trước khi thử lại.')
            finally:
                self.browsers.outlook_profiles.discard(pid)
                self.codes.pop(pid,None)
                self.jobs.pop(pid,None)
                self.state.emit()

    async def stop(self,pid):
        job=self.jobs.get(pid)
        if job:
            job.cancel()
            await asyncio.gather(job,return_exceptions=True)
            if self.jobs.get(pid) is job:
                self.jobs.pop(pid,None)
                self.codes.pop(pid,None)
                self.browsers.outlook_profiles.discard(pid)
                self.update(pid,'cancelled','Đã dừng đăng nhập Outlook.')

    async def shutdown(self):
        await asyncio.gather(*(self.stop(pid) for pid in list(self.jobs)))
