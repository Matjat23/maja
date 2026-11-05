"""Talk to Maja from your phone: a small web server (FastAPI) on your home Wi-Fi.

When phone access is on, Maja serves a chat page at http://<this computer>:8765. The
phone opens it once through a link (or QR code) that contains a secret key; the page
remembers the key and sends it with every request, so nobody else on the network can
read or change your notes. Phone access is off until Matty turns it on with the phone button.

The connection is plain HTTP, so only use it on a network you trust (like home Wi-Fi).
"""

import ipaddress
import secrets
import socket
import subprocess
import threading

from markdown import plain_text

try:
    import uvicorn
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel
    SERVER_AVAILABLE = True
except ImportError:
    SERVER_AVAILABLE = False

PORT = 8765


TAILSCALE_COMMANDS = ["tailscale", r"C:\Program Files\Tailscale\tailscale.exe"]


def is_tailscale_address(address):
    """Tailscale gives each device an address in 100.64.0.0/10."""
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def tailscale_ip():
    """This computer's address in Matty's private Tailscale network, or None.

    With Tailscale on the computer and the phone (same account), the phone can reach Maja
    from anywhere, encrypted, without opening anything to the internet.
    """
    for command in TAILSCALE_COMMANDS:
        try:
            result = subprocess.run([command, "ip", "-4"], capture_output=True, text=True, timeout=5,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            continue
        for line in result.stdout.split():
            if is_tailscale_address(line.strip()):
                return line.strip()
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        return None
    return next((address for address in sorted(addresses) if is_tailscale_address(address)), None)


def local_ip():
    """This computer's address on the local network (no data is actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.168.1.1", 1))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


STATES = ("idle", "listening", "thinking", "speaking")


def create_app(maja, lock, key, on_message=None, state=lambda: "idle"):
    """The web app. `lock` keeps the phone and the desktop window from changing Maja at
    the same time; `on_message(message, reply)` lets the window show phone chats;
    `state()` says what Maja is doing, for her face and, later, a robot body."""
    app = FastAPI(title="Maja", docs_url=None, redoc_url=None, openapi_url=None)

    def check_key(x_maja_key: str = Header(default="")):
        if not secrets.compare_digest(x_maja_key.encode(), key.encode()):
            raise HTTPException(status_code=401, detail="Wrong or missing key")

    class Message(BaseModel):
        message: str

    @app.get("/", response_class=HTMLResponse)
    def page():
        return PHONE_PAGE  # The page itself contains no personal data.

    @app.post("/api/chat", dependencies=[Depends(check_key)])
    def chat(body: Message):
        message = body.message.strip()[:4000]
        with lock:
            reply = maja.reply(message)
        if on_message and message:
            on_message(message, reply)
        return {"reply": plain_text(reply)}  # The phone page shows plain text.

    @app.get("/api/history", dependencies=[Depends(check_key)])
    def history():
        with lock:
            return {"messages": [{**m, "content": plain_text(m["content"])} for m in maja.history]}

    @app.get("/face", response_class=HTMLResponse)
    def face():
        return FACE_PAGE  # Also contains no personal data.

    @app.get("/api/state", dependencies=[Depends(check_key)])
    def current_state():
        # The hardware interface: the face page and a future robot body ask this a few times
        # a second and show it (eyes, mouth, a nod, an LED). It never blocks on the lock.
        value = state()
        return {"state": value if value in STATES else "idle"}

    return app


class PhoneServer:
    def __init__(self, maja, lock, key, on_message=None, state=lambda: "idle"):
        self.key = key
        self._app = create_app(maja, lock, key, on_message, state)
        self._server = None

    def face_link(self, address=None):
        return f"http://{address or tailscale_ip() or local_ip()}:{PORT}/face#key={self.key}"

    @property
    def running(self):
        return self._server is not None

    def link(self, address=None):
        return f"http://{address or local_ip()}:{PORT}/#key={self.key}"

    def links(self):
        """{'home': link on this Wi-Fi, 'anywhere': link through Tailscale or None}."""
        anywhere = tailscale_ip()
        return {"home": self.link(), "anywhere": self.link(anywhere) if anywhere else None}

    def start(self):
        config = uvicorn.Config(self._app, host="0.0.0.0", port=PORT, log_level="warning")
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, daemon=True).start()

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
            self._server = None


def new_key():
    return secrets.token_urlsafe(18)


FACE_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1b1525"><title>Maja's face</title>
<style>
  html, body { margin: 0; height: 100%; background: #1b1525; overflow: hidden; }
  canvas { display: block; width: 100vw; height: 100vh; }
  #note { position: fixed; bottom: 12px; width: 100%; text-align: center; color: #6f6480;
          font: 14px system-ui, sans-serif; }
</style></head>
<body>
<canvas id="face"></canvas><div id="note">Tap for full screen</div>
<script>
  // Maja's face for an old phone or tablet. It asks Maja what she's doing a few times a
  // second (idle, listening, thinking, speaking) and draws eyes and a mouth to match.
  const fromLink = new URLSearchParams(location.hash.slice(1)).get("key");
  if (fromLink) { try { localStorage.setItem("majaKey", fromLink); } catch (e) {} history.replaceState(null, "", "/face"); }
  let key = fromLink;
  try { key = key || localStorage.getItem("majaKey"); } catch (e) {}
  const canvas = document.getElementById("face"), note = document.getElementById("note");
  const ctx = canvas.getContext("2d");
  let state = "idle", connected = false, blinkUntil = 0, nextBlink = performance.now() + 3000;

  async function poll() {
    try {
      const response = await fetch("/api/state", { headers: { "X-Maja-Key": key || "" } });
      if (response.status === 401) { note.textContent = "Open this page from Maja's phone link."; connected = false; }
      else { state = (await response.json()).state; connected = true; }
    } catch (e) { connected = false; }
    setTimeout(poll, 350);
  }

  function roundedRect(x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); ctx.fill();
  }

  function draw(now) {
    const w = canvas.width = innerWidth * devicePixelRatio, h = canvas.height = innerHeight * devicePixelRatio;
    const unit = Math.min(w, h), t = now / 1000;
    ctx.fillStyle = "#1b1525"; ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = connected ? "#f4a4d4" : "#6f6480";
    if (now > nextBlink) { blinkUntil = now + 130; nextBlink = now + 2500 + Math.random() * 4000; }

    // Eyes: taller when listening, narrower and moving while thinking, closed while blinking.
    let eyeH = unit * 0.26, eyeW = unit * 0.15, lookX = 0, lookY = 0;
    if (state === "listening") { eyeH *= 1.12; eyeW *= 1.08; }
    if (state === "thinking") { eyeH *= 0.62; lookX = Math.sin(t * 1.6) * unit * 0.05; lookY = -unit * 0.03; }
    if (now < blinkUntil) eyeH = unit * 0.025;
    for (const side of [-1, 1]) {
      const cx = w / 2 + side * unit * 0.2 + lookX, cy = h * 0.42 + lookY;
      roundedRect(cx - eyeW / 2, cy - eyeH / 2, eyeW, eyeH, eyeW * 0.45);
    }

    // Mouth: a small smile, an "o" when listening, moving while speaking, dots while thinking.
    const my = h * 0.42 + unit * 0.26;
    ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = unit * 0.025; ctx.lineCap = "round";
    if (state === "speaking") {
      const open = unit * (0.03 + 0.05 * Math.abs(Math.sin(t * 9) * Math.sin(t * 3.7)));
      ctx.beginPath(); ctx.ellipse(w / 2, my, unit * 0.07, open, 0, 0, Math.PI * 2); ctx.fill();
    } else if (state === "listening") {
      ctx.beginPath(); ctx.ellipse(w / 2, my, unit * 0.035, unit * 0.035, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 0.25 + 0.2 * Math.sin(t * 4);
      ctx.beginPath(); ctx.arc(w / 2, h * 0.45, unit * 0.42, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    } else if (state === "thinking") {
      for (let i = 0; i < 3; i++) {
        ctx.globalAlpha = 0.3 + 0.7 * Math.max(0, Math.sin(t * 4 - i * 0.8));
        ctx.beginPath(); ctx.arc(w / 2 + (i - 1) * unit * 0.06, my, unit * 0.017, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    } else {
      ctx.beginPath(); ctx.arc(w / 2, my - unit * 0.06, unit * 0.08, Math.PI * 0.2, Math.PI * 0.8); ctx.stroke();
    }
    requestAnimationFrame(draw);
  }

  document.body.addEventListener("click", () => {
    note.textContent = "";
    if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
  });
  poll();
  requestAnimationFrame(draw);
</script>
</body></html>
"""


PHONE_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1b1525"><title>Maja 💜</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #1b1525; color: #f8f1fa; font: 16px system-ui, sans-serif;
         display: flex; flex-direction: column; height: 100dvh; }
  header { padding: 14px 16px 6px; font-size: 24px; font-weight: 700; color: #f4a4d4; }
  #status { padding: 0 16px 8px; font-size: 13px; opacity: .8; }
  #chat { flex: 1; overflow-y: auto; padding: 8px 16px; }
  .msg { max-width: 85%; margin: 6px 0; padding: 10px 12px; border-radius: 14px;
         white-space: pre-wrap; line-height: 1.35; overflow-wrap: anywhere; }
  .you { margin-left: auto; background: #3a2d4f; }
  .maja { background: #2a2038; color: #f4a4d4; }
  form { display: flex; gap: 8px; padding: 10px 16px calc(10px + env(safe-area-inset-bottom)); }
  input { flex: 1; min-width: 0; padding: 12px; border: 0; border-radius: 10px;
          background: #2a2038; color: #f8f1fa; font-size: 16px; }
  button { padding: 0 18px; border: 0; border-radius: 10px; background: #f4a4d4;
           color: #1b1525; font-weight: 700; font-size: 16px; }
  button:disabled { opacity: .5; }
</style></head>
<body>
<header>Maja ✨</header>
<div id="status">Connecting…</div>
<div id="chat"></div>
<form id="form"><input id="text" autocomplete="off" placeholder="Message Maja">
<button id="send">Send</button></form>
<script>
  // The key arrives once in the link (#key=...), is remembered on this phone,
  // and is removed from the address bar.
  const fromLink = new URLSearchParams(location.hash.slice(1)).get("key");
  if (fromLink) { try { localStorage.setItem("majaKey", fromLink); } catch (e) {} history.replaceState(null, "", "/"); }
  let key = fromLink;
  try { key = key || localStorage.getItem("majaKey"); } catch (e) {}
  const chat = document.getElementById("chat"), status = document.getElementById("status");
  const text = document.getElementById("text"), send = document.getElementById("send");

  function add(who, message) {
    const div = document.createElement("div");
    div.className = "msg " + who;
    div.textContent = message;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
  }
  async function call(path, options = {}) {
    const response = await fetch(path, { ...options,
      headers: { "Content-Type": "application/json", "X-Maja-Key": key || "" } });
    if (response.status === 401) throw new Error("This phone isn't connected. Scan the QR code in Maja (📱) again.");
    if (!response.ok) throw new Error("Maja had a problem (" + response.status + ").");
    return response.json();
  }
  async function load() {
    try {
      const data = await call("/api/history");
      data.messages.forEach(m => add(m.role === "user" ? "you" : "maja", m.content));
      status.textContent = "Connected to Maja on your computer";
    } catch (error) { status.textContent = error.message; }
  }
  document.getElementById("form").addEventListener("submit", async event => {
    event.preventDefault();
    const message = text.value.trim();
    if (!message) return;
    text.value = ""; add("you", message);
    send.disabled = true; status.textContent = "Maja is thinking…";
    try {
      const data = await call("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
      add("maja", data.reply);
      status.textContent = "Connected to Maja on your computer";
    } catch (error) { status.textContent = error.message; }
    send.disabled = false; text.focus();
  });
  load();
</script>
</body></html>
"""
