"""Publish one explicitly scheduled Reel using only Facebook's rendered UI."""
import asyncio
import inspect
from pathlib import Path
import re
from urllib.parse import urlsplit

from app.core.facebook import facebook_url
from app.services.facebook_identity import DOM_PATH as IDENTITY_DOM,switch_to_page_identity,FacebookIdentityError

DOM_PATH=Path(__file__).with_name('reel_publisher_dom.js')
FACEBOOK_AUTHORITY=re.compile(r'(?:www\.|m\.)?facebook\.com(?::443)?',re.IGNORECASE|re.ASCII)
STAGES={'opening','switching','uploading','processing','caption','review','publishing','verifying'}
UI_EXTENSION_KEY=re.compile(r'^(?:actor|menu|scope)_[a-z_]{1,48}$')
KNOWN_TITLES={value.casefold():value for value in (
    'Create reel','Create a reel','New reel','Edit reel','Share reel','Reel settings',
    'Tạo thước phim','Chỉnh sửa thước phim','Chia sẻ thước phim',
    'Créer un reel','Créer un réel','Modifier le reel','Partager le reel','Paramètres du reel',
)}


class ReelNeedsUser(ValueError):
    """Pause on unsupported UI or uncertain publication; never retry Publish."""


class ReelPublisher:
    PROCESSING_ATTEMPTS=600
    VERIFICATION_ATTEMPTS=90
    UI_ATTEMPTS=30

    def __init__(self,page,page_url,page_name,video_path,caption,notify=None,checkpoint=None,*,prior_checkpoint=None,posting_guard=None):
        try:self.page_url=facebook_url(page_url)[1]
        except (TypeError,ValueError):raise ReelNeedsUser('Cần chọn đúng URL Page để đăng Reel.') from None
        self.page,self.page_name=page,page_name
        self.video_path,self.caption=Path(video_path),caption or ''
        self.notify,self.checkpoint=notify,checkpoint
        self.posting_guard=posting_guard
        self.prior_checkpoint=prior_checkpoint or {}
        self.publish_attempted=bool(self.prior_checkpoint.get('publish_attempted'))
        self.success_was_visible=bool(self.prior_checkpoint.get('success_was_visible'))
        self.previous_reel_url=self.prior_checkpoint.get('previous_reel_url')
        self.actor_verified=False
        self._published_in_this_run=False;self._discovered_reel_url=None
        self._last_read={};self._stage='opening'

    async def _call(self,callback,*args):
        if callback is None:return None
        result=callback(*args)
        if inspect.isawaitable(result):result=await result
        return result

    async def _notice(self,stage,message):
        if stage in STAGES:self._stage=stage
        await self._call(self.notify,stage,message)

    async def _authorize_posting(self):
        if not callable(self.posting_guard) or await self._call(self.posting_guard) is not True:
            raise ReelNeedsUser('Page chưa được xác nhận đã đổi thông tin. Chưa tải hoặc đăng Reel.')

    async def _read(self,*,verification_only=False,discover_published_reel=False):
        try:
            parsed=urlsplit(self.page.url)
            official=parsed.scheme=='https' and FACEBOOK_AUTHORITY.fullmatch(parsed.netloc) is not None
        except (TypeError,ValueError):official=False
        if not official:
            raise ReelNeedsUser('Facebook đã chuyển sang trang khác. Kiểm tra Chrome trước khi tiếp tục đăng Reel.')
        arguments={'pageUrl':self.page_url,'pageName':self.page_name,'caption':self.caption,'pageActorVerified':self.actor_verified}
        if verification_only:arguments['verificationOnly']=True
        if verification_only and discover_published_reel:arguments['discoverPublishedReel']=True
        result=await self.page.evaluate(DOM_PATH.read_text(encoding='utf-8'),arguments)
        self._last_read=result if isinstance(result,dict) else {}
        if result.get('blocked'):raise ReelNeedsUser('Facebook cần đăng nhập hoặc xác minh. Xử lý trong Chrome rồi kiểm tra lượt Reel này.')
        return result

    async def _save_failure_diagnostics(self,error):
        """Persist bounded UI facts only; this never changes the Publish latch."""
        try:
            if not callable(self.checkpoint):return
            data=self._last_read
            count=lambda value:min(10000,max(0,value)) if type(value) is int else 0
            controls={}
            raw_buttons=data.get('buttons',[])
            for item in raw_buttons[:1000] if isinstance(raw_buttons,list) else []:
                if not isinstance(item,dict) or item.get('kind') not in {'create','app_menu','upload','next','publish'}:continue
                counts=controls.setdefault(item['kind'],{'enabled':0,'disabled':0})
                key='enabled' if item.get('enabled') is True else 'disabled'
                counts[key]+=1
            raw_captions=data.get('captions',[])
            captions=raw_captions if isinstance(raw_captions,list) else []
            files=data.get('files',[])
            file_count=data.get('file_input_count',len(files) if isinstance(files,list) else 0)
            heading=data.get('heading','')
            title=KNOWN_TITLES.get(' '.join(heading.split()).casefold()) if isinstance(heading,str) and len(heading)<=160 else None
            if isinstance(error,FacebookIdentityError):category='identity_verification'
            elif data.get('blocked') is True:category='login_or_checkpoint'
            elif not isinstance(error,ReelNeedsUser):category='unexpected_error'
            elif self.publish_attempted or self._stage=='verifying':category='publication_unverified'
            elif self._stage=='switching':category='identity_verification'
            elif self._stage=='processing' and data.get('processing') is True:category='processing_unfinished'
            elif any(kind in controls for kind in ('next','publish')) and not any(controls.get(kind,{}).get('enabled',0) for kind in ('next','publish')):category='controls_disabled'
            elif self._stage=='processing':category='processing_unfinished'
            else:category='ui_unavailable'
            raw_ui=data.get('ui_diagnostics',{})
            ui={key:count(raw_ui.get(key)) for key in ('dialog_count','all_publish_count','outside_publish_count','enabled_publish_count')} if isinstance(raw_ui,dict) else {}
            ui['scope_is_dialog']=isinstance(raw_ui,dict) and raw_ui.get('scope_is_dialog') is True
            if isinstance(raw_ui,dict):
                added=0
                for key,value in raw_ui.items():
                    if key in ui or not isinstance(key,str) or not UI_EXTENSION_KEY.fullmatch(key):continue
                    if type(value) is bool:ui[key]=value
                    elif type(value) is int:ui[key]=count(value)
                    else:continue
                    added+=1
                    if added==32:break
            diagnostic={
                'failure_stage':self._stage if self._stage in STAGES else 'opening','failure_category':category,
                'title':title,'flags':{key:data.get(key) is True for key in ('blocked','reel_context','processing','upload_ready','actor_matches','page_context_actor_matches','published','use_upload_chooser')},
                'buttons':controls,'caption_count':count(len(captions)),
                'caption_matches':[item.get('matches') is True for item in captions[:8] if isinstance(item,dict)],
                'file_count':count(file_count),'ui':ui,
            }
            await self._call(self.checkpoint,{'diagnostics':diagnostic})
        except asyncio.CancelledError:raise
        except Exception:pass

    async def _advance_ready(self,data):
        """Wait only for recognized disabled Next/Publish controls to enable."""
        for index in range(self.UI_ATTEMPTS+1):
            targets=[item for item in data.get('buttons',[]) if item.get('kind') in ('next','publish')]
            if not data.get('reel_context') or not targets or any(item.get('enabled') for item in targets):return data
            if index==self.UI_ATTEMPTS:break
            await self._wait()
            data=await self._read()
        raise ReelNeedsUser('Nút Tiếp tục hoặc Đăng Reel vẫn chưa sẵn sàng sau khi cập nhật chú thích. Kiểm tra giao diện trong Chrome.')

    async def _wait(self):await self.page.wait_for_timeout(1000)

    def _one(self,data,kind):
        values=[item for item in data.get('buttons',[]) if item.get('kind')==kind and item.get('enabled')]
        if len(values)>1:raise ReelNeedsUser('Có nhiều nút Reel trùng tên. Kiểm tra đúng hộp tạo Reel trong Chrome.')
        return values[0] if values else None

    def _locator(self,item):return self.page.locator('[data-cm-reel="'+str(item['id'])+'"]')

    async def _click(self,item):
        await self._locator(item).click(timeout=7000)

    async def _page_actor(self):
        await self._notice('switching','Đang xác nhận và chuyển sang đúng danh tính Page để tạo Reel.')
        identity=await switch_to_page_identity(self.page,self.page_url,self.page_name)
        if not self.page_name and identity.get('name'):self.page_name=identity['name']
        self.actor_verified=True
        # Reuse the narrowly recognized, non-destructive Page guide Close.
        for index in range(4):
            data=await self.page.evaluate('('+IDENTITY_DOM.read_text(encoding='utf-8')+'\n)()')
            if data.get('blocked'):raise ReelNeedsUser('Facebook cần đăng nhập hoặc xác minh trước khi tạo Reel.')
            tip=next((item for item in data.get('controls',[]) if item.get('kind')=='dismiss_tip'),None)
            if not tip:return
            if index==3:break
            await self.page.locator('[data-cm-identity="'+str(tip['id'])+'"]').click(timeout=7000)
            await self._wait()
        raise ReelNeedsUser('Hướng dẫn trên Page chưa đóng được. Đóng hướng dẫn trong Chrome rồi kiểm tra lượt Reel.')

    async def _composer(self):
        for _ in range(self.UI_ATTEMPTS):
            data=await self._read()
            if data.get('reel_context'):return data
            await self._wait()
        raise ReelNeedsUser('Chưa mở được giao diện tạo Reel. Kiểm tra Page và quyền đăng video trong Chrome.')

    async def _open_reel_entry(self):
        # The Page's Create Reel entry can live in Facebook's global Menu,
        # outside <main>. Open that menu once, only after actor verification.
        opened_menu=False
        for index in range(self.UI_ATTEMPTS):
            data=await self._read()
            create=self._one(data,'create')
            if create:
                await self._click(create)
                return await self._composer()
            if not opened_menu:
                menu=self._one(data,'app_menu')
                if menu:
                    await self._notice('opening','Đang mở Menu → Reel của Page đã xác nhận.')
                    await self._click(menu)
                    opened_menu=True
            if index+1<self.UI_ATTEMPTS:await self._wait()
        raise ReelNeedsUser('Chưa tìm thấy mục tạo Reel trong Menu của Page. Kiểm tra giao diện trong Chrome.')

    async def _upload(self,data):
        files=data.get('files',[])
        if len(files)==1:
            await self._locator(files[0]).set_input_files(str(self.video_path.resolve()),timeout=10000)
        elif files:
            raise ReelNeedsUser('Có nhiều ô tải video. Chọn đúng giao diện tạo Reel trong Chrome.')
        else:
            button=self._one(data,'upload')
            if button is None:raise ReelNeedsUser('Chưa tìm thấy ô hoặc nút tải video Reel trong giao diện hiện tại.')
            async with self.page.expect_file_chooser(timeout=10000) as pending:
                await self._click(button)
            chooser=await pending.value
            await chooser.set_files(str(self.video_path.resolve()),timeout=10000)
        await self._notice('processing','Đã chọn video; đang chờ Facebook tải và xử lý, tối đa 10 phút.')
        for index in range(self.PROCESSING_ATTEMPTS):
            data=await self._read()
            if not data.get('reel_context'):raise ReelNeedsUser('Giao diện tạo Reel đã thay đổi trong khi tải video. Kiểm tra Chrome.')
            if data.get('upload_ready') and not data.get('processing'):return data
            if index and index%30==0:await self._notice('processing','Facebook vẫn đang tải hoặc xử lý video Reel.')
            await self._wait()
        raise ReelNeedsUser('Hết 10 phút video chưa xử lý xong. Kiểm tra tải lên trong Chrome; chương trình chưa bấm đăng.')

    async def _fill_caption(self,data):
        fields=data.get('captions',[])
        if len(fields)>1:raise ReelNeedsUser('Có nhiều ô chú thích. Kiểm tra đúng hộp Reel trong Chrome.')
        if not fields:return False
        if not fields[0].get('matches'):
            await self._notice('caption','Đang nhập và xác nhận chú thích Reel đã chọn.')
            await self._locator(fields[0]).fill(self.caption,timeout=7000)
            data=await self._read()
            fields=data.get('captions',[])
            if len(fields)!=1 or not fields[0].get('matches'):
                raise ReelNeedsUser('Chưa xác nhận được chú thích Reel đã nhập đúng. Kiểm tra Chrome trước khi đăng.')
        return True

    def _proof(self,data):
        if not self.publish_attempted:return None
        current=self._reel_url(data.get('current_reel_url'))
        supplied=self.prior_checkpoint.get('verification_url') or self._discovered_reel_url
        if supplied:
            expected=self._reel_url(supplied)
            if not expected or current!=expected:return None
        elif current==self.previous_reel_url:return None
        count=data.get('reel_video_count')
        if current and data.get('reel_video_visible') is True and type(count) is int and count==1:
            return {'url':current,'evidence':'visible_reel_video','page_url':self.page_url}
        return None

    @staticmethod
    def _reel_url(value):
        try:
            parsed=urlsplit(value or '')
            match=re.fullmatch(r'/reels?/([0-9]+)/?',parsed.path)
            if parsed.scheme=='https' and FACEBOOK_AUTHORITY.fullmatch(parsed.netloc) and match:
                return 'https://www.facebook.com/reel/'+match[1]
        except (TypeError,ValueError):pass
        return None

    async def _reconcile(self):
        await self._notice('verifying','Đang kiểm tra video hiển thị trên trang Reel; không bấm đăng lại.')
        for index in range(self.VERIFICATION_ATTEMPTS):
            discover=self._published_in_this_run and not self.success_was_visible and not self.prior_checkpoint.get('verification_url') and not self._discovered_reel_url
            data=await self._read(verification_only=True,discover_published_reel=discover)
            proof=self._proof(data)
            if proof:return proof
            count=data.get('published_reel_candidate_count')
            candidate=self._reel_url(data.get('published_reel_candidate_url')) if discover and type(count) is int and count==1 else None
            if candidate and candidate!=self.previous_reel_url:
                self._discovered_reel_url=candidate
                await self.page.goto(candidate,wait_until='domcontentloaded',timeout=45000)
                continue
            if index+1<self.VERIFICATION_ATTEMPTS:await self._wait()
        raise ReelNeedsUser('Chưa thấy video Reel. Dán link Reel đã đăng vào Manager để kiểm tra; không tự đăng lại để tránh trùng video.')

    async def run(self):
        try:
            if self.publish_attempted:
                old_target=self.prior_checkpoint.get('page_url')
                if not old_target or facebook_url(old_target)[1]!=self.page_url:raise ReelNeedsUser('Tiến độ đăng Reel không khớp Page. Kiểm tra lượt đăng này.')
                return await self._reconcile()
            if not callable(self.checkpoint):raise ReelNeedsUser('Chưa có bộ lưu tiến độ đăng Reel. Chưa tải hoặc đăng video.')
            await self._authorize_posting()
            if not self.video_path.is_file():raise ReelNeedsUser('File video đã chọn không còn tồn tại. Chọn lại video trước khi đăng Reel.')
            await self._notice('opening','Đang mở và xác nhận đúng danh tính Page để tạo Reel.')
            await self.page.goto(self.page_url,wait_until='domcontentloaded',timeout=45000)
            await self._page_actor()
            data=await self._open_reel_entry()
            await self._authorize_posting()
            await self._notice('uploading','Đang chọn video cho Reel của Page.')
            data=await self._upload(data)
            for _ in range(6):
                if not data.get('reel_context'):raise ReelNeedsUser('Giao diện hiện tại không còn là tạo Reel. Chưa bấm đăng.')
                caption_available=await self._fill_caption(data)
                # Filling can re-render the controls and their local markers.
                data=await self._read()
                if data.get('processing'):
                    data=await self._upload_finished();continue
                data=await self._advance_ready(data)
                publish=self._one(data,'publish')
                if publish:
                    if self.caption and not caption_available:raise ReelNeedsUser('Chưa tìm thấy ô chú thích Reel để nhập nội dung đã chọn.')
                    if not data.get('actor_matches'):raise ReelNeedsUser('Chưa xác nhận được tên Page trong hộp đăng Reel. Chưa bấm đăng.')
                    if caption_available and not (len(data.get('captions',[]))==1 and data['captions'][0].get('matches')):
                        raise ReelNeedsUser('Chú thích Reel đã thay đổi trước khi đăng. Kiểm tra lại nội dung trong Chrome.')
                    await self._notice('publishing','Đang lưu tiến độ trước khi đăng Reel một lần.')
                    await self._authorize_posting()
                    self.success_was_visible=bool(data.get('published'));self.previous_reel_url=self._reel_url(data.get('current_reel_url'))
                    durable={'publish_attempted':True,'stage':'publishing','page_url':self.page_url,'success_was_visible':self.success_was_visible,'previous_reel_url':self.previous_reel_url}
                    if await self._call(self.checkpoint,durable) is False:raise ReelNeedsUser('Chưa lưu được tiến độ đăng Reel. Chưa bấm đăng.')
                    self.publish_attempted=True
                    data=await self._read()
                    publish=self._one(data,'publish')
                    if not publish or not data.get('reel_context') or not data.get('actor_matches') or data.get('processing'):
                        raise ReelNeedsUser('Giao diện hoặc danh tính Page đã thay đổi trước khi đăng Reel. Chưa bấm đăng; kiểm tra lượt này.')
                    if self.caption and not (len(data.get('captions',[]))==1 and data['captions'][0].get('matches')):
                        raise ReelNeedsUser('Chú thích Reel chưa khớp ngay trước khi đăng. Chưa bấm đăng; kiểm tra lượt này.')
                    self.success_was_visible=self.success_was_visible or bool(data.get('published'))
                    await self._authorize_posting()
                    # Do not retry this click, including on detachment/timeout.
                    self._published_in_this_run=True
                    try:await self._click(publish)
                    except asyncio.CancelledError:raise
                    except Exception:pass
                    return await self._reconcile()
                next_button=self._one(data,'next')
                if next_button is None:raise ReelNeedsUser('Chưa có nút Tiếp tục hoặc Đăng Reel phù hợp. Kiểm tra giao diện trong Chrome.')
                await self._click(next_button)
                await self._wait()
                data=await self._composer()
            raise ReelNeedsUser('Giao diện tạo Reel có thêm bước chưa hỗ trợ. Kiểm tra Chrome trước khi đăng.')
        except asyncio.CancelledError:raise
        except FacebookIdentityError as error:
            await self._save_failure_diagnostics(error)
            raise ReelNeedsUser(str(error)) from None
        except ReelNeedsUser as error:
            await self._save_failure_diagnostics(error)
            raise
        except Exception as error:
            await self._save_failure_diagnostics(error)
            if self.publish_attempted:raise ReelNeedsUser('Chưa xác nhận được kết quả đăng Reel. Kiểm tra Page; chương trình không tự bấm đăng lại.') from None
            raise ReelNeedsUser('Luồng Reel tạm dừng. Kiểm tra mạng, quyền Page và giao diện Chrome; chưa xác nhận đã đăng video.') from None

    async def _upload_finished(self):
        # Later editor steps can trigger another processing spinner. Wait only;
        # never call set_input_files a second time.
        for _ in range(self.PROCESSING_ATTEMPTS):
            data=await self._read()
            if data.get('reel_context') and not data.get('processing'):return data
            await self._wait()
        raise ReelNeedsUser('Facebook chưa xử lý xong video Reel. Kiểm tra Chrome trước khi đăng.')
