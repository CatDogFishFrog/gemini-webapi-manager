"""Макет сторінок входу Google для тестів консольного логіну.

Два «хости» на loopback: ``127.0.0.1`` — акаунти (accounts.google.com), ``localhost`` — застосунок
(gemini.google.com; Chromium приймає Secure-cookie лише для localhost). Повторює лише *структуру* реальних сторінок: id/name полів, data-challengetype,
шляхи /signin/challenge/..., щоб класифікатор бачив те саме, що й на справжньому Google.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from gemini_vault.login import LoginTarget

EMAIL = "tester@example.com"
PASSWORD = "pw-ok-123"
TOTP = "123456"
SMS = "654321"

_STYLE = "<style>body{font:16px sans-serif;padding:30px}input,button{display:block;margin:8px 0;padding:8px}</style>"

_JS = """
<script>
async function post(url, data){ const r = await fetch(url,{method:'POST',body:JSON.stringify(data)}); return r.json(); }
function showErr(t){ const e=document.getElementById('err'); e.textContent=t; e.style.display='block'; }
async function submitField(api, fieldId){
  const res = await post(api,{value:[...document.querySelectorAll('[id="'+fieldId+'"]')].find(e=>e.type!=='hidden').value});
  if(res.error) showErr(res.error); else location.href = res.next;
}
</script>
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><title>{title}</title>{_STYLE}</head><body>"
            f"<h1>{title}</h1>{body}"
            f"<div id='err' jsname='B34EJ' role='alert' style='display:none;color:#b00'></div>{_JS}</body></html>")


class Scenario:
    def __init__(self, second: str | None = "totp", dp_delay: float = 1.5, password: str = PASSWORD):
        self.second = second  # None | totp | selection | device | rejected | unknown | captcha
        self.dp_delay = dp_delay
        self.password = password
        self.log: list[str] = []
        self.dp_started: float | None = None
        self.password_attempts = 0


class _Handler(BaseHTTPRequestHandler):
    scenario: Scenario
    app_origin: str
    acc_origin: str

    def log_message(self, *args):  # тиша
        pass

    def _send(self, body: str, status=200, ctype="text/html", headers=None):
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, data):
        self._send(json.dumps(data), ctype="application/json")

    def do_GET(self):
        s = self.scenario
        path = urlparse(self.path).path
        s.log.append(f"GET {path}")
        if path in ("/", "/signin"):
            # як у справжнього Google: перед видимим полем лежить приховане з тим самим id
            return self._send(page("Sign in", "<input type='hidden' id='identifierId' value=''>"
                                   "<input id='identifierId' name='identifier' type='email' aria-label='Email or phone'>"
                                   "<button id='identifierNext' onclick=\"submitField('/api/identifier','identifierId')\">Next</button>"
                                   "<a href='/help'>Help</a>"))
        if path == "/signin/challenge/pwd":
            # заголовок лежить ще й в aria-live-контейнері: це не повідомлення про помилку
            return self._send(page("Welcome", "<div aria-live='assertive'>Welcome</div><input name='Passwd' type='password' aria-label='Enter your password'>"
                                   "<button id='passwordNext' onclick=\"submitField('/api/password','pw')\">Next</button>"
                                   "<script>document.querySelector('[name=Passwd]').id='pw'</script>"))
        if path == "/signin/challenge/selection":
            return self._send(page("Verify it's you", "<ul>"
                "<li><div role='link' tabindex=0 data-challengetype='6' onclick=\"location.href='/signin/challenge/totp'\">Get a verification code from the Google Authenticator app</div></li>"
                "<li><div role='link' tabindex=0 data-challengetype='9' onclick=\"location.href='/signin/challenge/ipp'\">Get a text message with a verification code to ••••••12</div></li>"
                "<li><div role='link' tabindex=0 data-challengetype='39' onclick=\"location.href='/signin/challenge/dp'\">Tap Yes on your phone or tablet</div></li></ul>"))
        if path == "/signin/challenge/totp":
            return self._send(page("2-Step Verification", "<p>Enter the 6-digit code from your authenticator app.</p>"
                "<input id='totpPin' name='totpPin' type='tel' aria-label='Enter code'>"
                "<button id='totpNext' onclick=\"submitField('/api/totp','totpPin')\">Next</button>"
                "<button id='tryAnother'>Try another way</button>"
                "<script>document.getElementById('tryAnother').onclick=()=>location.href='/signin/challenge/selection'</script>"))
        if path == "/signin/challenge/ipp":
            return self._send(page("2-Step Verification", "<p>Google sent a text message to your phone.</p>"
                "<input id='idvPin' name='Pin' type='tel' autocomplete='one-time-code' aria-label='Enter the code'>"
                "<button id='idvNext' onclick=\"submitField('/api/sms','idvPin')\">Next</button>"))
        if path == "/signin/challenge/dp":
            if s.dp_started is None:
                s.dp_started = time.monotonic()
            return self._send(page("Check your phone", "<p>Google sent a notification to your Pixel. Tap Yes on the notification to verify it's you.</p><p style='font-size:40px'>42</p>"
                "<button id='tryAnother'>Try another way</button>"
                "<script>setInterval(async()=>{const r=await fetch('/api/dp');const j=await r.json();if(j.done)location.href='/signin/challenge/finish'},500)</script>"))
        if path == "/signin/challenge/finish":
            return self._send(page("Stay signed in", "<p>Do you want to skip this next time?</p>"
                "<button onclick=\"location.href='/done'\">Not now</button><button onclick=\"location.href='/done'\">Continue</button>"))
        if path == "/signin/rejected":
            return self._send(page("Couldn't sign you in", "<p>This browser or app may not be secure. Try using a different browser.</p>"))
        if path == "/weird":
            return self._send(page("Something new", "<p>Google invented a screen with only decorative text but nothing to press at all today.</p>"))
        if path == "/done":  # як у Google: з accounts-хоста редірект на хост застосунку, який ставить cookie
            self.send_response(302)
            self.send_header("Location", f"{self.app_origin}/setcookies")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if path == "/setcookies":
            cookies = {"__Secure-1PSID": "psid-value", "__Secure-1PSIDTS": "psidts-value", "SID": "sid-value"}
            self.send_response(302)
            for k, v in cookies.items():
                self.send_header("Set-Cookie", f"{k}={v}; Path=/; Secure; HttpOnly")
            self.send_header("Location", f"{self.app_origin}/app")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if path == "/app":
            return self._send("<html><head><title>Gemini</title></head><body><h1>Gemini</h1>"
                              f"<script>window.WIZ_global_data={{oPEP7c:'{EMAIL}'}}</script></body></html>")
        if path == "/api/dp":
            done = s.dp_started is not None and time.monotonic() - s.dp_started >= s.dp_delay
            return self._json({"done": done})
        self._send("not found", 404)

    def do_POST(self):
        s = self.scenario
        path = urlparse(self.path).path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        value = body.get("value", "")
        s.log.append(f"POST {path}")
        if path == "/api/identifier":
            if value.lower() != EMAIL:
                return self._json({"error": "Couldn't find your Google Account"})
            return self._json({"next": "/signin/challenge/pwd"})
        if path == "/api/password":
            s.password_attempts += 1
            if value != s.password:
                return self._json({"error": "Wrong password. Try again or click Forgot password to reset it."})
            nxt = {"totp": "/signin/challenge/totp", "selection": "/signin/challenge/selection",
                   "device": "/signin/challenge/dp", "rejected": "/signin/rejected",
                   "unknown": "/weird", None: "/done"}[s.second]
            return self._json({"next": nxt})
        if path == "/api/totp":
            return self._json({"next": "/done"} if value == TOTP else {"error": "Wrong code. Try again."})
        if path == "/api/sms":
            return self._json({"next": "/done"} if value == SMS else {"error": "Wrong code. Try again."})
        self._send("not found", 404)


class MockGoogle:
    """Запускає сервер на вільному порті; ``target`` — LoginTarget для login-коду."""

    def __init__(self, scenario: Scenario | None = None):
        self.scenario = scenario or Scenario()
        handler = type("H", (_Handler,), {"scenario": self.scenario})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        handler.app_origin = f"http://localhost:{self.port}"
        handler.acc_origin = f"http://127.0.0.1:{self.port}"
        self.target = LoginTarget(
            sign_in_url=f"{handler.acc_origin}/signin",
            app_url=f"{handler.app_origin}/app",
            app_host="localhost",
            cookie_domain="localhost",
        )
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
