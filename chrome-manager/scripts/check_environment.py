"""Read-only dependency diagnostics; imports each required package to detect blocked DLLs."""
import importlib
import importlib.util
import json
import pathlib
import shutil
import sys

modules = ['fastapi','uvicorn','sqlalchemy','playwright.async_api','psutil','httpx','filelock','python_socks','dotenv','socksio']
missing = []
errors = {}
for name in modules:
    try:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
        else:
            importlib.import_module(name)
    except Exception as error:
        errors[name] = type(error).__name__ + ': ' + str(error)
report = {
    'python': sys.version.split()[0], 'python_supported': sys.version_info >= (3,12),
    'missing_python_modules': missing, 'module_import_errors': errors,
    'node': shutil.which('node'), 'npm': shutil.which('npm.cmd') or shutil.which('npm'),
    'frontend_built': (pathlib.Path(__file__).resolve().parents[1]/'frontend/dist/index.html').exists(),
}
print(json.dumps(report, indent=2))
raise SystemExit(1 if missing or errors or not report['python_supported'] or not report['node'] else 0)
