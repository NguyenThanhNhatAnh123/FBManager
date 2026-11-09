"""Failure-isolated periodic work and bounded, secret-free readiness metadata."""
import asyncio
import inspect
import time


class TickHealth:
    def __init__(self, *, clock=None, stale_after=10):
        self.clock = clock or time.monotonic
        self.stale_after = stale_after
        self.services = {}

    def expect(self, name):
        self.services.setdefault(name, {'last_attempt': None, 'last_success': None,
                                        'error': None, 'failures': 0})

    def attempted(self, name):
        self.expect(name)
        self.services[name]['last_attempt'] = self.clock()

    def finished(self, name, *, failed=False):
        row = self.services[name]
        row['error'] = 'scheduler_tick_failed' if failed else None
        if failed:
            row['failures'] += 1
        else:
            row['last_success'] = self.clock()

    def call(self, name, callback):
        self.attempted(name)
        try:
            callback()
        except Exception:
            self.finished(name, failed=True)
            return False
        self.finished(name)
        return True

    def snapshot(self):
        now = self.clock()
        values = {}
        for name, row in self.services.items():
            age = None if row['last_success'] is None else max(0, now - row['last_success'])
            values[name] = {'healthy': row['error'] is None and age is not None and age <= self.stale_after,
                            'last_success_seconds_ago': None if age is None else round(age, 3),
                            'error': row['error'], 'failures': row['failures']}
        return {'ready': bool(values) and all(row['healthy'] for row in values.values()),
                'services': values}


class PeriodicSupervisor:
    """Each callback gets one attempt per cycle; failed mutations are never replayed here.

    Callbacks are existing ticks, which own their idempotency/recovery policy.
    Exceptions cannot starve later subsystems. Cancellation always propagates.
    """
    def __init__(self, steps, *, interval=2, stale_after=10, clock=None):
        self.steps = tuple(steps)
        names = [name for name, _ in self.steps]
        if not names or len(set(names)) != len(names) or interval <= 0 or stale_after <= interval:
            raise ValueError('Invalid periodic supervisor configuration')
        self.interval = interval
        self.health = TickHealth(clock=clock, stale_after=stale_after)
        for name in names:
            self.health.expect(name)
        self.task = None
        self.stopping = False

    async def cycle(self):
        for name, callback in self.steps:
            self.health.attempted(name)
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self.health.finished(name, failed=True)
            else:
                self.health.finished(name)

    async def run(self):
        if self.task is not None and not self.task.done():
            raise RuntimeError('Periodic supervisor is already running')
        self.task = asyncio.current_task()
        self.stopping = False
        try:
            while True:
                await self.cycle()
                await asyncio.sleep(self.interval)
        finally:
            self.stopping = True

    def snapshot(self):
        running = self.task is not None and not self.task.done() and not self.stopping
        result = self.health.snapshot()
        return {**result, 'running': running, 'ready': running and result['ready']}


async def shutdown_services(services):
    """Attempt every owned cleanup, report fixed codes, then propagate cancellation."""
    failures = {}
    cancelled = None
    for name, callback in services:
        try:
            value = callback()
            if inspect.isawaitable(value):
                await value
        except asyncio.CancelledError as error:
            cancelled = error
        except Exception:
            failures[name] = 'shutdown_failed'
    if cancelled is not None:
        raise cancelled
    return failures
