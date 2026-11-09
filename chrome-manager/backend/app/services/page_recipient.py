"""Single-profile recipient workflow, driven exclusively through rendered UI."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL,uuid5

from sqlalchemy import select
from playwright.async_api import Error as BrowserError
from app.core.facebook import facebook_url
from app.core.page_recipient import PageRecipientStart,asset_path
from app.models.entities import Profile,BmProfile,FacebookAccount,PageRecipientJob,now
from app.services.facebook_identity import ensure_personal_identity,switch_to_page_identity,FacebookIdentityError
from app.services.outlook_inbox import OutlookInboxReader,OutlookInboxNeedsLogin
from app.services.page_setup import PageSetupNeedsUser

DOM=Path(__file__).with_name('page_recipient_dom.js')
FACEBOOK='https://www.facebook.com/'
OUTLOOK='https://outlook.live.com/mail/'
FB_HOSTS={'facebook.com','www.facebook.com','m.facebook.com','accountscenter.facebook.com'}
RUNNING={'queued','opening','verifying_account','finding_invite','reviewing_invite','accepting','waiting_code','entering_code','switching','setup'}
PAUSED={'needs_confirmation','needs_user','needs_outlook_login','code_timeout','needs_invite_choice','needs_setup_data'}
FIELDS=('avatar','cover','bio','address','categories')


class RecipientPause(Exception):
    def __init__(self,status,message):self.status,self.message=status,message


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


async def visible_named(page,role,pattern):
    scopes=[]
    dialogs=page.get_by_role('dialog')
    for dialog in reversed(await dialogs.all()):
        if await dialog.is_visible():scopes.append(dialog)
    scopes.append(page)
    for scope in scopes:
        for element in await scope.get_by_role(role,name=re.compile(pattern,re.I)).all():
            if await element.is_visible() and await element.is_enabled():return element
    return None


async def click_named(page,pattern):
    element=await visible_named(page,'button',pattern)
    if element is None:raise RecipientPause('needs_user','Không tìm thấy nút phù hợp trong giao diện hiện tại. Kiểm tra Chrome rồi tiếp tục.')
    await element.click(timeout=10000)


def action_pattern(data,key,fallback):
    label=(data.get('actions') or {}).get(key)
    return '^'+re.escape(label)+'$' if isinstance(label,str) and 0<len(label)<=160 else fallback


class RecipientMailbox:
    """Open Outlook only after the Facebook invitation has been reviewed."""
    def __init__(self,context,email,facebook):
        self.context,self.email,self.facebook=context,email,facebook
        self.page=self.reader=None;self.notify=None

    async def _open(self):
        if self.page is None or self.page.is_closed():
            self.page=await self.context.new_page()
            self.reader=OutlookInboxReader(self.page,self.email,notify=self.notify,require_account=False)
            try:await self.page.goto(OUTLOOK,wait_until='commit',timeout=45000)
            except BrowserError:pass
        self.reader.notify=self.notify
        return self.reader

    async def capture_baseline(self):
        # Baseline is an optional old-mail guard. A slow/sign-in page must not
        # prevent receiving the Facebook invitation or demand an avatar click.
        try:
            async with asyncio.timeout(10):
                reader=await self._open()
                return await reader.try_capture_baseline(timeout_seconds=10)
        except Exception:return None
        finally:
            try:await self.facebook.bring_to_front()
            except BrowserError:pass

    async def wait_for_code(self,since,timeout_seconds=300,manual_queue=None):
        started=time.monotonic()
        async with asyncio.timeout(timeout_seconds):
            reader=await self._open()
            return await reader.wait_for_code(since,timeout_seconds=max(0,timeout_seconds-(time.monotonic()-started)),manual_queue=manual_queue)


class PageRecipientManager:
    def __init__(self,db,state,browsers,tasks,config,*,posting_policy=None):
        self.db,self.state,self.browsers,self.tasks,self.config=db,state,browsers,tasks,config
        self.posting_policy=posting_policy
        self.jobs={};self.codes={};self.sessions={};self.semaphore=asyncio.Semaphore(1)
        asset_root=config.root/'page-assets'
        if asset_root.is_symlink() or asset_root.is_junction():raise ValueError('Thư mục page-assets phải nằm trong ứng dụng.')
        self.asset_root=asset_root.resolve();self.asset_root.mkdir(exist_ok=True)
        if not hasattr(browsers,'recipient_profiles'):browsers.recipient_profiles=set()
        with db.session.begin() as session:
            for row in session.scalars(select(PageRecipientJob)):
                if row.status in RUNNING|PAUSED:
                    row.status='interrupted';row.message='Lượt trước bị gián đoạn. Tiếp tục sẽ kiểm tra lại giao diện và bước đã hoàn tất.';row.code_deadline=None

    def public(self,row,profile_name):
        checkpoint=json.loads(row.checkpoint)
        return {'id':row.id,'profile_id':row.profile_id,'profile_name':profile_name,'status':row.status,'step':row.step,
                'message':row.message,'page_id':row.page_id,'page_name':row.page_name,'started_at':row.started_at,
                'updated_at':row.updated_at,'code_deadline':row.code_deadline,'fields':json.loads(row.fields),
                'active':row.profile_id in self.jobs,'candidates':checkpoint.get('candidates',[])}

    def list(self):
        with self.db.session() as session:
            names=dict(session.execute(select(Profile.id,Profile.name)).all())
            return [self.public(row,names.get(row.profile_id,'')) for row in session.scalars(select(PageRecipientJob).order_by(PageRecipientJob.id.desc()))]

    def row(self,pid):
        with self.db.session() as session:
            return session.scalar(select(PageRecipientJob).where(PageRecipientJob.profile_id==pid))

    def update(self,pid,**changes):
        with self.db.session.begin() as session:
            row=session.scalar(select(PageRecipientJob).where(PageRecipientJob.profile_id==pid))
            if row:
                for key,value in changes.items():setattr(row,key,value)
                row.updated_at=now()
        self.state.emit()

    def checkpoint(self,pid,**changes):
        row=self.row(pid);values=json.loads(row.checkpoint);values.update(changes)
        self.update(pid,checkpoint=json.dumps(values,ensure_ascii=False))
        return values

    def phase(self,pid,status,message):self.update(pid,status=status,step=status,message=message)

    def field(self,pid,key,status,message='',**extra):
        row=self.row(pid);fields=json.loads(row.fields)
        fields[key]={'status':status,'message':message,**extra}
        self.update(pid,fields=json.dumps(fields,ensure_ascii=False))

    def verified_setup_field(self,pid,key,status,message='',**extra):
        """Called only by PageSetupController after persisted Facebook readback."""
        if status!='completed':return self.field(pid,key,status,message,**extra)
        row=self.row(pid)
        try:
            body=PageRecipientStart.model_validate_json(row.configuration)
            checkpoint=json.loads(row.checkpoint)
            requested=body.setup.requested();input_key={'avatar':'avatar_path','cover':'cover_path'}.get(key,key)
            value=requested.get(input_key);digest=extra.get('fingerprint')
            if (body.accept_only or key not in FIELDS or not value or checkpoint.get('accepted') is not True
                    or checkpoint.get('switched') is not True or not isinstance(digest,str)
                    or not re.fullmatch(r'[a-f0-9]{64}',digest) or self.input_fingerprint(key,value)!=digest):
                raise ValueError()
            page_url=facebook_url(checkpoint.get('page_url') or '')[1]
            receipt=str(uuid5(NAMESPACE_URL,f'chrome-page-setup:{row.id}:{row.started_at}:{page_url}:{key}:{digest}'))
            result=self.posting_policy.record_chrome_setup(page_url,receipt,{key:digest}) if self.posting_policy is not None else None
            if (not isinstance(result,dict) or result.get('can_publish') is not True or result.get('information_status')!='updated'
                    or result.get('reason') is not None or not isinstance(result.get('page_id'),str)
                    or not re.fullmatch(r'[1-9][0-9]{0,31}',result['page_id'])):
                raise ValueError()
            key,_=facebook_url(page_url)
            if key.isdigit() and key!=result['page_id']:raise ValueError()
        except Exception:
            self.field(pid,key,'needs_user','Đã đọc lại mục Page nhưng chưa ghi được xác nhận đúng ID Page. Kiểm tra Page Manager rồi Tiếp tục.',**extra)
            raise PageSetupNeedsUser('Chưa ghi được xác nhận thông tin Page; điều kiện đăng vẫn bị khóa.') from None
        self.field(pid,key,status,message,**extra)

    def validate_setup(self,body):
        for key in ('avatar_path','cover_path'):
            value=getattr(body.setup,key)
            if value:asset_path(value,self.asset_root)

    def input_fingerprint(self,key,value):
        if value and key in ('avatar','cover'):
            path=asset_path(value,self.asset_root)
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            return fingerprint({'path':str(path),'sha256':digest.hexdigest()})
        return fingerprint(value)

    def start(self,body):
        self.validate_setup(body)
        pid=body.profile_id
        if pid in self.jobs or pid in self.browsers.recipient_profiles:
            raise ValueError('Profile đang có lượt nhận Page. Dừng hoặc Tiếp tục lượt hiện tại.')
        if self.browsers.status(pid) in ('Running','Starting','Queued'):
            raise ValueError('Dừng Chrome của profile trước khi bắt đầu nhận Page.')
        if pid in self.tasks.profile_jobs or pid in self.browsers.extension_profiles or pid in self.browsers.facebook_profiles or pid in self.browsers.outlook_profiles:
            raise ValueError('Profile đang được một tác vụ khác sử dụng.')
        self.browsers.validate_automation_start(pid)
        with self.db.session.begin() as session:
            profile=session.get(Profile,pid)
            if profile is None:raise ValueError('Không có profile được chọn.')
            if session.get(BmProfile,pid) is not None:raise ValueError('Chọn profile FB nhận trong Chrome Profiles, không chọn profile cầm BM.')
            row=session.scalar(select(PageRecipientJob).where(PageRecipientJob.profile_id==pid))
            old=json.loads(row.configuration) if row else {}
            old_checkpoint=json.loads(row.checkpoint) if row else {}
            target=body.expected_page_url or (old_checkpoint.get('page_url') if old_checkpoint.get('accepted') else None)
            if target and not body.expected_page_url:body=body.model_copy(update={'expected_page_url':target})
            same=row is not None and target and target==old_checkpoint.get('page_url') and old.get('outlook_email','').casefold()==body.outlook_email.casefold()
            checkpoint=old_checkpoint if same else {}
            if row is None:
                row=PageRecipientJob(profile_id=pid,configuration='{}');session.add(row)
            values=body.model_dump();requested=body.setup.requested();old_fields=json.loads(row.fields or '{}') if same else {}
            fields={}
            for key in FIELDS:
                input_key={'avatar':'avatar_path','cover':'cover_path'}.get(key,key)
                value=requested.get(input_key)
                digest=self.input_fingerprint(key,value)
                previous=old_fields.get(key,{})
                fields[key]=previous if previous.get('fingerprint')==digest and previous.get('status')=='completed' else {'status':'pending' if value else 'skipped','message':'','fingerprint':digest}
            row.configuration=json.dumps(values,ensure_ascii=False);row.checkpoint=json.dumps(checkpoint,ensure_ascii=False);row.fields=json.dumps(fields,ensure_ascii=False)
            row.status='queued';row.step='opening';row.message='Đã xếp hàng nhận Page.';row.started_at=now();row.updated_at=now();row.code_deadline=None
            if not same:row.page_id=row.page_name=None
        self._spawn(pid)
        return {'accepted':[pid]}

    def _spawn(self,pid):
        self.browsers.recipient_profiles.add(pid);self.codes.setdefault(pid,asyncio.Queue(maxsize=1))
        job=asyncio.create_task(self._run(pid));self.jobs[pid]=job
        def done(task):
            if self.jobs.get(pid) is task:self.jobs.pop(pid,None)
            if not task.cancelled():task.exception()
            self.state.emit()
        job.add_done_callback(done);self.state.emit()

    def resume(self,pid):
        if pid in self.jobs:raise ValueError('Lượt nhận Page vẫn đang chạy.')
        row=self.row(pid)
        if row is None:raise ValueError('Chưa có lượt nhận Page để tiếp tục.')
        if row.status=='completed':raise ValueError('Lượt này đã hoàn tất. Nhập dữ liệu setup rồi bắt đầu lượt mới nếu cần.')
        if pid not in self.browsers.recipient_profiles and self.browsers.status(pid) in ('Running','Starting','Queued'):
            raise ValueError('Dừng Chrome đang mở trước khi tiếp tục lượt nhận Page.')
        if pid in self.tasks.profile_jobs or any(pid in owners for owners in (self.browsers.extension_profiles,self.browsers.facebook_profiles,self.browsers.outlook_profiles)):
            raise ValueError('Profile đang được một tác vụ khác sử dụng.')
        self.browsers.validate_automation_start(pid,for_recipient=True)
        self.validate_setup(PageRecipientStart.model_validate_json(row.configuration))
        if row.status=='needs_confirmation':
            checkpoint=json.loads(row.checkpoint)
            review=checkpoint.get('review_target')
            if not review:raise ValueError('Chưa có lời mời xác định để xác nhận.')
            self.checkpoint(pid,confirmed_target=review)
        self.update(pid,status='queued',message='Đang tiếp tục; kiểm tra lại trạng thái Facebook.',code_deadline=None)
        self._spawn(pid)
        return {'accepted':[pid]}

    def submit_code(self,pid,code):
        row=self.row(pid)
        if row is None or row.status not in ('waiting_code','code_timeout'):
            raise ValueError('Profile hiện không chờ mã Facebook.')
        queue=self.codes.setdefault(pid,asyncio.Queue(maxsize=1))
        if queue.full():queue.get_nowait()
        queue.put_nowait(code)
        return {'ok':True}

    async def stop(self,pid):
        job=self.jobs.get(pid)
        if job:
            job.cancel();await asyncio.gather(job,return_exceptions=True)
            self.jobs.pop(pid,None)
        owned=pid in self.browsers.recipient_profiles
        if owned:
            await self.browsers.stop(pid)
            self.browsers.recipient_profiles.discard(pid)
        self.codes.pop(pid,None)
        self.sessions.pop(pid,None)
        row=self.row(pid)
        if row and row.status!='completed':self.update(pid,status='cancelled',message='Đã dừng lượt nhận Page; tiến độ đã xác minh được giữ lại.',code_deadline=None)

    async def shutdown(self):
        for pid in list(self.jobs):
            job=self.jobs.get(pid)
            if job:job.cancel()
        await asyncio.gather(*list(self.jobs.values()),return_exceptions=True)
        self.jobs.clear();self.codes.clear();self.sessions.clear()

    async def read(self,page,mode):
        parsed=urlsplit(page.url)
        if parsed.scheme!='https' or parsed.hostname not in FB_HOSTS:
            raise RecipientPause('needs_user','Facebook đã chuyển sang trang khác. Kiểm tra Chrome trước khi tiếp tục.')
        return await page.evaluate(DOM.read_text(encoding='utf-8'),{'mode':mode})

    async def visit(self,page,url):
        await self.tasks._rate_limit(FACEBOOK.rstrip('/'))
        try:await page.goto(url,wait_until='commit',timeout=45000)
        except BrowserError:
            if urlsplit(page.url).hostname not in FB_HOSTS|{'outlook.live.com','outlook.office.com','login.live.com','login.microsoftonline.com'}:
                raise RecipientPause('needs_user','Trang chưa tải được. Kiểm tra mạng trong Chrome rồi tiếp tục.')
        await page.wait_for_timeout(800)

    async def screenshot(self,page,pid):
        try:
            folder=self.config.root/'.runtime'/'page-recipient';folder.mkdir(parents=True,exist_ok=True)
            await page.screenshot(path=str(folder/f'profile_{pid}_facebook.png'),full_page=False,
                                  mask=[page.locator('input,textarea,[contenteditable="true"]')],timeout=5000)
        except Exception:pass

    async def verify_personal(self,page,pid):
        with self.db.session() as session:
            account=session.get(FacebookAccount,pid);profile=session.get(Profile,pid)
            expected_url=account.url if account else None;expected_name=account.name if account else None
            if profile and re.fullmatch(r'[0-9]{6,30}',profile.name):
                try:cached_key=facebook_url(expected_url)[0] if expected_url else None
                except ValueError:cached_key=None
                if cached_key!=profile.name:expected_name=None
                expected_url='https://www.facebook.com/profile.php?id='+profile.name
        await self.visit(page,FACEBOOK+'me')
        identity=await ensure_personal_identity(page,expected_url,expected_name)
        actual=identity.get('url')
        if not actual or expected_url and facebook_url(actual)[0]!=facebook_url(expected_url)[0]:
            raise FacebookIdentityError('Chưa xác nhận được UID của tài khoản FB cá nhân.')
        self.checkpoint(pid,personal_url=actual,personal_name=identity.get('name') or expected_name)
        return identity

    async def find_invite(self,page,pid,body):
        self.phase(pid,'finding_invite','Đang tìm lời mời quản lý Page trong thông báo Facebook.')
        if body.expected_page_url:return {'url':body.expected_page_url,'name':body.expected_page_name or ''}
        opener=await visible_named(page,'button',r'^(Notifications|Thông báo|Notifications?[, ].*)$')
        if opener is None:opener=await visible_named(page,'tab',r'.*(notifications|thông báo).*')
        if opener is None:raise RecipientPause('needs_user','Không tìm thấy nút thông báo. Có thể nhập link Page có lời mời để tiếp tục.')
        await opener.click(timeout=10000)
        found={}
        for _ in range(12):
            data=await self.read(page,'invites')
            for candidate in data.get('invitations',[]):
                try:key,url=facebook_url(candidate['url'])
                except ValueError:continue
                if body.expected_page_name and candidate.get('name','').casefold()!=body.expected_page_name.casefold():continue
                found[key]={'url':url,'name':candidate.get('name','')[:160],'navigation_url':candidate['url']}
            if found:break
            await page.wait_for_timeout(1000)
        candidates=list(found.values());self.checkpoint(pid,candidates=[{key:value for key,value in item.items() if key!='navigation_url'} for item in candidates])
        if len(candidates)!=1:
            raise RecipientPause('needs_invite_choice','Chưa xác định được đúng một lời mời. Nhập link Page hoặc chọn đúng Page trong thông báo rồi bắt đầu lại.')
        return candidates[0]

    async def receive(self,page,pid,body,reader):
        row=self.row(pid);checkpoint=json.loads(row.checkpoint)
        if checkpoint.get('accepted'):
            # An accepted job resumes by proving Page access. Never reopen an
            # invitation or re-submit Accept, even if stale invite UI remains.
            try:await switch_to_page_identity(page,checkpoint.get('page_url'),row.page_name)
            except asyncio.CancelledError:raise
            except Exception:
                raise RecipientPause('needs_user','Page đã được ghi nhận là đã nhận. Chưa xác nhận chuyển đúng Page; kiểm tra Chrome rồi tiếp tục. Không bấm nhận lời mời lại.') from None
            return
        next_clicked=False;accept_clicked=False;submitted_code=False
        for _ in range(90):
            data=await self.read(page,'stage')
            if data.get('blocked'):
                raise RecipientPause('needs_user','Facebook cần đăng nhập hoặc xác minh bổ sung. Xử lý trong Chrome rồi tiếp tục.')
            if data.get('accepted') or data.get('switch_now'):
                self.checkpoint(pid,accepted=True);return
            if data.get('code_error'):
                raise RecipientPause('needs_user','Facebook không chấp nhận mã. Kiểm tra mã hoặc yêu cầu mã mới trên Facebook; chương trình không tự gửi lại liên tục.')
            if submitted_code:
                # Facebook may auto-submit and briefly show the underlying
                # invitation again. Never enter Review/Accept while it settles.
                await page.wait_for_timeout(1000);continue
            if data.get('code'):
                if submitted_code:
                    await page.wait_for_timeout(1000);continue
                checkpoint=json.loads(self.row(pid).checkpoint)
                since=datetime.fromisoformat(checkpoint.get('code_requested_at') or now())
                if 'code_requested_at' not in checkpoint:self.checkpoint(pid,code_requested_at=since.isoformat())
                deadline=datetime.now(timezone.utc)+timedelta(seconds=body.code_wait_seconds)
                self.update(pid,status='waiting_code',step='waiting_code',message='Đang chờ mã Facebook trong Outlook, tối đa 5 phút.',code_deadline=deadline.isoformat())
                async def notify(message,remaining):
                    self.update(pid,message=message)
                reader.notify=notify
                try:code=await reader.wait_for_code(since,timeout_seconds=body.code_wait_seconds,manual_queue=self.codes[pid])
                except TimeoutError:raise RecipientPause('code_timeout','Hết 5 phút chưa có mã phù hợp. Có thể chờ thêm hoặc nhập mã thủ công rồi Tiếp tục.') from None
                self.phase(pid,'entering_code','Đang điền mã Facebook vào đúng ô xác minh.');self.update(pid,code_deadline=None)
                code_input=await visible_named(page,'textbox',r'^(Code|Mã|Security code|Verification code|Mã xác nhận|Mã xác minh)$')
                if code_input is None:
                    for element in await page.get_by_placeholder(re.compile(r'^(Code|Mã|Security code|Verification code|Mã xác nhận|Mã xác minh)$',re.I)).all():
                        if await element.is_visible():code_input=element;break
                if code_input is None:
                    for element in await page.locator('input[autocomplete="one-time-code"],input[name="code"],input[name="approvals_code"]').all():
                        if await element.is_visible():code_input=element;break
                if code_input is None:raise RecipientPause('needs_user','Không tìm thấy ô nhập mã trên Facebook.')
                try:await code_input.fill(code,timeout=10000)
                finally:code=None
                submitted_code=True
                for _ in range(20):
                    after=await self.read(page,'stage')
                    if after.get('blocked'):
                        raise RecipientPause('needs_user','Facebook cần đăng nhập hoặc xác minh bổ sung. Xử lý trong Chrome rồi tiếp tục.')
                    if after.get('accepted') or after.get('switch_now'):
                        self.checkpoint(pid,accepted=True);return
                    if after.get('code_error'):
                        raise RecipientPause('needs_user','Facebook không chấp nhận mã. Kiểm tra mã hoặc yêu cầu mã mới trên Facebook; chương trình không tự gửi lại liên tục.')
                    if not after.get('code'):
                        # A disappearing code dialog is submission in progress,
                        # not a missing Continue error or permission to Accept.
                        break
                    button=await visible_named(page,'button',action_pattern(after,'continue',r'^(Continue|Tiếp tục|Continuer)$'))
                    if button is not None:
                        try:await button.click(timeout=5000)
                        except BrowserError:
                            # Auto-submit can remove the dialog between finding
                            # its enabled button and dispatching this one click.
                            settled=await self.read(page,'stage')
                            if settled.get('accepted') or settled.get('switch_now'):
                                self.checkpoint(pid,accepted=True);return
                            if settled.get('code'):
                                raise RecipientPause('needs_user','Mã đã được nhập nhưng chưa xác nhận gửi thành công. Kiểm tra màn xác minh trong Chrome rồi tiếp tục.') from None
                        break
                    await page.wait_for_timeout(500)
                else:
                    raise RecipientPause('needs_user','Mã đã được nhập nhưng nút Tiếp tục chưa sẵn sàng. Kiểm tra màn xác minh trong Chrome rồi tiếp tục.')
            elif data.get('accept'):
                if accept_clicked:
                    await page.wait_for_timeout(1000);continue
                row=self.row(pid);checkpoint=json.loads(row.checkpoint)
                review={'url':checkpoint['page_url'],'control':data.get('control','unknown')}
                if review['control']=='unknown':raise RecipientPause('needs_user','Chưa đọc rõ quyền của lời mời. Kiểm tra Review invite trước khi nhận.')
                if checkpoint.get('confirmed_target')!=review:
                    self.checkpoint(pid,review_target=review)
                    raise RecipientPause('needs_confirmation','Xác nhận nhận Page '+(row.page_name or row.page_id or '')+' với quyền '+review['control']+' cho profile này trước khi bấm Accept.')
                self.phase(pid,'accepting','Đang chấp nhận đúng lời mời Page đã xác nhận.')
                await reader.capture_baseline()
                self.checkpoint(pid,code_requested_at=now())
                await click_named(page,action_pattern(data,'accept',r'^(Accept|Chấp nhận|Accepter)$'));accept_clicked=True
            elif data.get('next') and not next_clicked:
                await click_named(page,action_pattern(data,'next',r'^(Next|Tiếp|Tiếp tục|Suivant)$'));next_clicked=True
            elif data.get('review'):
                self.phase(pid,'reviewing_invite','Đang mở Review invite và đọc quyền của Page.')
                await click_named(page,action_pattern(data,'review',r'^(Review Invite|Review Invitation|Xem lại lời mời|Xem lời mời|Xem xét lời mời)$'))
            else:
                # A previously accepted invitation can omit its banner. Only a
                # verified switch into the exact Page proves actual access.
                checkpoint=json.loads(self.row(pid).checkpoint)
                if checkpoint.get('accepted'):
                    try:
                        await switch_to_page_identity(page,checkpoint['page_url'],self.row(pid).page_name)
                    except FacebookIdentityError:pass
                    else:return
            await page.wait_for_timeout(1000)
        raise RecipientPause('needs_user','Chưa xác nhận được kết quả nhận lời mời. Kiểm tra màn Facebook rồi tiếp tục; không tự bấm Accept lặp.')

    async def _run(self,pid):
        page=None;completed=False
        try:
            async with self.semaphore:
                async with asyncio.timeout(1800):
                    row=self.row(pid);body=PageRecipientStart.model_validate_json(row.configuration)
                    self.phase(pid,'opening','Đang mở đúng Chrome profile và các tab Facebook/Outlook.')
                    context=await self.browsers.ensure_open(pid,for_automation=True,for_recipient=True)
                    live=self.sessions.get(pid)
                    continuing=bool(live and live['context'] is context and not live['page'].is_closed())
                    checkpoint=json.loads(row.checkpoint)
                    preserve_challenge=False
                    if continuing:
                        page,reader=live['page'],live['reader']
                        current=await self.read(page,'stage')
                        preserve_challenge=bool(current.get('code'))
                        if not current.get('code') and not current.get('accepted') and not checkpoint.get('accepted'):
                            guard=await context.new_page()
                            try:await self.verify_personal(guard,pid)
                            finally:await guard.close()
                    else:
                        page=await context.new_page()
                        self.phase(pid,'verifying_account','Đang xác nhận và chuyển về đúng tài khoản FB cá nhân nếu cần.')
                        await self.verify_personal(page,pid)
                        reader=RecipientMailbox(context,body.outlook_email,page)
                        self.sessions[pid]={'context':context,'page':page,'reader':reader}
                    if checkpoint.get('page_url') and not body.expected_page_url:
                        body=body.model_copy(update={'expected_page_url':checkpoint['page_url'],'expected_page_name':self.row(pid).page_name})
                    invitation=await self.find_invite(page,pid,body)
                    key,url=facebook_url(invitation['url']);self.checkpoint(pid,page_url=url)
                    self.update(pid,page_id=key,page_name=invitation.get('name') or body.expected_page_name)
                    try:at_target=facebook_url(page.url)[1]==url
                    except ValueError:at_target=False
                    if not preserve_challenge and (not continuing or not at_target):await self.visit(page,invitation.get('navigation_url') or url)
                    data=await self.read(page,'stage')
                    if data.get('heading') and (not self.row(pid).page_name):self.update(pid,page_name=data['heading'][:160])
                    await self.receive(page,pid,body,reader)
                    self.phase(pid,'switching','Đã nhận lời mời; đang chuyển sang đúng danh tính Page.')
                    identity=await switch_to_page_identity(page,url,self.row(pid).page_name)
                    self.checkpoint(pid,accepted=True,switched=True)
                    if identity.get('name'):self.update(pid,page_name=identity['name'][:160])
                    if body.accept_only:
                        self.update(pid,status='completed',step='ready_setup',message='Đã nhận và switch đúng Page. Chưa cập nhật ảnh/nội dung vì đang chờ dữ liệu setup.',code_deadline=None)
                    else:
                        from app.services.page_setup import PageSetupController
                        self.phase(pid,'setup','Đang cập nhật các mục Page đã được cung cấp.')
                        controller=PageSetupController(page,url,self.row(pid).page_name,self.asset_root,
                                                       lambda key,status,message='',**extra:self.verified_setup_field(pid,key,status,message,**extra))
                        await controller.apply(body.setup,json.loads(self.row(pid).fields))
                        self.update(pid,status='completed',step='completed',message='Đã nhận Page, switch và đọc lại các mục setup đã cập nhật.',code_deadline=None)
                    completed=True
                    await self.screenshot(page,pid)
                    self.state.log('Recipient workflow finished; code/password/mail content were not logged.',pid)
        except asyncio.CancelledError:
            if self.row(pid).status!='completed':self.update(pid,status='cancelled',message='Đã dừng lượt nhận Page; các bước đã xác minh được giữ.',code_deadline=None)
        except OutlookInboxNeedsLogin as error:
            self.phase(pid,'needs_outlook_login',str(error))
        except FacebookIdentityError:
            self.phase(pid,'needs_user','Chưa xác nhận được đúng danh tính FB/Page. Kiểm tra menu chuyển tài khoản trong Chrome rồi Tiếp tục.')
        except PageSetupNeedsUser as pause:
            self.phase(pid,'needs_user',str(pause))
        except RecipientPause as pause:
            self.phase(pid,pause.status,pause.message)
        except Exception:
            self.phase(pid,'needs_user','Luồng tạm dừng ở bước hiện tại. Kiểm tra mạng và giao diện trong Chrome rồi Tiếp tục; không có mã/mật khẩu trong log.')
        finally:
            if self.row(pid) and self.row(pid).status!='waiting_code':self.update(pid,code_deadline=None)
            if page:await self.screenshot(page,pid)
            if completed:
                self.browsers.recipient_profiles.discard(pid);self.codes.pop(pid,None);self.sessions.pop(pid,None)
            self.state.emit()
