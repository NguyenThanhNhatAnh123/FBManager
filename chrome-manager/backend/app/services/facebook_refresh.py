"""Durable, one-at-a-time 24-hour refresh of cached Facebook/Page names."""
import asyncio
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from app.core.facebook import FacebookCheck
from app.models.entities import Profile,Proxy,BmProfile,BmProxy,FacebookAccount,FacebookRefreshSchedule,ReelJob

INTERVAL=timedelta(hours=24)
RETRY=timedelta(seconds=60)


def utc_now():return datetime.now(timezone.utc)


def instant(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (AttributeError,TypeError,ValueError):return None


class FacebookRefreshScheduler:
    def __init__(self,db,state,browsers,tasks,facebook):
        self.db,self.state,self.browsers,self.tasks,self.facebook=db,state,browsers,tasks,facebook
        self.active=None;self.closing=False;self._logged={}
        at=utc_now()
        with db.session.begin() as session:
            for row in session.scalars(select(FacebookRefreshSchedule).where(FacebookRefreshSchedule.result=='running')):
                row.result='interrupted'
                reserved=(instant(row.last_attempt_at)+INTERVAL) if instant(row.last_attempt_at) else instant(row.next_due_at) or at+INTERVAL
                row.next_due_at=max(instant(row.next_due_at) or reserved,reserved).isoformat()
        self._ensure_rows(at)

    def _eligible(self,session):
        return list(session.scalars(select(Profile).join(Proxy,Profile.proxy_id==Proxy.id).where(
            Profile.id.not_in(select(BmProfile.profile_id)),Proxy.id.not_in(select(BmProxy.proxy_id))).order_by(Profile.id)))

    def _initial_due(self,session,profile,at):
        account=session.get(FacebookAccount,profile.id)
        return ((instant(account.checked_at) if account else None) or instant(profile.created_at) or at)+INTERVAL

    def _ensure_rows(self,at):
        with self.db.session.begin() as session:
            for profile in self._eligible(session):
                row=session.get(FacebookRefreshSchedule,profile.id)
                if row is None:
                    session.add(FacebookRefreshSchedule(profile_id=profile.id,next_due_at=self._initial_due(session,profile,at).isoformat(),result='waiting'))
                elif row.result!='running':
                    account=session.get(FacebookAccount,profile.id)
                    checked=instant(account.checked_at) if account else None
                    attempted=instant(row.last_attempt_at)
                    if checked and (attempted is None or checked>attempted) and checked+INTERVAL>(instant(row.next_due_at) or at):
                        row.next_due_at=(checked+INTERVAL).isoformat();row.retry_after_at=None

    def _log(self,pid,message):
        try:
            at=utc_now();key=(pid,message)
            if key in self._logged and at-self._logged[key]<timedelta(minutes=10):return
            self._logged[key]=at
            self.state.log(message,pid,'warning')
        except Exception:pass

    def tick(self):
        if self.closing:return
        try:self._tick()
        except Exception:self._log(None,'Automatic Facebook name refresh is waiting; local scheduling remains available.')

    def _tick(self):
        at=utc_now()
        if self.active is not None:
            pid,job=self.active
            if job.done():self._finish(pid,job,at)
        self._ensure_rows(at)
        if self.active is not None or not getattr(self.state.settings,'facebook_auto_refresh',True) or self.facebook.jobs:return
        with self.db.session() as session:
            # Keep the shared browser capacity available for imminent Reel work.
            imminent=session.scalar(select(ReelJob.id).where(ReelJob.archived_at==None,ReelJob.status.in_(('scheduled','waiting_profile')),ReelJob.scheduled_at<=(at+timedelta(minutes=10)).isoformat()).limit(1))
            if imminent is not None:return
            rows=[]
            for profile in self._eligible(session):
                row=session.get(FacebookRefreshSchedule,profile.id)
                if row and (instant(row.next_due_at) or at)<=at and (instant(row.retry_after_at) or at)<=at:
                    rows.append((row.next_due_at,profile.id))
        for _,pid in sorted(rows):
            if not self._ready(pid):continue
            try:self.browsers.validate_automation_start(pid)
            except ValueError:
                self._defer(pid,at);continue
            start=at.replace(hour=0,minute=0,second=0,microsecond=0)
            try:body=FacebookCheck(profile_ids=[pid],names_only=True,timezone='UTC',check_date=start.date().isoformat(),day_start=start.isoformat(),day_end=(start+timedelta(days=1)).isoformat())
            except Exception:
                self._defer(pid,at);return
            with self.db.session.begin() as session:
                row=session.get(FacebookRefreshSchedule,pid)
                if row is None:continue
                previous=(row.next_due_at,row.last_attempt_at)
                row.last_attempt_at=at.isoformat();row.next_due_at=(at+INTERVAL).isoformat();row.result='running';row.retry_after_at=None
            try:result=self.facebook.enqueue(body)
            except Exception:
                job=self.facebook.jobs.get(pid)
                if job is not None:self.active=(pid,job);return
                self._defer(pid,at,previous);return
            job=self.facebook.jobs.get(pid)
            if pid not in result.get('accepted',[]):self._defer(pid,at,previous);return
            if job is None:
                with self.db.session.begin() as session:
                    row=session.get(FacebookRefreshSchedule,pid)
                    if row:row.result='error';row.last_finished_at=at.isoformat()
                self._log(pid,'Automatic Facebook name refresh could not retain its job; the daily retry was preserved.')
                return
            self.active=(pid,job);self.state.emit();return

    def _ready(self,pid):
        if self.facebook.jobs or self.browsers.status(pid) in ('Running','Starting','Queued') or pid in self.tasks.profile_jobs:return False
        if any(pid in getattr(self.browsers,key,set()) for key in ('extension_profiles','facebook_profiles','outlook_profiles','recipient_profiles','reel_profiles')):return False
        if self.browsers.resources.blocked_reason(self.state.settings):return False
        occupied=set(getattr(self.browsers,'contexts',{}))|set(getattr(self.browsers,'reserved',set()))|set(getattr(self.browsers,'launches',{}))
        occupied.update(self.tasks.profile_jobs)
        for key in ('extension_profiles','facebook_profiles','outlook_profiles','recipient_profiles','reel_profiles'):occupied.update(getattr(self.browsers,key,set()))
        if len(occupied)>=self.state.settings.max_browsers:return False
        with self.db.session() as session:
            profile=session.get(Profile,pid);proxy=session.get(Proxy,profile.proxy_id) if profile and profile.proxy_id else None
            return bool(proxy and proxy.status not in ('Failed','Timeout') and session.get(BmProfile,pid) is None and session.get(BmProxy,proxy.id) is None)

    def _defer(self,pid,at,previous=None):
        with self.db.session.begin() as session:
            row=session.get(FacebookRefreshSchedule,pid)
            if row:
                if previous is not None:row.next_due_at,row.last_attempt_at=previous
                row.result='waiting';row.retry_after_at=(at+RETRY).isoformat()
        self._log(pid,'Automatic Facebook name refresh is waiting for an available configured profile.')

    def _finish(self,pid,job,at):
        try:
            result=job.result() if not job.cancelled() else {'status':'cancelled'}
        except asyncio.CancelledError:result={'status':'cancelled'}
        except Exception:result={'status':'error'}
        if not isinstance(result,dict):result={'status':'error'}
        status=result.get('status')
        if status not in ('complete','partial','error','login_required','cancelled','deferred'):status='error'
        finished=min(instant(result.get('finished_at')) or at,at)
        with self.db.session.begin() as session:
            row=session.get(FacebookRefreshSchedule,pid)
            if row:
                row.result='waiting' if status=='deferred' else status
                if status=='deferred':
                    row.next_due_at=finished.isoformat();row.retry_after_at=(finished+RETRY).isoformat()
                else:
                    row.last_finished_at=finished.isoformat();row.next_due_at=(finished+INTERVAL).isoformat();row.retry_after_at=None
        if self.active==(pid,job):self.active=None
        self.state.emit()

    def status(self, *, include_profiles=True):
        at=utc_now()
        profiles=[];eligible_count=due_count=0;next_due=last_finished=None
        with self.db.session() as session:
            query=select(Profile.id,Profile.created_at,FacebookAccount.checked_at,FacebookRefreshSchedule).join(
                Proxy,Profile.proxy_id==Proxy.id).outerjoin(
                FacebookAccount,FacebookAccount.profile_id==Profile.id).outerjoin(
                FacebookRefreshSchedule,FacebookRefreshSchedule.profile_id==Profile.id).where(
                Profile.id.not_in(select(BmProfile.profile_id)),Proxy.id.not_in(select(BmProxy.proxy_id))).order_by(Profile.id)
            for profile_id,created_at,checked_at,row in session.execute(query.execution_options(yield_per=100)):
                due=row.next_due_at if row else ((instant(checked_at) or instant(created_at) or at)+INTERVAL).isoformat()
                finished=row.last_finished_at if row else None
                eligible_count+=1
                due_count+=int((instant(due) or at)<=at)
                next_due=due if next_due is None else min(next_due,due)
                if finished:last_finished=finished if last_finished is None else max(last_finished,finished)
                if include_profiles:
                    profiles.append({'profile_id':profile_id,'next_due_at':due,
                        'last_attempt_at':row.last_attempt_at if row else None,'last_finished_at':finished,'result':row.result if row else 'waiting'})
        return {'enabled':bool(getattr(self.state.settings,'facebook_auto_refresh',True)),'interval_hours':24,
                'running':bool(self.active is not None and not self.active[1].done()),'active_profile_id':self.active[0] if self.active is not None else None,
                'eligible_count':eligible_count,'due_count':due_count,
                'next_due_at':next_due,'last_finished_at':last_finished,'profiles':profiles}

    async def shutdown(self):
        self.closing=True
        if self.active is None:return
        pid,job=self.active
        if not job.done() and self.facebook.jobs.get(pid) is job:
            try:await self.facebook.stop(pid)
            except Exception:self._log(pid,'Automatic Facebook name refresh could not finish closing its owned job.')
        if job.done():self._finish(pid,job,utc_now())
