# Tunnel watchdog — restarts cloudflared on death, re-points the Razorpay webhook via API, self-tests.
# Loop interval: 60s. Log: data/tunnel-watchdog.log. Kill switch: create data/watchdog.stop
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BASE = pathlib.Path(__file__).resolve().parents[1]
LOG = BASE / 'data' / 'tunnel-watchdog.log'
STOP = BASE / 'data' / 'watchdog.stop'
TUNNEL_LOG = BASE / 'data' / 'tunnel.log'

def log(msg):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {msg}\n')

def tunnel_url():
    try:
        t = TUNNEL_LOG.read_text(encoding='utf-8', errors='replace')
        m = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', t)
        return m[-1] if m else None
    except OSError:
        return None

def health_ok(url):
    try:
        import urllib.request
        with urllib.request.urlopen(f'{url}/health', timeout=20) as r:
            return r.status == 200
    except OSError:
        return False

# The event set the consumers act on (halt feed + payment-link verify path).
# Razorpay's dashboard has a known desync mode where per-event selection
# silently drops to zero (WHAT_BROKE 2b) while the config API still answers —
# so every re-point re-asserts the full set, and a lost webhook is rebuilt
# complete (ensure_webhook).
WEBHOOK_EVENTS = [
    'payment_link.paid', 'payment_link.partially_paid',
    'payment_link.expired', 'payment_link.cancelled',
    'subscription.activated', 'subscription.authenticated',
    'subscription.charged', 'subscription.pending',
    'subscription.halted', 'subscription.cancelled',
    'subscription.completed', 'subscription.paused',
    'subscription.resumed', 'subscription.updated',
]

def update_webhook(url, auth):
    import httpx
    # Razorpay's update API takes the event set as a name->true map, not a
    # list (a list is rejected with 'Invalid event name/names: 1, 2, 3…').
    events = {e: True for e in WEBHOOK_EVENTS}
    webhook_id = os.environ.get('VAAPSI_WEBHOOK_ID', 'TV7th2OVQXy2WF')
    payload = {'url': url, 'events': events}
    r = httpx.put(f'https://api.razorpay.com/v1/webhooks/{webhook_id}',
                  auth=auth, json=payload, timeout=25)
    return r.status_code == 200, r.text[:120]


def ensure_webhook(url, auth):
    """Create the webhook if it doesn't exist (idempotent bootstrapping).

    Called by the watchdog at startup: if the registration was lost (a
    dashboard deletion, an account reset, a fresh test account), the
    watchdog rebuilds it with the full event set instead of PUTting into
    a 404 forever. If it exists, the create call is skipped — update_webhook
    keeps the URL and events fresh every tunnel rotation.
    """
    import httpx
    webhook_id = os.environ.get('VAAPSI_WEBHOOK_ID', 'TV7th2OVQXy2WF')
    probe = httpx.get(f'https://api.razorpay.com/v1/webhooks/{webhook_id}',
                      auth=auth, timeout=25)
    if probe.status_code == 200:
        return True, 'exists'
    events = {e: True for e in WEBHOOK_EVENTS}
    r = httpx.post('https://api.razorpay.com/v1/webhooks',
                   auth=auth, json={'url': f'{url}/webhooks/razorpay',
                                    'events': events, 'active': True}, timeout=25)
    return r.status_code in (200, 201), r.text[:120]

def db_path():
    from app.settings import get_settings
    return get_settings().data_dir / 'vaapsi.sqlite3'

def signed_selftest(url, secret):
    import hashlib
    import hmac

    import httpx
    body = json.dumps({'event': 'subscription.halted', 'created_at': int(time.time()),
                       'payload': {'subscription': {'entity': {'id': 'sub_SELFTEST_WATCHDOG', 'status': 'halted'}}}}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        r = httpx.post(f'{url}/webhooks/razorpay', content=body,
                       headers={'Content-Type': 'application/json', 'X-Razorpay-Signature': sig}, timeout=25)
        return r.status_code == 200
    except OSError:
        return False

def selftest_landed():
    from app.settings import get_settings
    db = sqlite3.connect(get_settings().data_dir / 'vaapsi.sqlite3')
    return db.execute("SELECT count() FROM webhook_events WHERE payload_json LIKE '%sub_SELFTEST_WATCHDOG%'").fetchone()[0]

def server_ok():
    try:
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8) as r:
            return r.status == 200
    except OSError:
        return False

def main():
    log('watchdog started')
    # Boot-time webhook bootstrap: if the registration was lost entirely,
    # rebuild it (URL + full event set + active) instead of PUTting into a
    # 404 forever. See ensure_webhook / WHAT_BROKE 2b.
    try:
        from app.settings import get_settings
        s = get_settings()
        auth = (s.razorpay_key_id, s.razorpay_key_secret)
        okboot, how = ensure_webhook('http://127.0.0.1:8000', auth)
        log(f'webhook ensure: {"OK (" + how + ")" if okboot else "FAILED"}')
    except Exception as exc:  # noqa: BLE001 — boot must never crash the loop
        log(f'webhook ensure error (suppressed, will retry next poll): {exc}')
    while not STOP.exists():
        try:
            if not server_ok():
                log('server down — relaunching uvicorn')
                server_stdout = open(BASE / 'data' / 'server.log', 'a')  # noqa: SIM115 — held for child lifetime
                subprocess.Popen([str(BASE / '.venv' / 'Scripts' / 'python.exe'), '-m', 'uvicorn', 'app.main:app',
                                  '--host', '127.0.0.1', '--port', '8000'],
                                 cwd=str(BASE), stdout=server_stdout, stderr=subprocess.STDOUT)
                time.sleep(6)
            url = tunnel_url()
            ok = bool(url) and health_ok(url)
            if not ok:
                log(f'tunnel down (url={url}) — relaunching')
                subprocess.run(['taskkill', '/F', '/IM', 'cloudflared.exe'], capture_output=True, check=False)
                with open(TUNNEL_LOG, 'w', encoding='utf-8') as f:
                    f.write('')
                # Keep the handle open for the life of the child process (deliberate — SIM115 suppressed)
                tunnel_stdout = open(TUNNEL_LOG, 'w')  # noqa: SIM115
                subprocess.Popen([str(BASE / 'tools' / 'cloudflared.exe'), 'tunnel', '--url', 'http://127.0.0.1:8000'],
                                 stdout=tunnel_stdout, stderr=subprocess.STDOUT)
                time.sleep(12)
                url = tunnel_url()
                if url:
                    log(f'new tunnel: {url}')
                    from app.settings import get_settings
                    s = get_settings()
                    auth = (s.razorpay_key_id, s.razorpay_key_secret)
                    # Re-assert URL *and* the full event set — the dashboard
                    # has dropped per-event selections before (WHAT_BROKE 2b).
                    okput, detail = update_webhook(f'{url}/webhooks/razorpay', auth)
                    log(f'webhook re-point+events: {"OK" if okput else "FAILED " + detail[:60]}')
                    time.sleep(2)
                    st = signed_selftest(url, s.razorpay_webhook_secret)
                    log(f'self-test: {"OK" if st else "FAIL"}')
        # A transient DNS failure inside any probe must never kill the loop —
        # the watchdog that dies IS the outage (2026-08-30: getaddrinfo crash
        # left an orphan tunnel and a dead webhook URL for hours).
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log(f'poll error (suppressed, retrying): {exc}')
        time.sleep(60)

if __name__ == '__main__':
    main()
