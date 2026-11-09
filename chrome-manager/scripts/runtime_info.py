"""Quote-free CLI probes shared by Windows PowerShell startup."""
import argparse
import importlib.util
import sys
from pathlib import Path

MODULES = ['fastapi','uvicorn','sqlalchemy','playwright','psutil','httpx','filelock','python_socks','dotenv','socksio','PIL','tzdata']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['python', 'dependencies', 'pip', 'channel', 'port'])
    mode = parser.parse_args().mode
    if mode == 'python':
        if sys.version_info < (3, 12):
            return 1
        print(sys.executable)
        return 0
    if mode == 'pip':
        return int(importlib.util.find_spec('pip') is None)
    if mode == 'dependencies':
        missing = [name for name in MODULES if importlib.util.find_spec(name) is None]
        if missing:
            print('Missing Python modules: ' + ', '.join(missing))
        return int(bool(missing))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
    from app.core.config import Config
    config = Config()
    print((config.channel or 'chromium') if mode == 'channel' else config.port)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
