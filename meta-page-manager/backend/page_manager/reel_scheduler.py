"""Durable token-only Page Reel schedules, independent of Chrome sessions."""
import asyncio
import time
from datetime import datetime,timedelta,timezone

from .errors import AppError
from .graph import GraphError,GraphMutationUncertain
from .reel_graph import ReelGraphClient
from .publication_approvals import token_binding


ATTEMPTS=('start_attempted','upload_attempted','finish_attempted')
ACTIVE={'starting','uploading','processing','publishing','verifying'}
PAUSED={'needs_user','needs_review'}


class TokenReelScheduler:
    POLL_ATTEMPTS=60
    POLL_SECONDS=5

    def __init__(self,manager,assets,graph_factory=None,clock=None,monotonic=None):
        self.manager,self.store,self.assets=manager,manager.store,assets
        self.graph_factory=graph_factory or (lambda:ReelGraphClient(manager.version))
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.jobs={};self.page_jobs={};self.connection_jobs={};self.cancelling=set()
        self.capacity=asyncio.Semaphore(2);self.closing=False;self.started=False;self.last_error=None
        self.store.reel_recover()
        self.approvals=getattr(self.store,'publication_approvals',None)
        self._approval_cursor=0
        self.monotonic=monotonic or time.monotonic
        self._last_tick=None

    def _now(self):return self.clock().astimezone(timezone.utc).isoformat()

    def status(self):
        age=None if self._last_tick is None else max(0,self.monotonic()-self._last_tick)
        running=self.started and not self.closing and age is not None and age<=10
        return {'scheduler_running':running,'scheduler_healthy':running and self.last_error is None,
                'last_tick_seconds_ago':None if age is None else round(age,3),'active_count':len(self.jobs),
                **({'error':self.last_error} if self.last_error else {})}

    def public(self,row):
        values={key:row.get(key) for key in ('id','request_id','page_id','page_name','connection_id','connection_label','asset_id','asset_name','caption','scheduled_at','scheduled_local','timezone','fold','status','step','message','video_id','published_url','created_at','updated_at','started_at','finished_at','archived_at')}
        if row.get('publish_mode')=='on_approval':values.update(publish_mode='on_approval',approve_by=row.get('approve_by'))
        if row.get('review_batch') is not None:values['review_batch']=row['review_batch']
        if row.get('approval_policy') is not None:values['approval_policy']=row['approval_policy']
        checkpoint=row['checkpoint'];busy=row['id'] in self.jobs or row['id'] in self.cancelling
        available=not row.get('archived_at') and row['status'] not in ('completed','cancelled') and not busy
        unacknowledged=any(checkpoint.get(phase+'_attempted') and not checkpoint.get(phase+'_ack') for phase in ('start','upload','finish'))
        eligible=self.store.posting_eligibility(row['page_id'])['can_publish']
        values.update(publish_attempted=bool(checkpoint.get('finish_attempted')),
                      posting_eligible=eligible,
                      can_cancel=not row.get('archived_at') and row['status'] not in ('completed','cancelled') and row['id'] not in self.cancelling,
                      can_resume=eligible and available and row['status'] in PAUSED and not unacknowledged and not checkpoint.get('uncertain') and not checkpoint.get('finish_attempted') and not checkpoint.get('cancel_requested'),
                      can_verify=available and row['status'] in PAUSED and bool(row.get('video_id')))
        values['publication_approval']=self._approval(row)
        if not self._read_only(row) and not values['publication_approval']['can_publish']:values['can_resume']=False
        return values

    def list(self):return {'items':[self.public(row) for row in self.store.reel_jobs() if not row.get('archived_at')]}

    def _source(self,row):return self.manager._edit_source(row['page_id'],row['connection_id'],row['token_revision'])

    def _approval(self,row,*,create=False):
        try:
            approvals=self.approvals or getattr(self.store,'publication_approvals',None)
            if approvals is None:
                if row.get('approval_policy') is not None or row.get('publish_mode')=='on_approval' or row.get('review_batch') is not None:raise ValueError()
                with self.store.connect() as db:
                    exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_approval_settings'").fetchone()
                    if exists and db.execute('SELECT required FROM publication_approval_settings WHERE id=1').fetchone()[0]:raise ValueError()
                return {'required':False,'can_publish':True,'state':'not_required','reason':None,'request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None}
            binding=token_binding(row,self.store.reel_asset(row['asset_id']))
            return (approvals.ensure if create else approvals.verdict)(binding)
        except Exception:
            return {'required':True,'can_publish':False,'state':'unavailable','reason':'publication_approval_unavailable','request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None}

    def _require_approval(self,row):
        verdict=self._approval(row)
        if not verdict['can_publish']:raise AppError('Lịch chưa có duyệt hợp lệ cho đúng nội dung và giờ đăng.',verdict['reason'],409)

    def _backfill_approvals(self):
        candidates=[row for row in self.store.reel_jobs() if row['status']=='scheduled' and not row.get('archived_at')
                    and not any(row['checkpoint'].get(key) for key in ATTEMPTS)
                    and (row['scheduled_at']>self._now() or row.get('publish_mode')=='on_approval' or row.get('approval_policy')=='remind_until_decision_v1')]
        candidates.sort(key=lambda row:row['id'])
        ordered=[row for row in candidates if row['id']>self._approval_cursor]+[row for row in candidates if row['id']<=self._approval_cursor]
        for row in ordered[:100]:self._approval(row,create=True);self._approval_cursor=row['id']

    def _guard(self,job_id,*,verification=False):
        row=self.store.reel_job(job_id)
        if self.closing or row['id'] in self.cancelling or row['checkpoint'].get('cancel_requested') and not verification or row.get('archived_at'):raise asyncio.CancelledError()
        self._source(row)
        if not verification:
            self.store.ensure_posting_ready(row['page_id']);self._require_approval(row)
        return row

    @staticmethod
    def _read_only(row):
        checkpoint=row['checkpoint']
        return bool(checkpoint.get('uncertain') or checkpoint.get('finish_attempted') or any(
            checkpoint.get(phase+'_attempted') and not checkpoint.get(phase+'_ack') for phase in ('start','upload')))

    def _pause(self,job_id,message,*,uncertain=False):
        return self.store.reel_checkpoint(job_id,{'uncertain':True} if uncertain else {},status='needs_review',step='review',message=message,finished_at=None)

    def tick(self):
        if self.closing:return
        self.started=True
        self._last_tick=self.monotonic()
        try:
            self._backfill_approvals()
            for row in self.store.reel_due(self._now()):
                if len(self.jobs)>=2:break
                if row['id'] in self.jobs or row['id'] in self.cancelling:continue
                verdict=self._approval(row)
                if not self._read_only(row) and not verdict['can_publish']:
                    if row.get('publish_mode')=='on_approval' and verdict.get('reason')=='publication_approval_expired':
                        self.store.reel_update(row['id'],status='needs_user',step='approval_expired',message='Đã qua hạn duyệt đăng ngay; giữ video, không tự đăng muộn hoặc tạo lại yêu cầu duyệt.')
                    elif row['step']!='approval':
                        message='Video sẵn sàng; sẽ đăng ngay sau khi bạn duyệt.' if row.get('publish_mode')=='on_approval' else 'Đến giờ đăng; đang giữ lịch để chờ duyệt đúng nội dung và giờ đăng.'
                        self.store.reel_update(row['id'],step='approval',message=message)
                    continue
                if row['page_id'] in self.page_jobs or row['connection_id'] in self.connection_jobs or self.manager._page_lock(row['page_id']).locked() or self.manager._lock(row['connection_id']).locked():
                    if row['step']!='waiting_page':self.store.reel_update(row['id'],step='waiting_page',message='Đến giờ đăng; đang chờ Page hoặc nguồn token hoàn tất thao tác khác.')
                    continue
                try:
                    self._source(row)
                    if not self._read_only(row):self.store.ensure_posting_ready(row['page_id'])
                except AppError as error:
                    if error.code=='page_information_required':
                        self.store.reel_update(row['id'],status='needs_user',step='page_information',message=error.detail);continue
                    self.store.reel_update(row['id'],status='needs_user',step='source',message='Nguồn token hoặc quyền Page đã thay đổi. Kiểm tra kết nối; không tự chọn token khác.');continue
                self._spawn(row)
            self.last_error=None
        except Exception:self.last_error='Bộ lập lịch token chưa đọc được tiến độ. Lịch đã lưu được giữ để kiểm tra lại.'

    def _spawn(self,row,*,verify_only=False):
        jid=row['id'];pid=row['page_id'];cid=row['connection_id']
        self.page_jobs[pid]=jid;self.connection_jobs[cid]=jid
        coroutine=None
        try:
            self.store.reel_update(jid,status='verifying' if verify_only else 'starting',step='verifying' if verify_only else 'starting',
                                   message='Đang kiểm tra trạng thái Meta; không gửi lại video hoặc lệnh đăng.' if verify_only else 'Đến giờ đăng; đang xác nhận đúng Page và video đã chọn.',started_at=row.get('started_at') or self._now(),finished_at=None)
            coroutine=self._run(jid,verify_only=verify_only)
            task=asyncio.create_task(coroutine);self.jobs[jid]=task
        except Exception:
            if coroutine:coroutine.close()
            if self.page_jobs.get(pid)==jid:self.page_jobs.pop(pid,None)
            if self.connection_jobs.get(cid)==jid:self.connection_jobs.pop(cid,None)
            raise
        def done(completed):
            if self.jobs.get(jid) is completed:self.jobs.pop(jid,None)
            if self.page_jobs.get(pid)==jid:self.page_jobs.pop(pid,None)
            if self.connection_jobs.get(cid)==jid:self.connection_jobs.pop(cid,None)
            if not completed.cancelled():completed.exception()
        task.add_done_callback(done)

    async def _wait(self):await asyncio.sleep(self.POLL_SECONDS)

    async def _poll(self,job_id,graph,token,*,published):
        for index in range(self.POLL_ATTEMPTS):
            row=self._guard(job_id,verification=published)
            data=await graph.reel_status(token,row['page_id'],row['video_id'])
            self._guard(job_id,verification=published)
            if data.get('video_id')!=row['video_id'] or data.get('failed') or data.get('owner_conflict'):raise AppError('Meta chưa xử lý được video của đúng Page đã chọn. Kiểm tra trạng thái trên Page.','reel_processing_failed',409)
            if data.get('published') and data.get('owner_matches') and data.get('published_url'):
                return data
            # An unpublished upload can omit `from`. Its ID is already bound
            # to the exact Page's acknowledged start and verified Page token.
            # Explicit foreign ownership still blocks finish; final proof
            # always requires the exact Page owner.
            if not published and data.get('upload_complete'):return data
            if index+1<self.POLL_ATTEMPTS:await self._wait()
        return None

    async def _verify(self,job_id,graph,token):
        row=self.store.reel_job(job_id)
        if not row.get('video_id'):
            self._pause(job_id,'Chưa có mã video từ Meta. Không tạo phiên hoặc gửi lại bước chưa rõ kết quả.');return
        self.store.reel_update(job_id,status='verifying',step='verifying',message='Đang kiểm tra trạng thái xuất bản của đúng video; không bấm đăng lại.')
        proof=await self._poll(job_id,graph,token,published=True)
        if proof:
            self.store.reel_update(job_id,status='completed',step='completed',message='Meta xác nhận Reel đã xuất bản trên đúng Page.',published_url=proof['published_url'],finished_at=self._now())
        else:self._pause(job_id,'Chưa có xác nhận xuất bản từ Meta. Chỉ kiểm tra lại trạng thái; không tự gửi lại lệnh đăng.')

    async def _run(self,job_id,*,verify_only=False):
        initial=self.store.reel_job(job_id)
        try:
            async with self.capacity,self.manager._page_lock(initial['page_id']),self.manager._lock(initial['connection_id']):
                checkpoint=self.store.reel_job(job_id)['checkpoint']
                read_only=verify_only or self._read_only(self.store.reel_job(job_id))
                row=self._guard(job_id,verification=read_only);checkpoint=row['checkpoint']
                source=self._source(row)
                async with self.graph_factory() as graph:
                    token,_=await self.manager._edit_token(graph,row['page_id'],source)
                    row=self._guard(job_id,verification=read_only);checkpoint=row['checkpoint']
                    if verify_only or checkpoint.get('uncertain') or checkpoint.get('finish_attempted'):
                        await self._verify(job_id,graph,token);return
                    if any(checkpoint.get(phase+'_attempted') and not checkpoint.get(phase+'_ack') for phase in ('start','upload')):
                        self._pause(job_id,'Một bước trước chưa có phản hồi chắc chắn. Chỉ kiểm tra video đã tạo, không gửi lại.',uncertain=True)
                        await self._verify(job_id,graph,token);return
                    asset=await asyncio.to_thread(self.assets.resolve,row['asset_id'])
                    binding={'asset_sha256':asset['sha256'],'asset_size':asset['size'],'page_id':row['page_id']}
                    row=self.store.reel_checkpoint(job_id,binding);self._guard(job_id)
                    if not row['checkpoint'].get('start_attempted'):
                        self.store.reel_checkpoint(job_id,{'start_attempted':True},status='starting',step='starting',message='Đang tạo phiên video cho đúng Page.')
                        video_id=await graph.start_reel(token,row['page_id'])
                        version_reader=getattr(graph,'upload_version',None)
                        version=version_reader(video_id) if callable(version_reader) else None
                        patch={'start_ack':True,**({'upload_version':version} if isinstance(version,str) else {})}
                        row=self.store.reel_checkpoint(job_id,patch,video_id=video_id)
                        self._guard(job_id)
                    if not row.get('video_id'):raise AppError('Tiến độ thiếu mã video đã tạo. Không tạo lại phiên.','reel_checkpoint_invalid',409)
                    if not row['checkpoint'].get('upload_attempted'):
                        self._guard(job_id)
                        self.store.reel_checkpoint(job_id,{'upload_attempted':True},status='uploading',step='uploading',message='Đang tải video đã chọn tới Meta.')
                        upload_options={'upload_version':row['checkpoint']['upload_version']} if row['checkpoint'].get('upload_version') else {}
                        await graph.upload_reel(token,row['video_id'],asset['path'],asset['size'],sha256=asset['sha256'],before_send=lambda:self._guard(job_id),**upload_options)
                        row=self.store.reel_checkpoint(job_id,{'upload_ack':True})
                        self._guard(job_id)
                    self.store.reel_update(job_id,status='processing',step='processing',message='Đang xác nhận Meta đã nhận đủ video trước khi gửi đăng.')
                    ready=await self._poll(job_id,graph,token,published=False)
                    if ready is None:
                        self._pause(job_id,'Meta chưa xác nhận nhận đủ video. Có thể Tiếp tục để kiểm tra; không tải lại video.');return
                    if ready.get('published'):
                        await self._verify(job_id,graph,token);return
                    row=self._guard(job_id)
                    since=(self.clock().astimezone(timezone.utc)-timedelta(hours=24)).isoformat()
                    if self.store.reel_publish_count(row['page_id'],since)>=30:
                        self.store.reel_update(job_id,status='needs_user',step='rate_limit',message='Page đã có 30 lượt gửi đăng qua ứng dụng trong 24 giờ. Chờ hết giới hạn rồi Tiếp tục.');return
                    self.store.reel_checkpoint(job_id,{'finish_attempted':True},status='publishing',step='publishing',message='Đang gửi lệnh xuất bản Reel một lần.')
                    await graph.finish_reel(token,row['page_id'],row['video_id'],row['caption'])
                    self.store.reel_checkpoint(job_id,{'finish_ack':True})
                    self._guard(job_id,verification=True)
                    await self._verify(job_id,graph,token)
        except asyncio.CancelledError:
            row=self.store.reel_job(job_id);checkpoint=row['checkpoint']
            uncertain=any(checkpoint.get(phase+'_attempted') and not checkpoint.get(phase+'_ack') for phase in ('start','upload','finish'))
            if self.closing and not any(checkpoint.get(name) for name in ATTEMPTS):
                self.store.reel_update(job_id,status='scheduled',step='waiting',message='Ứng dụng đã dừng trước khi gửi video; lịch sẽ tiếp tục khi mở lại.')
            else:
                status='needs_review' if checkpoint.get('finish_attempted') or uncertain or self.closing else 'cancelled'
                self.store.reel_checkpoint(job_id,{'uncertain':True} if uncertain else {},status=status,step='stopped',message='Đã dừng lượt chạy. Tiến độ đã gửi tới Meta được giữ; không tự gửi lại.',finished_at=None)
        except GraphMutationUncertain:self._pause(job_id,'Mất xác nhận từ Meta sau khi gửi yêu cầu. Chỉ kiểm tra trạng thái, không gửi lại.',uncertain=True)
        except (GraphError,AppError) as error:
            row=self.store.reel_job(job_id)
            attempted=any(row['checkpoint'].get(name) for name in ATTEMPTS)
            if isinstance(error,AppError) and error.code.startswith('publication_'):
                self.store.reel_update(job_id,status='needs_review' if attempted else 'scheduled',step='approval',message='Đang giữ lịch; chưa có duyệt hợp lệ cho đúng nội dung và cửa sổ giờ đăng.')
            elif isinstance(error,AppError) and error.code=='page_information_required':
                self.store.reel_update(job_id,status='needs_review' if attempted else 'needs_user',step='page_information',message=error.detail)
            else:self.store.reel_update(job_id,status='needs_review' if attempted else 'needs_user',step='review',message='Chưa hoàn tất Reel. Kiểm tra nguồn token, đúng Page, video và trạng thái Meta; không tự chọn nguồn khác.')
        except Exception:
            row=self.store.reel_job(job_id)
            attempted=any(row['checkpoint'].get(name) for name in ATTEMPTS)
            self.store.reel_checkpoint(job_id,{'uncertain':True} if attempted else {},status='needs_review' if attempted else 'needs_user',step='review',message='Lượt Reel bị gián đoạn. Tiến độ được giữ để kiểm tra; không tự gửi lại yêu cầu đã gửi.')

    def _available(self,row):
        if self.closing or row.get('archived_at') or row['status']=='completed' or row['id'] in self.jobs or row['id'] in self.cancelling:
            raise AppError('Lượt Reel đang chạy, đã hoàn tất hoặc ứng dụng đang dừng.','reel_busy',409)
        if row['page_id'] in self.page_jobs or row['connection_id'] in self.connection_jobs:
            raise AppError('Page hoặc nguồn token đang được lịch khác sử dụng.','reel_busy',409)
        self._source(row)

    async def resume(self,job_id):
        row=self.store.reel_job(job_id);self._available(row)
        self.store.ensure_posting_ready(row['page_id'])
        self._require_approval(row)
        if not self.public(row)['can_resume']:raise AppError('Lượt này chỉ được kiểm tra trạng thái hoặc đã hủy. Không gửi lại bước chưa rõ kết quả.','reel_verify_only',409)
        self.store.reel_update(job_id,status='scheduled',step='waiting',message='Đã tiếp tục lịch; sẽ chỉ thực hiện bước chưa từng gửi khi Page và token sẵn sàng.',finished_at=None)
        return self.public(self.store.reel_job(job_id))

    async def verify(self,job_id):
        row=self.store.reel_job(job_id);self._available(row)
        if not self.public(row)['can_verify']:raise AppError('Lượt này chưa có mã video hoặc chưa cần kiểm tra lại.','reel_not_verifiable',409)
        if len(self.jobs)>=2:raise AppError('Hai lượt đang chạy; chờ trước khi kiểm tra thêm.','reel_busy',409)
        self._spawn(row,verify_only=True)
        return self.public(self.store.reel_job(job_id))

    async def cancel(self,job_id):
        row=self.store.reel_job(job_id)
        if row['status']=='completed' or row.get('archived_at'):raise AppError('Reel đã hoàn tất; hủy lịch không xóa nội dung trên Meta.','reel_completed',409)
        if job_id in self.cancelling:raise AppError('Lượt Reel đang dừng.','reel_busy',409)
        self.cancelling.add(job_id)
        try:
            self.store.reel_checkpoint(job_id,{'cancel_requested':True})
            task=self.jobs.get(job_id)
            if task:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            current=self.store.reel_job(job_id)
            if current['status']!='completed':
                uncertain=any(current['checkpoint'].get(phase+'_attempted') and not current['checkpoint'].get(phase+'_ack') for phase in ('start','upload','finish'))
                self.store.reel_checkpoint(job_id,{'uncertain':True} if uncertain else {},status='needs_review' if current['checkpoint'].get('finish_attempted') or uncertain else 'cancelled',step='stopped',message='Đã dừng lịch. Không xóa hoặc gửi lại nội dung đã tới Meta.',finished_at=self._now())
        finally:self.cancelling.discard(job_id)
        return self.public(self.store.reel_job(job_id))

    async def shutdown(self):
        self.closing=True
        tasks=list(self.jobs.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.jobs.clear();self.page_jobs.clear();self.connection_jobs.clear()
