"""
Vercel Serverless Function Entry Point for SONORA Django Web App.
Ensures the project root is in sys.path and exports the WSGI application callable.
"""

import os
import sys
import shutil
from pathlib import Path

# Inject repository root into sys.path so 'myproject' and 'home' resolve cleanly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

# Ensure /tmp/db.sqlite3 is initialized if running on Vercel
if os.getenv("VERCEL"):
    import tempfile
    tmp_root = Path("/tmp") if os.path.exists("/tmp") else Path(tempfile.gettempdir())
    tmp_db = tmp_root / "db.sqlite3"
    orig_db = ROOT_DIR / "db.sqlite3"
    if orig_db.exists() and not tmp_db.exists():
        try:
            shutil.copy2(orig_db, tmp_db)
        except Exception as e:
            print(f"Error copying db.sqlite3 to /tmp: {e}", file=sys.stderr)

try:
    from django.core.wsgi import get_wsgi_application
    _django_app = get_wsgi_application()
except Exception as exc:
    import traceback
    tb = traceback.format_exc()
    print(f"Error initializing Django WSGI application on Vercel: {tb}", file=sys.stderr)
    def _django_app(environ, start_response):
        start_response('500 Internal Server Error', [('Content-Type', 'text/html; charset=utf-8')])
        html = f"""<!DOCTYPE html>
<html>
<head><title>SONORA - Serverless Initialization Error</title></head>
<body style="font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:40px;line-height:1.5;">
<h2>SONORA Serverless Initialization Error</h2>
<pre style="background:#1e293b;padding:16px;border-radius:8px;overflow:auto;color:#ef4444;">{tb}</pre>
</body></html>"""
        return [html.encode("utf-8")]

def app(environ, start_response):
    try:
        return _django_app(environ, start_response)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print(f"Unhandled request error on Vercel: {tb}", file=sys.stderr)
        start_response('500 Internal Server Error', [('Content-Type', 'text/html; charset=utf-8')])
        html = f"""<!DOCTYPE html>
<html>
<head><title>SONORA - Request Error</title></head>
<body style="font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:40px;line-height:1.5;">
<h2>SONORA Serverless Request Error</h2>
<pre style="background:#1e293b;padding:16px;border-radius:8px;overflow:auto;color:#ef4444;">{tb}</pre>
</body></html>"""
        return [html.encode("utf-8")]

handler = app
