"""Built-in HTML page served on the bare base domain (``https://<domain>/``).

Without a ``token`` query parameter the page is a short landing page that
shows how to open a tunnel. With ``?token=<token>`` (the WebLogs link printed
in the SSH banner) it becomes a live WebLogs viewer that connects to the
``/channel/weblogs`` WebSocket on the same origin. This replaces the external
frontend, so a self-hosted instance needs nothing but this API.

Security: request data shown in the viewer is attacker-controlled (anyone can
send requests to a tunnel), so the script renders it exclusively through
``textContent``. The page is served with a strict, hash-based CSP, no referrer
(the token lives in the URL) and ``no-store`` caching.
"""

from __future__ import annotations

import base64
import hashlib
import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..config import settings

router = APIRouter()

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f8; --surface: #ffffff; --text: #1b1b1f; --muted: #6b6b76;
  --border: #e3e3e8; --accent: #3b5bdb; --ok: #2b8a3e; --warn: #c27c0e;
  --err: #c92a2a; --code-bg: #f0f0f3;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111114; --surface: #1a1a1f; --text: #ececf1; --muted: #9a9aa6;
    --border: #2c2c33; --accent: #8ca2ff; --ok: #69db7c; --warn: #ffc078;
    --err: #ff8787; --code-bg: #24242b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 1100px; margin: 0 auto; padding: 32px 16px; }
h1 { font-size: 22px; margin: 0 0 4px; }
p { margin: 8px 0; color: var(--muted); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cmd {
  display: block; padding: 12px 14px; margin: 16px 0; overflow-x: auto;
  background: var(--code-bg); border: 1px solid var(--border);
  border-radius: 8px; white-space: pre;
}
header { display: flex; flex-wrap: wrap; align-items: baseline;
  justify-content: space-between; gap: 8px; margin-bottom: 16px; }
#status { font-size: 14px; color: var(--muted); }
#status[data-kind="ok"] { color: var(--ok); }
#status[data-kind="end"] { color: var(--err); }
.table-wrap { overflow-x: auto; background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px; }
table { width: 100%; min-width: 640px; border-collapse: collapse; font-size: 14px; }
tr.entry td:not(.path) { white-space: nowrap; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
th { color: var(--muted); font-weight: 500; white-space: nowrap; }
td.path { word-break: break-all; font-family: ui-monospace, Menlo, monospace; }
tr.entry { cursor: pointer; }
tr.entry:hover, tr.entry.open { background: var(--code-bg); }
tr.detail td { background: var(--code-bg); }
tr.detail pre { margin: 0; white-space: pre-wrap; word-break: break-all;
  font-size: 13px; max-height: 420px; overflow: auto; }
.s2 { color: var(--ok); } .s3 { color: var(--accent); }
.s4 { color: var(--warn); } .s5 { color: var(--err); }
#empty { padding: 24px 12px; color: var(--muted); text-align: center; }
"""

_SCRIPT = """
(function () {
  "use strict";
  var token = new URLSearchParams(location.search).get("token");
  var byId = function (id) { return document.getElementById(id); };
  if (!token) { byId("landing").hidden = false; return; }
  byId("logs").hidden = false;

  var statusEl = byId("status"), rows = byId("rows"), empty = byId("empty");
  var MAX_ENTRIES = 1000;
  var REASONS = {
    TUNNEL_NOT_FOUND: "Tunnel not found. Check the link from your SSH session.",
    TUNNEL_CLOSED: "Tunnel closed.",
    TUNNEL_EXPIRED: "Tunnel expired."
  };
  var proto = location.protocol === "https:" ? "wss:" : "ws:";
  var url = proto + "//" + location.host + "/channel/weblogs?token=" +
    encodeURIComponent(token);
  var finished = false, attempt = 0;

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.dataset.kind = kind;
  }
  function blank(v) { return v === null || v === undefined || v === ""; }
  function fmtTime(ts) {
    var d = new Date(ts);
    return isNaN(d.getTime()) ? String(ts || "") : d.toLocaleTimeString();
  }
  function headersText(h) {
    if (typeof h === "string") {
      try { h = JSON.parse(h); } catch (e) { return h; }
    }
    if (!h || typeof h !== "object") { return ""; }
    return Object.keys(h).map(function (k) { return k + ": " + h[k]; }).join("\\n");
  }
  function statusClass(code) {
    if (!code) { return ""; }
    return code >= 500 ? "s5" : code >= 400 ? "s4" : code >= 300 ? "s3" : "s2";
  }
  function addEntry(e) {
    empty.hidden = true;
    var tr = document.createElement("tr");
    tr.className = "entry";
    var cells = [
      fmtTime(e.timestamp),
      e.method || "",
      e.path || "",
      blank(e.status_code) ? "-" : e.status_code,
      blank(e.latency_ms) ? "" : Math.round(Number(e.latency_ms)) + " ms"
    ];
    cells.forEach(function (value, i) {
      var td = document.createElement("td");
      td.textContent = String(value);
      if (i === 2) { td.className = "path"; }
      if (i === 3) { td.className = statusClass(Number(e.status_code)); }
      tr.appendChild(td);
    });
    var detail = document.createElement("tr");
    detail.className = "detail";
    detail.hidden = true;
    var td = document.createElement("td");
    td.colSpan = 5;
    var pre = document.createElement("pre");
    var text = headersText(e.headers);
    if (e.body) { text += "\\n\\n" + e.body; }
    pre.textContent = text;
    td.appendChild(pre);
    detail.appendChild(td);
    tr.addEventListener("click", function () {
      detail.hidden = !detail.hidden;
      tr.classList.toggle("open");
    });
    rows.prepend(detail);
    rows.prepend(tr);
    while (rows.children.length > MAX_ENTRIES * 2) { rows.lastElementChild.remove(); }
  }
  function finish(reason) {
    finished = true;
    setStatus(REASONS[reason] || "Tunnel is no longer available.", "end");
  }
  function connect() {
    setStatus("Connecting...", "wait");
    var ws = new WebSocket(url);
    ws.onopen = function () {
      attempt = 0;
      // The server replays stored entries on every connect.
      rows.textContent = "";
      empty.hidden = false;
      setStatus("Live", "ok");
    };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "log" && msg.data) { addEntry(msg.data); }
      else if (msg.type === "status") { setStatus(String(msg.data), "wait"); }
      else if (msg.type === "error") { finish(String(msg.data)); }
    };
    ws.onclose = function (ev) {
      if (finished) { return; }
      if (ev.code === 4000 || ev.code === 4004) { finish(ev.reason); return; }
      attempt = Math.min(attempt + 1, 6);
      setStatus("Disconnected. Reconnecting...", "wait");
      setTimeout(connect, 1000 * Math.pow(2, attempt - 1));
    };
  }
  connect();
})();
"""

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>PublicHost</title>
<link rel="icon" href="data:,">
<style>__STYLE__</style>
</head>
<body>
<main>
  <section id="landing" hidden>
    <h1>PublicHost</h1>
    <p>Expose a local web server to the internet over SSH. No signup, no client
    to install: run this and keep the session open.</p>
    <code class="cmd">__SSH_COMMAND__</code>
    <p>Replace <code>3000</code> with your local port. The SSH session prints
    your public HTTPS address and a WebLogs link to inspect incoming
    requests.</p>
  </section>
  <section id="logs" hidden>
    <header>
      <h1>WebLogs</h1>
      <span id="status" data-kind="wait">Connecting...</span>
    </header>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th>
        <th>Latency</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
      <div id="empty">Waiting for requests to your tunnel...</div>
    </div>
  </section>
</main>
<script>__SCRIPT__</script>
</body>
</html>
"""


def _csp_hash(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def ssh_command() -> str:
    """Return the client command shown on the landing page."""
    port = settings.external_ssh_port
    port_flag = "" if port == 22 else f"-p {port} "
    return f"ssh {port_flag}-R 0:localhost:3000 {settings.ssh_domain}"


def render_page() -> str:
    """Render the page; only the SSH command is interpolated (HTML-escaped)."""
    return (
        _TEMPLATE.replace("__STYLE__", _STYLE)
        .replace("__SCRIPT__", _SCRIPT)
        .replace("__SSH_COMMAND__", html.escape(ssh_command()))
    )


_CSP = "; ".join(
    [
        "default-src 'none'",
        f"script-src {_csp_hash(_SCRIPT)}",
        f"style-src {_csp_hash(_STYLE)}",
        # 'self' covers same-host wss:// in CSP3 browsers.
        "connect-src 'self'",
        "img-src data:",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ]
)

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    # The WebLogs token is part of the URL: never leak it via Referer.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    """Landing page, or the WebLogs viewer when opened with ``?token=``."""
    return HTMLResponse(render_page(), headers=_SECURITY_HEADERS)
