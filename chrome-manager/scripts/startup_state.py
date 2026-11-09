"""Safe JSON-only startup preflight; never launch or stop an application."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--record',action='store_true',help='Record safe launcher metadata')
    options=parser.parse_args(argv)
    config=None
    try:
        from app.core.config import Config
        from app.core.startup import inspect_existing_instance
        config=Config()
        result=inspect_existing_instance(config)
    except Exception:
        result={'state':'unavailable','port':None,'reason':'instance_probe_unavailable'}
    if options.record:
        from app.core.startup_diagnostics import StartupDiagnostics
        diagnostics=StartupDiagnostics(config.root if config is not None else Path(__file__).resolve().parents[1])
        if result['state'] in ('busy','unavailable'):
            diagnostics.failure('preflight',reason=result['reason'],port=result['port'])
        else:
            diagnostics.status('running' if result['state']=='running' else 'checking',
                               reason=result['reason'],port=result['port'],pid=result.get('pid'))
    print(json.dumps(result,separators=(',',':')))
    return 0


if __name__=='__main__':raise SystemExit(main())
