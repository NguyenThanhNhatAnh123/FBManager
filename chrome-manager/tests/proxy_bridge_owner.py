"""Read-only production ownership probe used by the real Chromium smoke test."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from app.services.proxy_bridge import owns_proxy_connection
print('allowed' if owns_proxy_connection(int(sys.argv[2]), int(sys.argv[3]), sys.argv[1]) else 'denied')
