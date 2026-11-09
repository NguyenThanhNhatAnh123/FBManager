"""Durable local Reel schedules; the browser publishes only due user-created jobs."""
import asyncio
import hashlib
from datetime import datetime,timezone
import json
from pathlib import Path
import re
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError

from app.core.facebook import facebook_url
from app.core.reel_inputs import canonical_reel_url
from app.core.reel_time import resolve_schedule
from app.models.entities import Profile,BmProfile,FacebookPage,PageRecipientJob,ReelAsset,ReelJob,now
from app.services.reel_assets import ReelAssetStore,MAX_VIDEO_BYTES
from app.services.reel_publisher import ReelPublisher,ReelNeedsUser

RUNNING={'opening','switching','uploading','processing','caption','review','publishing','verifying'}
PENDING={'scheduled','waiting_profile'}
PAUSED={'needs_user','needs_review','interrupted'}
REMIND_UNTIL_DECISION='remind_until_decision_v1'

class ReelRequestConflict(ValueError):
    pass


class ReelScheduler:
    def __init__(self,db,state,browsers,tasks,config,*,posting_policy=None):
        self.db,self.state,self.browsers,self.tasks,self.config=db,state,browsers,tasks,config
        self.posting_policy=posting_policy
        self.assets=ReelAssetStore(config.root);self.jobs={};self.profile_jobs={};self.pages={};self.closing=False
        self.cancelling=set();self.cancelling_profiles=set()
        self._approval_cursor=0
        register=getattr(posting_policy,'register_publication_media_resolver',None)
        if callable(register):register(self.resolve_approval_media)
        register_status=getattr(posting_policy,'register_publication_status_resolver',None)
        if callable(register_status):register_status(self.resolve_publication_status)
        if not hasattr(browsers,'reel_profiles'):browsers.reel_profiles=set()
        with db.session.begin() as session:
            for row in session.scalars(select(ReelJob).where(ReelJob.status.in_(RUNNING),ReelJob.archived_at==None)):
                row.status='needs_review' if row.publish_attempted else 'scheduled'
                row.message='Lượt đăng bị gián đoạn; cần kiểm tra kết quả trên Page trước khi tiếp tục.' if row.publish_attempted else 'Sẽ tiếp tục lịch đã lưu khi Chrome trống.'
                row.updated_at=now()

    def config_public(self):
        return {'max_upload_bytes':MAX_VIDEO_BYTES,'accepted_extensions':['.mp4','.m4v','.mov'],
                'default_timezone':'America/Los_Angeles','scheduler_running':not self.closing,'background_browser':True}

    @staticmethod
    def public_asset(row):
        return {'id':row.id,'name':row.name,'size':row.size,'mime':row.mime,'created_at':row.created_at,
                'preview_url':f'/api/reels/assets/{row.id}/video'}

    def list_assets(self):
        with self.db.session() as session:return [self.public_asset(row) for row in session.scalars(select(ReelAsset).order_by(ReelAsset.created_at.desc()))]

    def add_asset(self,metadata):
        with self.db.session.begin() as session:
            row=ReelAsset(**metadata);session.add(row);session.flush()
            return self.public_asset(row)

    def asset(self,asset_id):
        with self.db.session() as session:
            row=session.get(ReelAsset,asset_id)
            if row is None:raise ValueError('Video chưa được tải lên hoặc không còn tồn tại.')
            return row

    def page_options(self,pid,*,posting_eligible_only=False):
        if type(posting_eligible_only) is not bool:raise ValueError('Bộ lọc Page đủ điều kiện chưa hợp lệ.')
        found={}
        with self.db.session() as session:
            for name,page_url in session.execute(select(FacebookPage.name,FacebookPage.url).where(FacebookPage.profile_id==pid).order_by(FacebookPage.id)):
                try:key,url=facebook_url(page_url)
                except ValueError:continue
                found[key]={'page_url':url,'page_name':name,'source':'facebook_scan'}
            recipient=session.execute(select(PageRecipientJob.page_name,func.json_extract(PageRecipientJob.checkpoint,'$.accepted'),func.json_extract(PageRecipientJob.checkpoint,'$.switched'),func.json_extract(PageRecipientJob.checkpoint,'$.page_url')).where(PageRecipientJob.profile_id==pid)).first()
            if recipient:
                name,accepted,switched,page_url=recipient
                if accepted and switched:
                    try:key,url=facebook_url(page_url or '')
                    except ValueError:pass
                    else:found[key]={'page_url':url,'page_name':name or key,'source':'received'}
        options=[{**row,**self.posting_eligibility(row['page_url'])} for row in found.values()]
        return [row for row in options if row['can_publish'] is True] if posting_eligible_only else options

    def _posting_verdict(self,page_url,expected_page_id=None):
        result={'page_id':None,'can_publish':False,'information_status':'not_updated','posting_block_reason':'page_policy_unavailable','revision':0}
        try:
            key,_=facebook_url(page_url)
            value=self.posting_policy.posting_eligibility(page_url) if self.posting_policy is not None else None
            if not isinstance(value,dict):return result
            page_id=value.get('page_id')
            if not isinstance(page_id,str) or not re.fullmatch(r'[1-9][0-9]{0,31}',page_id):
                result['posting_block_reason']='page_identity_unverified';return result
            result['page_id']=page_id
            result['information_status']='updated' if value.get('information_status')=='updated' else 'not_updated'
            if key.isdigit() and key!=page_id or expected_page_id is not None and expected_page_id!=page_id:
                result['posting_block_reason']='page_identity_unverified';return result
            reason=value.get('reason')
            result['can_publish']=value.get('can_publish') is True and result['information_status']=='updated' and value.get('reason') is None
            result['posting_block_reason']=None if result['can_publish'] else reason if reason in (
                'page_information_required','page_identity_unverified','page_policy_unavailable') else 'page_information_required'
            revision=value.get('revision')
            if type(revision) is int and 0<=revision<=2**53-1:result['revision']=revision
            if result['can_publish'] and result['revision']<1:
                result.update(can_publish=False,posting_block_reason='page_policy_unavailable')
        except Exception:pass
        return result

    def posting_eligibility(self,page_url):
        verdict=self._posting_verdict(page_url)
        return self._public_posting(verdict)

    @staticmethod
    def _public_posting(verdict):
        return {'page_id':verdict['page_id'],'posting_page_id':verdict['page_id'],'can_publish':verdict['can_publish'],
                'information_status':verdict['information_status'],'posting_block_reason':verdict['posting_block_reason'],
                'posting_revision':verdict['revision']}

    @staticmethod
    def _attempted(row):
        try:return bool(row.publish_attempted or json.loads(row.checkpoint).get('publish_attempted'))
        except (TypeError,ValueError,AttributeError):return bool(row.publish_attempted)

    def _row_verdict(self,row):
        try:
            checkpoint=json.loads(row.checkpoint)
            binding=checkpoint.get('posting_policy')
            expected=binding.get('page_id') if isinstance(binding,dict) else None
            if binding is not None and (not isinstance(expected,str) or not re.fullmatch(r'[1-9][0-9]{0,31}',expected)):
                raise ValueError()
        except (TypeError,ValueError,AttributeError):
            return {'page_id':None,'can_publish':False,'information_status':'not_updated','posting_block_reason':'page_identity_unverified','revision':0}
        return self._posting_verdict(row.page_url,expected)

    @staticmethod
    def _require_posting(verdict):
        if verdict['can_publish']:return verdict
        messages={'page_information_required':'Page chưa được xác nhận đã đổi thông tin. Hoàn tất cập nhật và xác minh thông tin Page trước khi bật đăng.',
                  'page_identity_unverified':'Chưa xác nhận được đúng ID Page đã đổi thông tin. Kiểm tra lại Page trước khi bật đăng.',
                  'page_policy_unavailable':'Chưa kiểm tra được điều kiện thông tin Page. Giữ lịch tạm dừng và thử lại khi Page Manager sẵn sàng.'}
        raise ReelNeedsUser(messages[verdict['posting_block_reason']])

    def _bind_posting(self,row,verdict):
        checkpoint=json.loads(row.checkpoint)
        checkpoint['posting_policy']={'page_id':verdict['page_id'],'revision':verdict['revision']}
        self.update(row.id,page_url='https://www.facebook.com/profile.php?id='+verdict['page_id'],
                    checkpoint=json.dumps(checkpoint,ensure_ascii=False))

    def row(self,job_id):
        with self.db.session() as session:
            row=session.get(ReelJob,job_id)
            if row is None:raise ValueError('Không có lịch đăng Reel này.')
            return row

    def public(self,row,asset):
        result={key:getattr(row,key) for key in ('id','profile_id','profile_name','page_url','page_name','asset_id','caption','scheduled_at','timezone','status','step','message','created_at','updated_at','started_at','finished_at','published_url','publish_attempted','archived_at')}
        if row.approval_policy is not None:result['approval_policy']=row.approval_policy
        owned=row.profile_id is not None and self.profile_jobs.get(row.profile_id)==row.id
        stopping=row.id in self.cancelling or row.profile_id in self.cancelling_profiles
        posting=self._row_verdict(row)
        result.update(**self._public_posting(posting))
        result['publication_approval']=self._approval(row,asset=asset)
        result.update(asset_name=asset.name,asset_size=asset.size,
                      can_cancel=not row.archived_at and not stopping and (row.status in PENDING|RUNNING|{'needs_user','interrupted'} or row.status in {'needs_review','cancelled'} and owned),
                      can_resume=not row.archived_at and not stopping and row.profile_id is not None and row.status in PAUSED|{'cancelled'} and self.profile_jobs.get(row.profile_id,row.id)==row.id and (self._attempted(row) or posting['can_publish']),
                      can_archive=not row.archived_at and self._archive_eligible(row),
                      can_restore=bool(row.archived_at) and self._cancelled_pre_publish(row),
                      verification_url=self._verification_url(row),can_verify_link=self._verify_link_eligible(row))
        if not self._attempted(row) and not result['publication_approval']['can_publish']:result['can_resume']=False
        return result

    def resolve_approval_media(self,asset_id):
        asset=self.asset(asset_id)
        return self.assets.resolve(asset.id,asset.path,asset.sha256,asset.size)

    def resolve_publication_status(self,job_id):
        with self.db.session() as session:
            pair=session.execute(select(ReelJob,ReelAsset).join(ReelAsset,ReelAsset.id==ReelJob.asset_id).where(ReelJob.id==job_id)).first()
            if pair is None:raise ValueError('Không có lịch Reel này.')
            row,asset=pair
            profile_ready=row.profile_id is not None and session.get(Profile,row.profile_id) is not None
        posting=self._row_verdict(row)
        binding=self._approval_binding(row,asset,posting)
        approval=self._approval(row,asset=asset)
        ready=(row.status in PENDING and not row.archived_at and profile_ready and posting['can_publish'] and approval['can_publish'])
        if ready:
            try:self.assets.resolve(asset.id,asset.path,asset.sha256,asset.size)
            except Exception:ready=False
        status,step=row.status,row.step
        if status in PENDING and approval['state']=='expired':status,step='needs_user','approval_expired'
        return {'binding':binding,'status':status,'step':step,'published_url':row.published_url,
            'ready':bool(ready),'page_name':row.page_name}

    @staticmethod
    def _approval_binding(row,asset,posting):
        binding={'queue':'chrome','job_id':row.id,'page_id':posting['page_id'],
            'asset_id':asset.id,'asset_sha256':asset.sha256,'asset_size':asset.size,
            'caption':row.caption,'scheduled_at':row.scheduled_at,'timezone':row.timezone,
            'actor_id':row.profile_id,'actor_revision':0}
        if row.approval_policy is not None:binding['approval_policy']=row.approval_policy
        return binding

    def _approval(self,row,*,asset=None,create=False):
        method=getattr(self.posting_policy,'publication_approval',None)
        if not callable(method):
            # Compatibility for standalone legacy/test policies. The production
            # PageWorkspace always implements the fail-closed facade.
            required=row.approval_policy==REMIND_UNTIL_DECISION
            return {'required':required,'can_publish':not required,'state':'unavailable' if required else 'not_required','reason':'publication_approval_unavailable' if required else None,'request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None}
        try:
            asset=asset or self.asset(row.asset_id)
            posting=self._row_verdict(row)
            if not posting['page_id']:raise ValueError()
            value=method(self._approval_binding(row,asset,posting),create=create)
            if not isinstance(value,dict) or type(value.get('can_publish')) is not bool:raise ValueError()
            return value
        except Exception:
            return {'required':True,'can_publish':False,'state':'unavailable','reason':'publication_approval_unavailable','request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None}

    def _require_approval(self,row):
        if not self._approval(row)['can_publish']:
            raise ReelNeedsUser('Lịch đang chờ duyệt đúng video, Page, caption và cửa sổ giờ đăng. Chưa tải hoặc đăng Reel.')

    def _backfill_approvals(self):
        with self.db.session() as session:
            candidates=list(session.scalars(select(ReelJob).where(ReelJob.status.in_(PENDING),ReelJob.archived_at==None,
                (ReelJob.scheduled_at>now())|(ReelJob.approval_policy==REMIND_UNTIL_DECISION)).order_by(ReelJob.id)))
        candidates=[row for row in candidates if not self._attempted(row)]
        ordered=[row for row in candidates if row.id>self._approval_cursor]+[row for row in candidates if row.id<=self._approval_cursor]
        for row in ordered[:100]:self._approval(row,create=True);self._approval_cursor=row.id

    @staticmethod
    def _verification_url(row):
        try:
            value=json.loads(row.checkpoint).get('verification_url')
            return canonical_reel_url(value) if value else None
        except (TypeError,ValueError,AttributeError):return None

    def _verify_link_eligible(self,row):
        if row.archived_at or not row.publish_attempted or row.status not in {'needs_review','interrupted'}:return False
        pid=row.profile_id
        if pid is None or row.id in self.jobs or row.id in self.cancelling or pid in self.cancelling_profiles:return False
        owner=self.profile_jobs.get(pid)
        if owner is not None and owner!=row.id:return False
        if owner is None and pid in self.browsers.reel_profiles:return False
        with self.db.session() as session:return session.get(Profile,pid) is not None

    def list(self,include_archived=False):
        with self.db.session() as session:
            assets={row.id:row for row in session.scalars(select(ReelAsset))}
            query=select(ReelJob)
            if not include_archived:query=query.where(ReelJob.archived_at==None)
            return [self.public(row,assets[row.asset_id]) for row in session.scalars(query.order_by(ReelJob.scheduled_at.desc(),ReelJob.id.desc()))]

    @staticmethod
    def _cancelled_pre_publish(row):
        if row.status!='cancelled' or row.publish_attempted:return False
        try:return not bool(json.loads(row.checkpoint).get('publish_attempted'))
        except (ValueError,TypeError,AttributeError):return False

    def _archive_eligible(self,row):
        if not self._cancelled_pre_publish(row) or row.id in self.jobs or row.id in self.cancelling:return False
        if row.id in self.profile_jobs.values():return False
        pid=row.profile_id
        # Another job can own this profile: archiving an older cancelled row is
        # metadata-only and must leave that live owner's Chrome untouched.
        owner=self.profile_jobs.get(pid) if pid is not None else None
        if pid is not None and owner is None and (pid in self.browsers.reel_profiles or pid in self.cancelling_profiles):return False
        return True

    def archive(self,job_id):
        with self.db.session.begin() as session:
            row=session.get(ReelJob,job_id)
            if row is None:raise ValueError('Không có lịch đăng Reel này.')
            if row.archived_at:return {'ok':True,'archived_at':row.archived_at}
            if not self._archive_eligible(row):raise ValueError('Chỉ xóa khỏi danh sách lịch đã hủy trước khi gửi đăng và không còn được lượt đó sử dụng.')
            row.archived_at=now();stamp=row.archived_at
        self.state.emit()
        return {'ok':True,'archived_at':stamp}

    def restore(self,job_id):
        with self.db.session.begin() as session:
            row=session.get(ReelJob,job_id)
            if row is None:raise ValueError('Không có lịch đăng Reel này.')
            if not self._cancelled_pre_publish(row):raise ValueError('Chỉ khôi phục lịch đã hủy trước khi gửi đăng.')
            if row.archived_at is None:return {'ok':True,'archived_at':None}
            row.archived_at=None
        self.state.emit()
        return {'ok':True,'archived_at':None}

    def update(self,job_id,**changes):
        with self.db.session.begin() as session:
            row=session.get(ReelJob,job_id)
            if row:
                for key,value in changes.items():setattr(row,key,value)
                row.updated_at=now()
        if changes.get('status')=='cancelled':
            withdraw=getattr(self.posting_policy,'withdraw_publication',None)
            if callable(withdraw):withdraw(job_id)
        self.state.emit()

    def create(self,body):
        due,zone=resolve_schedule(body)
        policy=getattr(body,'approval_policy',REMIND_UNTIL_DECISION)
        if policy not in (None,REMIND_UNTIL_DECISION):raise ValueError('Chế độ duyệt video không hợp lệ.')
        request_id=getattr(body,'request_id',None)
        semantic={'profile_id':body.profile_id,'page_url':body.page_url,'page_name':body.page_name,
            'asset_id':body.asset_id,'caption':body.caption,'scheduled_at':due.isoformat(),
            'timezone':zone,'approval_policy':policy}
        request_hash=hashlib.sha256(json.dumps(semantic,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        prior=self._request_result(request_id,request_hash)
        if prior is not None:return prior
        if due<=datetime.now(timezone.utc):raise ValueError('Chọn thời gian đăng trong tương lai.')
        asset=self.asset(body.asset_id)
        path=Path(asset.path)
        if path.is_symlink() or path.resolve().parent!=self.assets.root or not path.is_file() or path.stat().st_size!=asset.size:
            raise ValueError('Video đã chọn bị thay đổi hoặc không còn tồn tại. Tải lại video.')
        try:
            with self.db.session.begin() as session:
                profile=session.get(Profile,body.profile_id)
                if profile is None:raise ValueError('Profile không tồn tại.')
                if session.get(BmProfile,body.profile_id) is not None:raise ValueError('Chọn Chrome profile nhận Page, không chọn profile cầm BM.')
                self.browsers.validate_start(body.profile_id)
                posting=self._require_posting(self._posting_verdict(body.page_url))
                binding={'posting_policy':{'page_id':posting['page_id'],'revision':posting['revision']}}
                row=ReelJob(profile_id=profile.id,profile_name=profile.name,page_url='https://www.facebook.com/profile.php?id='+posting['page_id'],page_name=body.page_name,
                            asset_id=asset.id,caption=body.caption,scheduled_at=due.isoformat(),timezone=zone,approval_policy=policy,
                            request_id=request_id,request_hash=request_hash if request_id else None,
                            status='scheduled',step='waiting',checkpoint=json.dumps(binding),message='Đã lưu lịch. Hệ thống sẽ tự mở đúng Chrome khi đến giờ.')
                session.add(row);session.flush()
        except IntegrityError:
            # A simultaneous request can win the unique key after our first read.
            prior=self._request_result(request_id,request_hash)
            if prior is not None:return prior
            raise
        self._approval(row,asset=asset,create=True)
        result=self.public(row,asset)
        self.state.emit();return result

    def _request_result(self,request_id,request_hash):
        if request_id is None:return None
        with self.db.session() as session:
            row=session.scalar(select(ReelJob).where(ReelJob.request_id==request_id))
        if row is None:return None
        if row.request_hash!=request_hash:
            raise ReelRequestConflict('request_id đã được dùng với nội dung lịch khác. Tải lại kết quả trước khi tạo yêu cầu mới.')
        return self.public(row,self.asset(row.asset_id))

    def busy(self,pid):
        return (pid in self.cancelling_profiles or self.browsers.status(pid) in ('Running','Starting','Queued') or pid in self.tasks.profile_jobs or
                any(pid in getattr(self.browsers,key,set()) for key in ('extension_profiles','facebook_profiles','outlook_profiles','recipient_profiles','reel_profiles')))

    def tick(self):
        if self.closing:return
        self._backfill_approvals()
        with self.db.session() as session:
            due=list(session.scalars(select(ReelJob).where(ReelJob.status.in_(PENDING),ReelJob.archived_at==None,ReelJob.scheduled_at<=now()).order_by(ReelJob.scheduled_at,ReelJob.id)))
        for row in due:
            if row.id in self.cancelling or row.profile_id in self.cancelling_profiles:continue
            if not self._attempted(row) and not self._approval(row)['can_publish']:
                if row.step!='approval':self.update(row.id,step='approval',message='Đến giờ đăng; đang giữ lịch để chờ duyệt đúng nội dung và giờ đăng.')
                continue
            if row.profile_id is None:
                self.update(row.id,status='cancelled',message='Profile đã bị xóa; lịch không được chạy.',finished_at=now());continue
            with self.db.session() as session:is_bm=session.get(BmProfile,row.profile_id) is not None
            if is_bm:
                self.update(row.id,status='needs_user',message='Profile hiện thuộc BM Profiles. Chọn lại profile nhận Page trước khi đăng.');continue
            if not self._attempted(row):
                try:self._require_posting(self._row_verdict(row))
                except ReelNeedsUser as error:
                    self.update(row.id,status='needs_user',message=str(error));continue
            if len(self.jobs)>=min(2,self.state.settings.max_browsers):
                if row.status!='waiting_profile':self.update(row.id,status='waiting_profile',message='Đến giờ đăng; đang chờ lượt Chrome trước hoàn tất.')
                continue
            owned=self.profile_jobs.get(row.profile_id)==row.id
            other_busy=row.profile_id in self.tasks.profile_jobs or any(row.profile_id in getattr(self.browsers,key,set()) for key in ('extension_profiles','facebook_profiles','outlook_profiles','recipient_profiles'))
            if (self.busy(row.profile_id) and not owned) or other_busy or self.browsers.resources.blocked_reason(self.state.settings):
                if row.status!='waiting_profile':self.update(row.id,status='waiting_profile',message='Đến giờ đăng; sẽ tự mở khi Chrome và tài nguyên đã trống.')
                continue
            try:self.browsers.validate_automation_start(row.profile_id,for_reel=owned)
            except ValueError:
                self.update(row.id,status='needs_user',message='Chưa mở được Chrome. Kiểm tra proxy và cấu hình profile rồi Tiếp tục.');continue
            self._spawn(row)

    def _spawn(self,row):
        if row.archived_at:return
        pid=row.profile_id;self.browsers.reel_profiles.add(pid);self.profile_jobs[pid]=row.id
        self.update(row.id,status='opening',step='opening',message='Đến giờ đăng; đang mở Chrome chạy ngầm của đúng profile.',started_at=now(),finished_at=None)
        task=asyncio.create_task(self._run(row.id));self.jobs[row.id]=task
        def done(job):
            if self.jobs.get(row.id) is job:self.jobs.pop(row.id,None)
            if not job.cancelled():job.exception()
            self.state.emit()
        task.add_done_callback(done)

    async def _run(self,job_id):
        row=self.row(job_id);pid=row.profile_id;completed=False;page=None
        try:
            if row.archived_at:return
            asset=self.asset(row.asset_id)
            prior=json.loads(row.checkpoint)
            attempted=bool(row.publish_attempted or prior.get('publish_attempted'))
            bound_page_id=None
            if not attempted:
                self._require_approval(row)
                posting=self._require_posting(self._row_verdict(row));bound_page_id=posting['page_id']
                self._bind_posting(row,posting);row=self.row(job_id);prior=json.loads(row.checkpoint)
            if attempted:
                prior['publish_attempted']=True;prior.setdefault('page_url',row.page_url)
                self.update(job_id,publish_attempted=True)
                path=Path(asset.path)
            else:path=await asyncio.to_thread(self.assets.resolve,asset.id,asset.path,asset.sha256,asset.size)
            verification_url=canonical_reel_url(prior['verification_url']) if attempted and prior.get('verification_url') else None
            context=await self.browsers.ensure_open(pid,for_automation=True,for_reel=True)
            page=self.pages.get(job_id)
            new_page=page is None or page.is_closed()
            if new_page:
                page=await context.new_page();self.pages[job_id]=page
            if attempted and (verification_url or new_page):
                await page.goto(verification_url or row.page_url,wait_until='domcontentloaded',timeout=45000)
            async def notify(stage,message):self.update(job_id,status=stage if stage in RUNNING else 'processing',step=stage,message=message)
            def posting_guard():
                current=self.row(job_id)
                self._require_approval(current)
                verdict=self._row_verdict(current)
                if verdict['page_id']!=bound_page_id:
                    verdict={**verdict,'can_publish':False,'posting_block_reason':'page_identity_unverified'}
                self._require_posting(verdict)
                return True
            async def checkpoint(values):
                if values.get('publish_attempted') is True and not attempted:posting_guard()
                saved=self.row(job_id);current=json.loads(saved.checkpoint);current.update(values)
                current['publish_attempted']=bool(saved.publish_attempted or current.get('publish_attempted'))
                self.update(job_id,checkpoint=json.dumps(current,ensure_ascii=False),publish_attempted=current['publish_attempted'])
                return True
            runner=ReelPublisher(page,row.page_url,row.page_name,str(path),row.caption,notify,checkpoint,
                                 prior_checkpoint=prior,posting_guard=posting_guard)
            if attempted:runner.PROCESSING_ATTEMPTS=90
            async with asyncio.timeout(1800):result=await runner.run()
            evidence=result.get('evidence')
            if verification_url and (evidence not in ('published_reel_permalink','visible_reel_video') or result.get('url')!=verification_url):
                raise ReelNeedsUser('Chưa thấy video hiển thị tại đúng link Reel đã cung cấp.')
            if evidence=='visible_reel_video' and not self.row(job_id).publish_attempted:
                raise ReelNeedsUser('Chưa có tiến độ gửi đăng để kiểm tra video Reel.')
            if evidence not in ('visible_reel_published','published_reel_permalink','visible_reel_video'):
                raise ReelNeedsUser('Chưa có bằng chứng Facebook xác nhận Reel đã đăng.')
            message='Đã thấy video hiển thị tại link Reel.' if evidence=='visible_reel_video' else 'Facebook đã xác nhận Reel được đăng lên đúng Page.'
            self.update(job_id,status='completed',step='completed',message=message,
                        published_url=result.get('url'),finished_at=now())
            completed=True;self.state.log(f'Reel schedule #{job_id} completed.',pid)
        except asyncio.CancelledError:
            current=self.row(job_id)
            status='needs_review' if current.publish_attempted else 'scheduled' if self.closing else 'cancelled'
            self.update(job_id,status=status,message='Đã dừng sau bước gửi đăng; cần kiểm tra Page, không tự đăng lại.' if current.publish_attempted else 'Ứng dụng dừng; lịch đã lưu sẽ tiếp tục lần mở sau.' if self.closing else 'Đã hủy lịch trước khi gửi đăng.',finished_at=None if self.closing else now())
        except Exception as error:
            current=self.row(job_id)
            message=str(error) if isinstance(error,ReelNeedsUser) else 'Chưa hoàn tất đăng Reel. Kiểm tra video, Chrome, mạng và giao diện Page rồi Tiếp tục.'
            self.update(job_id,status='needs_review' if current.publish_attempted else 'needs_user',message=message)
        finally:
            if completed:
                try:await self.browsers.stop(pid)
                except Exception:self.update(job_id,message='Reel đã đăng; Chrome chưa đóng xong. Có thể dừng profile thủ công.')
                self.pages.pop(job_id,None)
            if completed or self.closing:
                self.browsers.reel_profiles.discard(pid);self.profile_jobs.pop(pid,None)
            elif row.archived_at and self.profile_jobs.get(pid)==job_id:
                self.browsers.reel_profiles.discard(pid);self.profile_jobs.pop(pid,None)
            self.state.emit()

    async def cancel(self,job_id):
        row=self.row(job_id)
        if row.archived_at:raise ValueError('Lịch đã xóa khỏi danh sách. Khôi phục trước khi thao tác tiếp.')
        if row.status=='completed':raise ValueError('Reel đã hoàn tất; hủy lịch không xóa Reel trên Facebook.')
        if job_id in self.cancelling or row.profile_id in self.cancelling_profiles:
            raise ValueError('Chrome của lịch này đang dừng. Chờ đóng xong trước khi thao tác tiếp.')
        # Reserve the transition before the first await. A cancelled status is
        # resumable only after its owned Chrome and cleanup have finished.
        self.cancelling.add(job_id)
        if row.profile_id is not None:self.cancelling_profiles.add(row.profile_id)
        self.state.emit()
        try:
            task=self.jobs.get(job_id)
            if task:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                if self.jobs.get(job_id) is task:self.jobs.pop(job_id,None)
            current=self.row(job_id)
            if current.status!='completed':
                self.update(job_id,status='needs_review' if current.publish_attempted else 'cancelled',message='Cần kiểm tra kết quả trên Page; không tự đăng lại.' if current.publish_attempted else 'Đã hủy lịch.',finished_at=now())
            if row.profile_id is not None and self.profile_jobs.get(row.profile_id)==job_id:
                await self.browsers.stop(row.profile_id)
                self.browsers.reel_profiles.discard(row.profile_id);self.profile_jobs.pop(row.profile_id,None)
            self.pages.pop(job_id,None)
            return {'ok':True}
        finally:
            self.cancelling.discard(job_id)
            if row.profile_id is not None:self.cancelling_profiles.discard(row.profile_id)
            self.state.emit()

    def resume(self,job_id):
        row=self.row(job_id)
        if row.archived_at:raise ValueError('Lịch đã xóa khỏi danh sách. Khôi phục trước khi tiếp tục.')
        if job_id in self.cancelling or row.profile_id in self.cancelling_profiles:
            raise ValueError('Chrome của lịch này đang dừng. Chờ đóng xong trước khi tiếp tục.')
        if row.status not in PAUSED|{'cancelled'}:raise ValueError('Lịch này chưa cần tiếp tục hoặc đã hoàn tất.')
        if row.profile_id is None:raise ValueError('Profile của lịch này đã bị xóa.')
        if row.profile_id in self.profile_jobs:
            if self.profile_jobs[row.profile_id]!=job_id:raise ValueError('Profile đang đăng một Reel khác.')
            if job_id in self.jobs:raise ValueError('Lượt này vẫn đang chạy.')
        if not self._attempted(row):
            self._require_approval(row)
            posting=self._require_posting(self._row_verdict(row));self._bind_posting(row,posting)
        self.update(job_id,status='scheduled',step='waiting',message='Đã bật lại lịch; sẽ chạy khi đến giờ và Chrome trống.',finished_at=None)
        return {'ok':True}

    def verify_link(self,job_id,url):
        canonical=canonical_reel_url(url);row=self.row(job_id)
        if not self._verify_link_eligible(row):
            raise ValueError('Chỉ xác minh link cho lượt đã gửi đăng đang chờ kiểm tra, khi lượt đó không chạy hoặc đang dừng và profile không thuộc lượt khác.')
        try:
            checkpoint=json.loads(row.checkpoint)
            if not isinstance(checkpoint,dict):raise ValueError()
        except (TypeError,ValueError):raise ValueError('Tiến độ Reel không hợp lệ. Cần kiểm tra lượt đăng trước khi xác minh link.') from None
        checkpoint['verification_url']=canonical;checkpoint['publish_attempted']=True
        checkpoint.setdefault('page_url',row.page_url)
        self.update(job_id,checkpoint=json.dumps(checkpoint,ensure_ascii=False),publish_attempted=True)
        self.resume(job_id)
        return {'ok':True,'verification_url':canonical}

    async def stop_profile(self,pid):
        job_id=self.profile_jobs.get(pid)
        if job_id is not None:await self.cancel(job_id)

    async def shutdown(self):
        self.closing=True
        for task in list(self.jobs.values()):task.cancel()
        await asyncio.gather(*list(self.jobs.values()),return_exceptions=True)
        self.jobs.clear();self.profile_jobs.clear();self.pages.clear();self.browsers.reel_profiles.clear()
