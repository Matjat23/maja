"""Talk to Maja from your phone: a small web server (FastAPI) on your home Wi-Fi.

When phone access is on, Maja serves a chat page at http://<this computer>:8765. The
phone opens it once through a link (or QR code) that contains a secret key; the page
remembers the key and sends it with every request, so nobody else on the network can
read or change your notes. Phone access is off until Matty turns it on with the phone button.

The connection is plain HTTP, so only use it on a network you trust (like home Wi-Fi).
"""

import secrets
import socket
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


def local_ip():
    """This computer's address on the local network (no data is actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.168.1.1", 1))
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def create_app(maja, lock, key, on_message=None):
    """The web app. `lock` keeps the phone and the desktop window from changing Maja at
    the same time; `on_message(message, reply)` lets the window show phone chats."""
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

    return app


class PhoneServer:
    def __init__(self, maja, lock, key, on_message=None):
        self.key = key
        self._app = create_app(maja, lock, key, on_message)
        self._server = None

    @property
    def running(self):
        return self._server is not None

    def link(self):
        return f"http://{local_ip()}:{PORT}/#key={self.key}"

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
