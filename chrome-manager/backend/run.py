import asyncio
import os
import sys
import logging
from pathlib import Path
import webbrowser
from app.core.startup_diagnostics import StartupASGI,StartupDiagnostics,StartupObserver


def main():
    diagnostics=StartupDiagnostics(Path(__file__).resolve().parents[1])
    if sys.version_info < (3, 12):
        diagnostics.failure('runtime',reason='python_version_unsupported')
        print('Python 3.12 or later is required.');return 3
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    stage='configuration'
    try:
        from app.core.config import Config
        from app.core.startup import inspect_existing_instance
        import uvicorn
        config=Config()
        diagnostics.status('checking',port=config.port)
        dashboard_path=os.getenv('CM_OPEN_DASHBOARD_PATH','/')
        if dashboard_path not in ('/','/pages/'):dashboard_path='/'
        dashboard=f'http://127.0.0.1:{config.port}'+dashboard_path
        open_browser=os.getenv('CM_OPEN_DASHBOARD','false').lower()=='true'
        stage='preflight'
        existing=inspect_existing_instance(config)
        if existing['state']=='running':
            diagnostics.status('running',reason='existing_instance',pid=existing.get('pid'))
            print(f'Via is already running. Dashboard: {dashboard}')
            if open_browser:
                try:webbrowser.open(dashboard)
                except Exception:diagnostics.status('running',reason='browser_open_failed',pid=existing.get('pid'))
            return 0
        if existing['state']!='stopped':
            diagnostics.failure(stage,reason=existing['reason'])
            print(f"Via could not start ({existing['reason']}). See logs/startup-error.json.");return 3
        stage='application_import'
        from app.main import app
        application=StartupASGI(app,diagnostics)
        configuration=uvicorn.Config(application,host='127.0.0.1',port=config.port,loop='asyncio',workers=1,
                                     access_log=False,timeout_graceful_shutdown=30)
        observer=StartupObserver(diagnostics,(lambda:webbrowser.open(dashboard)) if open_browser else None)
        logger=logging.getLogger('uvicorn.error');logger.addHandler(observer)
        diagnostics.status('starting')
        stage='server_startup'
        try:uvicorn.Server(configuration).run()
        finally:logger.removeHandler(observer)
        diagnostics.status('stopped',reason='normal_shutdown');return 0
    except KeyboardInterrupt:
        diagnostics.status('stopped',reason='interrupted');return 0
    except SystemExit as error:
        code=error.code if type(error.code) is int else 3
        if code and not diagnostics.failed:diagnostics.failure(stage,exit_code=code)
        return code
    except Exception as error:
        diagnostics.failure(stage,error)
        print(f'Startup failed ({type(error).__name__}). See logs/startup-error.json.');return 3


if __name__ == '__main__':raise SystemExit(main())
