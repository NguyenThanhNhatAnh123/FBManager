"""Enable Facebook-only proxy routing without editing profiles or proxy assignments."""
import json
import os
from pathlib import Path
import sys
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.routing_setup import enable_facebook_only

if __name__ == '__main__':
    try:
        load_dotenv(ROOT / '.env')
        print(json.dumps(enable_facebook_only(ROOT, ROOT / os.getenv('CM_DATA_DIR', 'data')), indent=2))
        print('PASS: Facebook/Messenger use the saved profile proxy. Outlook and other domains use the direct connection.')
        print('Profiles, proxy credentials and login data were retained. Future starts can use start.bat.')
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
