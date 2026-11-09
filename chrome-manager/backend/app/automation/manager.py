import asyncio
import json
import uuid
from sqlalchemy import select
from app.core.schemas import TaskInput, origin
from app.core.security import encrypt_credentials, decrypt_credentials
from app.models.entities import Task, TaskRun, Profile, now

ACTIVE = ('Pending', 'Running')

def encode_task(config):
    return encrypt_credentials('task', config.model_dump_json())

def decode_task(value):
    return TaskInput.model_validate_json(decrypt_credentials(value)[1])

class TaskManager:
    def __init__(self, db, state, browsers, config):
        self.db, self.state, self.browsers, self.config = db, state, browsers, config
        self.jobs, self.profile_jobs, self.origin_locks, self.origin_times = {}, {}, {}, {}

    def recover(self):
        with self.db.session.begin() as session:
            for run in session.scalars(select(TaskRun).where(TaskRun.status.in_(ACTIVE))):
                run.status = 'Failed' if run.status == 'Running' else 'Cancelled'
                run.error_message = 'Application stopped before this run finished. Run it again explicitly.'
                run.finished_at = now()
            for task in session.scalars(select(Task).where(Task.status.in_(ACTIVE))):
                task.status = 'Interrupted'

    def validate_origins(self, config):
        for action in config.actions:
            if action.url and origin(action.url) not in self.state.settings.allowed_origins:
                raise ValueError('Approve the target origin in Settings before running automation.')

    def enqueue(self, task_id, profile_ids):
        with self.db.session.begin() as session:
            task = session.get(Task, task_id)
            if not task:
                raise ValueError('Task does not exist.')
            config = decode_task(task.configuration)
            self.validate_origins(config)
            busy = set(session.scalars(select(TaskRun.profile_id).where(TaskRun.status.in_(ACTIVE))))
            runs = []
            batch_id = uuid.uuid4().hex
            for pid in profile_ids:
                profile = session.get(Profile, pid)
                if not profile:
                    raise ValueError(f'Profile #{pid} does not exist.')
                if pid in self.browsers.extension_profiles:
                    raise ValueError(f'Close the Authenticator installation tab in profile #{pid} before running automation.')
                if pid in busy:
                    raise ValueError(f'Profile #{pid} already has a queued or running task.')
                self.browsers.validate_automation_start(pid)
                runs.append(TaskRun(task_id=task_id, profile_id=pid, profile_name=profile.name, batch_id=batch_id))
            session.add_all(runs)
            session.flush()
            task.status = 'Pending'
            ids = [(run.id, run.profile_id) for run in runs]
        for run_id, pid in ids:
            job = asyncio.create_task(self._execute(run_id, pid, task_id, config))
            self.jobs[run_id] = job
            self.profile_jobs[pid] = run_id
        self.state.log(f'Task #{task_id}: queued {len(ids)} runs.')
        return [i for i, _ in ids]

    def _update(self, run_id, **fields):
        with self.db.session.begin() as session:
            row = session.get(TaskRun, run_id)
            if row:
                for key, value in fields.items():
                    setattr(row, key, value)
        self.state.emit()

    def _refresh_task(self, task_id):
        with self.db.session.begin() as session:
            runs = list(session.scalars(select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.id)))
            task = session.get(Task, task_id)
            if task and runs:
                active = [r.status for r in runs if r.status in ACTIVE]
                statuses = active or [r.status for r in runs if r.batch_id == runs[-1].batch_id]
                task.status = ('Running' if 'Running' in statuses else 'Pending' if 'Pending' in statuses else
                               'Failed' if 'Failed' in statuses else 'Cancelled' if 'Cancelled' in statuses else 'Completed')

    async def _rate_limit(self, target_origin):
        lock = self.origin_locks.setdefault(target_origin, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            delay = self.state.settings.action_delay_ms / 1000
            await asyncio.sleep(max(0, self.origin_times.get(target_origin, 0) + delay - loop.time()))
            self.origin_times[target_origin] = loop.time()

    async def _execute(self, run_id, profile_id, task_id, config):
        page, context, guard = None, None, None
        results = []
        owned = profile_id not in self.browsers.contexts and profile_id not in self.browsers.launches
        status, error = 'Completed', None
        try:
            async with asyncio.timeout(self.state.settings.task_timeout_seconds):
                context = await self.browsers.ensure_open(profile_id, for_automation=True)
                self._update(run_id, status='Running', started_at=now())
                self._refresh_task(task_id)
                async def route_guard(route):
                    try:
                        allowed = origin(route.request.url) in self.state.settings.allowed_origins
                    except ValueError:
                        allowed = False
                    await route.continue_() if allowed else await route.abort('blockedbyclient')
                guard = route_guard
                await context.route('**/*', guard)
                page = await context.new_page()
                denied = asyncio.Event()
                def response_seen(response):
                    if response.status in (401, 403, 429) and response.request.resource_type == 'document':
                        denied.set()
                page.on('response', response_seen)
                page.on('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
                for index, action in enumerate(config.actions):
                    target = action.url if action.type == 'open_url' else page.url
                    target_origin = origin(target) if target.startswith(('http://', 'https://')) else origin(config.actions[0].url)
                    if target_origin not in self.state.settings.allowed_origins:
                        raise ValueError('Target origin is no longer approved.')
                    await self._rate_limit(target_origin)
                    if denied.is_set():
                        raise ValueError('Website denied or rate-limited access.')
                    result = {'step': index + 1, 'action': action.type}
                    if action.type == 'open_url':
                        if page.is_closed():
                            page = await context.new_page()
                        response = await page.goto(action.url, wait_until='domcontentloaded', timeout=action.timeout_ms)
                        if response and response.status in (401, 403, 429):
                            raise ValueError('Website denied or rate-limited access.')
                        result['http_status'] = response.status if response else None
                    elif action.type == 'wait_load':
                        await page.wait_for_load_state('load', timeout=action.timeout_ms)
                    elif action.type == 'read_title':
                        result['title'] = await page.title()
                    elif action.type == 'screenshot':
                        filename = f'run_{run_id}_step_{index + 1}.png'
                        await page.screenshot(path=str(self.config.screenshots_dir / filename), full_page=True, timeout=action.timeout_ms)
                        result['screenshot'] = f'/api/screenshots/{filename}'
                    elif action.type == 'click':
                        await page.locator(action.selector).click(timeout=action.timeout_ms)
                    elif action.type == 'fill':
                        await page.locator(action.selector).fill(action.value, timeout=action.timeout_ms)
                    elif action.type == 'read_text':
                        result['text'] = (await page.locator(action.selector).inner_text(timeout=action.timeout_ms))[:20000]
                    elif action.type == 'wait_selector':
                        await page.locator(action.selector).wait_for(state='visible', timeout=action.timeout_ms)
                    elif action.type == 'scroll':
                        await page.mouse.wheel(0, action.pixels)
                    elif action.type == 'close_tab':
                        await page.close()
                    if denied.is_set():
                        raise ValueError('Website denied or rate-limited access.')
                    results.append(result)
                    self._update(run_id, progress=round((index + 1) / len(config.actions) * 100), result=json.dumps(results))
        except asyncio.CancelledError:
            status, error = 'Cancelled', 'Run cancelled by user or application shutdown.'
        except TimeoutError:
            status, error = 'Failed', 'Task exceeded its wall timeout (including queue wait).'
        except Exception as exc:
            status, error = 'Failed', f'Step {len(results) + 1} failed ({type(exc).__name__}). Check selector, permission, proxy and page availability.'
        finally:
            if page and not page.is_closed():
                try:
                    await asyncio.wait_for(page.close(), 5)
                except Exception:
                    pass
            if context and guard:
                try:
                    await asyncio.wait_for(context.unroute('**/*', guard), 5)
                except Exception:
                    pass
            if config.close_browser or (owned and status != 'Completed'):
                try:
                    await self.browsers.stop(profile_id)
                except ValueError:
                    error = (error or '') + ' Browser close timed out; profile lock retained.'
                    if status == 'Completed':
                        status = 'Failed'
            self._update(run_id, status=status, error_message=error, finished_at=now(), result=json.dumps(results))
            self.jobs.pop(run_id, None)
            self.profile_jobs.pop(profile_id, None)
            self._refresh_task(task_id)
            self.state.log(f'Task #{task_id}, run #{run_id}: {status}.', profile_id, 'error' if status == 'Failed' else 'info')

    async def stop_task(self, task_id):
        with self.db.session() as session:
            ids = list(session.scalars(select(TaskRun.id).where(TaskRun.task_id == task_id, TaskRun.status.in_(ACTIVE))))
        await self._cancel(ids)
        return len(ids)

    async def stop_profile(self, profile_id):
        run_id = self.profile_jobs.get(profile_id)
        if run_id:
            await self._cancel([run_id])

    async def _cancel(self, ids):
        jobs = [self.jobs[i] for i in ids if i in self.jobs]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        # A task cancelled before its first event-loop turn never enters its finally block.
        task_ids = set()
        with self.db.session.begin() as session:
            for run_id in ids:
                row = session.get(TaskRun, run_id)
                if row and row.status in ACTIVE:
                    row.status, row.finished_at = 'Cancelled', now()
                    row.error_message = 'Run cancelled before execution.'
                    task_ids.add(row.task_id)
                    self.profile_jobs.pop(row.profile_id, None)
                self.jobs.pop(run_id, None)
        for task_id in task_ids:
            self._refresh_task(task_id)
        self.state.emit()

    async def shutdown(self):
        await self._cancel(list(self.jobs))
