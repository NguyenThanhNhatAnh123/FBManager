"""Offline scheduler failure/recovery and cleanup ownership contracts."""
import asyncio
import sqlite3

import pytest

from app.core.scheduler_health import PeriodicSupervisor, TickHealth, shutdown_services


def test_failed_subsystem_does_not_starve_later_ticks_and_recovers_without_secret_output():
    async def exercise():
        seen=[];recover=[False];clock=[0.0]
        def database_tick():
            seen.append('database')
            if not recover[0]:raise sqlite3.OperationalError('private-fixture-error')
        async def independent_tick():seen.append('independent')
        supervisor=PeriodicSupervisor([('db',database_tick),('other',independent_tick)],clock=lambda:clock[0])
        await supervisor.cycle()
        assert seen==['database','independent']
        first=supervisor.health.snapshot()
        assert not first['ready'] and first['services']['other']['healthy']
        assert first['services']['db']['error']=='scheduler_tick_failed'
        assert 'private-fixture-error' not in repr(first)
        recover[0]=True;clock[0]=2;await supervisor.cycle()
        assert supervisor.health.snapshot()['ready']
        assert supervisor.health.snapshot()['services']['db']['failures']==1
        clock[0]=13
        assert not supervisor.health.snapshot()['ready']
    asyncio.run(exercise())


def test_real_loop_reports_liveness_and_preserves_cancellation():
    async def exercise():
        ready=asyncio.Event();second=asyncio.Event();count=[0]
        async def tick():
            count[0]+=1
            if count[0]==1:ready.set()
            if count[0]==2:second.set()
        supervisor=PeriodicSupervisor([('work',tick)],interval=.001)
        assert not supervisor.snapshot()['ready']
        task=asyncio.create_task(supervisor.run())
        await asyncio.wait_for(ready.wait(),1)
        await asyncio.wait_for(second.wait(),1)
        assert supervisor.snapshot()['running'] and supervisor.snapshot()['ready']
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert not supervisor.snapshot()['running'] and not supervisor.snapshot()['ready']
    asyncio.run(exercise())


def test_tick_cancellation_is_never_converted_into_a_transient_failure():
    async def exercise():
        seen=[]
        async def cancelled():raise asyncio.CancelledError()
        supervisor=PeriodicSupervisor([('cancelled',cancelled),('later',lambda:seen.append(True))])
        with pytest.raises(asyncio.CancelledError):await supervisor.cycle()
        assert not seen and supervisor.health.snapshot()['services']['cancelled']['failures']==0
        health=TickHealth()
        with pytest.raises(asyncio.CancelledError):health.call('cancelled',lambda:(_ for _ in ()).throw(asyncio.CancelledError()))
    asyncio.run(exercise())


@pytest.mark.parametrize('cancel',[False,True])
def test_shutdown_attempts_remaining_owned_cleanups_after_failure_or_cancellation(cancel):
    async def exercise():
        seen=[]
        async def broken():
            seen.append('first')
            if cancel:raise asyncio.CancelledError()
            raise RuntimeError('private-fixture-error')
        async def worker():seen.append('worker')
        def lock_release():seen.append('lock')
        cleanup=shutdown_services([('first',broken),('worker',worker),('lock',lock_release)])
        if cancel:
            with pytest.raises(asyncio.CancelledError):await cleanup
        else:
            assert await cleanup=={'first':'shutdown_failed'}
        assert seen==['first','worker','lock']
    asyncio.run(exercise())
