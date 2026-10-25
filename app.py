import requests
import hashlib
import os
import random
import sqlite3
import string
import time
import shutil
from datetime import datetime, timedelta
from functools import wraps
from datetime import timezone
from html import escape
from authlib.integrations.flask_client import OAuth
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    send_file,
)
import secrets
from werkzeug.middleware.proxy_fix import ProxyFix

# ==========================================
# APP INITIALIZATION & SECURITY CONFIG
# ==========================================

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY", "hsl_corp_ultra_secure_key_2026_x89_production_ready"
)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=4),
)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

PAID_USERS = ["js7876839939@gmail.com"]

oauth = OAuth(app)
google = oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    client_kwargs={"scope": "openid email profile"},
)

# ==========================================
# RATE LIMITING & SECURITY MIDDLEWARE
# ==========================================

REQUEST_HISTORY = {}


def rate_limit(max_requests=10, window_seconds=60):
  def decorator(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
      ip = request.remote_addr
      now = time.time()

      if ip not in REQUEST_HISTORY:
        REQUEST_HISTORY[ip] = []

      REQUEST_HISTORY[ip] = [
          t for t in REQUEST_HISTORY[ip] if now - t < window_seconds
      ]

      if len(REQUEST_HISTORY[ip]) >= max_requests:
        return (
            jsonify({
                "status": "rate_limited",
                "message": (
                    "Too many attempts. Request blocked due to security"
                    " policies."
                ),
            }),
            429,
        )

      REQUEST_HISTORY[ip].append(now)
      return f(*args, **kwargs)

    return wrapped

  return decorator


@app.after_request
def apply_security_headers(response):
  response.headers["X-Frame-Options"] = "DENY"
  response.headers["X-Content-Type-Options"] = "nosniff"
  response.headers["X-XSS-Protection"] = "1; mode=block"
  response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
  return response


# ==========================================
# DATABASE ENGINE & SCHEMAS
# ==========================================


# --- PERSISTENT RAILWAY DATABASE ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DB_PATH = os.path.join(APP_DIR, "hsl.db")

if os.path.isdir("/data"):
    DATABASE_PATH = "/data/hsl.db"
else:
    DATABASE_PATH = LEGACY_DB_PATH

if (
    DATABASE_PATH != LEGACY_DB_PATH
    and not os.path.exists(DATABASE_PATH)
    and os.path.isfile(LEGACY_DB_PATH)
):
    shutil.copy2(LEGACY_DB_PATH, DATABASE_PATH)
    print("Database migration: existing hsl.db copied to /data/hsl.db")

print(f"Database path selected: {DATABASE_PATH}")

def init_db():
  con = sqlite3.connect(DATABASE_PATH, timeout=30)
  con.execute("PRAGMA busy_timeout = 30000")
  con.execute("PRAGMA journal_mode = WAL")
  cur = con.cursor()

  cur.execute("""
        CREATE TABLE IF NOT EXISTS apps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            name TEXT, 
            token TEXT UNIQUE, 
            owner_email TEXT, 
            created_at TEXT
        )
    """)

  cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            key_text TEXT UNIQUE, 
            app_token TEXT, 
            status TEXT, 
            hwid TEXT, 
            used_by TEXT, 
            created_at TEXT
        )
    """)

  cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            email TEXT, 
            hwid TEXT, 
            app_token TEXT, 
            key_text TEXT, 
            first_seen TEXT
        )
    """)

  cur.execute("""
        CREATE TABLE IF NOT EXISTS tool_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            username TEXT, 
            password TEXT, 
            app_token TEXT, 
            status TEXT, 
            hwid TEXT, 
            created_at TEXT,
            expiry_type TEXT NOT NULL DEFAULT 'lifetime',
            expires_at TEXT,
            UNIQUE(username, app_token)
        )
    """)

  try:
      cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_users'")
      table_sql = cur.fetchone()
      if table_sql and "UNIQUE" in table_sql[0] and "app_token" not in table_sql[0]:
          cur.execute("ALTER TABLE tool_users RENAME TO tool_users_old")
          cur.execute("""
                CREATE TABLE tool_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    username TEXT, 
                    password TEXT, 
                    app_token TEXT, 
                    status TEXT, 
                    hwid TEXT, 
                    created_at TEXT,
                    expiry_type TEXT NOT NULL DEFAULT 'lifetime',
                    expires_at TEXT,
                    UNIQUE(username, app_token)
                )
            """)
          cur.execute("""
                INSERT INTO tool_users (id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at)
                SELECT id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at FROM tool_users_old
            """)
          cur.execute("DROP TABLE tool_users_old")
          con.commit()
  except Exception as e:
      print(f"Migration note for tool_users: {e}")

  cur.execute("PRAGMA table_info(tool_users)")
  existing_columns = {row[1] for row in cur.fetchall()}
  if "created_at" not in existing_columns:
    cur.execute("ALTER TABLE tool_users ADD COLUMN created_at TEXT")
  if "expiry_type" not in existing_columns:
    cur.execute("ALTER TABLE tool_users ADD COLUMN expiry_type TEXT NOT NULL DEFAULT 'lifetime'")
  if "expires_at" not in existing_columns:
    cur.execute("ALTER TABLE tool_users ADD COLUMN expires_at TEXT")

  cur.execute("""
CREATE TABLE IF NOT EXISTS login_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    app_token TEXT,
    login_time TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT,
    ip_address TEXT,
    hwid TEXT
)
""")

  cur.execute("""
CREATE INDEX IF NOT EXISTS idx_login_history_app_token
ON login_history(app_token)
""")
  con.commit()
  con.close()


init_db()


def db(query, params=(), fetch=False):
    con = sqlite3.connect(DATABASE_PATH, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        cur = con.cursor()
        cur.execute(query, params)
        data = cur.fetchall() if fetch else None
        con.commit()
        return data
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ==========================================
# CSS STYLES & ANIMATED CROSSHAIR CURSOR
# ==========================================

COMMON_HEAD = """
<script src="https://cdn.tailwindcss.com"></script>
<style>
  :root {
    --cyan: #00f6ff;
    --blue: #3b82f6;
    --violet: #7c3aed;
    --danger: #ff1744;
    --bg: #03050b;
    --panel: rgba(7, 10, 19, .78);
    --line: rgba(0, 246, 255, .16);
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    background:
      radial-gradient(circle at 50% -10%, rgba(0,246,255,.12), transparent 35%),
      radial-gradient(circle at 100% 100%, rgba(124,58,237,.10), transparent 30%),
      #03050b;
    color: #f8fafc;
    cursor: none;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  ::selection { background: rgba(0,246,255,.28); color: white; }
  ::-webkit-scrollbar { width: 7px; height: 7px; }
  ::-webkit-scrollbar-track { background: #02040a; }
  ::-webkit-scrollbar-thumb {
    background: linear-gradient(var(--cyan), var(--violet));
    border-radius: 999px;
  }
  #c { position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: .9; }
  .glass {
    position: relative;
    overflow: hidden;
    backdrop-filter: blur(24px) saturate(140%);
    -webkit-backdrop-filter: blur(24px) saturate(140%);
    background: linear-gradient(145deg, rgba(15,20,35,.88), rgba(3,7,15,.74));
    border: 1px solid rgba(0,246,255,.18);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.055), 0 25px 80px rgba(0,0,0,.45);
  }
  .glass-card {
    position: relative;
    overflow: hidden;
    background: linear-gradient(145deg, rgba(12,17,30,.86), rgba(4,7,14,.72));
    border: 1px solid rgba(0,246,255,.13);
    backdrop-filter: blur(18px) saturate(130%);
    -webkit-backdrop-filter: blur(18px) saturate(130%);
    transition: transform .28s ease, border-color .28s ease, box-shadow .28s ease;
  }
  .glass-card:hover {
    border-color: rgba(0,246,255,.38);
    transform: translateY(-5px);
    box-shadow: 0 18px 55px rgba(0,0,0,.48), 0 0 35px rgba(0,246,255,.13);
  }
  input, select { transition: border-color .2s ease, box-shadow .2s ease; }
  input:focus, select:focus {
    border-color: rgba(0,246,255,.65) !important;
    box-shadow: 0 0 0 3px rgba(0,246,255,.07), 0 0 22px rgba(0,246,255,.10);
  }
  #cursor-dot {
    position: fixed; width: 7px; height: 7px; background: var(--cyan);
    border-radius: 50%; pointer-events: none; z-index: 9999;
    transform: translate(-50%, -50%); box-shadow: 0 0 12px var(--cyan);
  }
  #cursor-crosshair {
    position: fixed; width: 34px; height: 34px; border: 1px solid rgba(0,246,255,.7);
    border-radius: 50%; pointer-events: none; z-index: 9998;
    transform: translate(-50%, -50%);
  }
  .side-active {
    background: linear-gradient(90deg, rgba(0,246,255,.16), rgba(124,58,237,.08)) !important;
    border: 1px solid rgba(0,246,255,.42) !important;
    color: #67f7ff !important;
    font-weight: 800;
  }
  @media (max-width: 768px) { body { cursor: auto; } #cursor-dot, #cursor-crosshair { display: none; } }
</style>
"""

CURSOR_SCRIPT = """
<div id="cursor-dot"></div>
<div id="cursor-crosshair"></div>
<script>
  const dot = document.getElementById('cursor-dot');
  const cross = document.getElementById('cursor-crosshair');
  let mx = 0, my = 0, cx = 0, cy = 0;
  window.addEventListener('mousemove', (e) => {
    mx = e.clientX; my = e.clientY;
    dot.style.left = mx + 'px'; dot.style.top = my + 'px';
  });
  function renderCursor() {
    cx += (mx - cx) * 0.18; cy += (my - cy) * 0.18;
    cross.style.left = cx + 'px'; cross.style.top = cy + 'px';
    requestAnimationFrame(renderCursor);
  }
  renderCursor();

  const canvasElement = document.getElementById('c');
  const ctx = canvasElement.getContext('2d');
  function resizeCanvas() { canvasElement.width = window.innerWidth; canvasElement.height = window.innerHeight; }
  resizeCanvas(); window.onresize = resizeCanvas;
  let particlesArray = [];
  for(let i = 0; i < 70; i++) {
    particlesArray.push({ x: Math.random() * canvasElement.width, y: Math.random() * canvasElement.height, r: Math.random() * 1.5 + 0.5, vy: Math.random() * 0.5 + 0.2, opacity: Math.random() * 0.6 + 0.2 });
  }
  function animateParticles() {
    ctx.clearRect(0, 0, canvasElement.width, canvasElement.height);
    particlesArray.forEach(p => {
      p.y -= p.vy;
      if(p.y < 0) { p.y = canvasElement.height; p.x = Math.random() * canvasElement.width; }
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(34, 211, 238, ${p.opacity})`;
      ctx.fill();
    });
    requestAnimationFrame(animateParticles);
  }
  animateParticles();
</script>
"""

# ==========================================
# UI TEMPLATES
# ==========================================

LANDING = (
    """<!DOCTYPE html>
<html>
<head>"""
    + COMMON_HEAD
    + """</head>
<body class="text-white overflow-x-hidden relative min-h-screen flex flex-col justify-between">
<canvas id="c"></canvas>
<nav class="relative z-10 flex justify-between items-center px-10 py-5 bg-black/55 backdrop-blur-xl border-b border-cyan-400/15">
  <div class="flex items-center gap-3">
    <div class="w-10 h-10 bg-gradient-to-r from-cyan-400 to-indigo-600 rounded-xl flex items-center justify-center shadow-[0_0_18px_#22d3ee]">👾</div>
    <div>
      <p class="font-black text-sm tracking-wider">HSL CORP</p>
      <p class="text-[9px] text-cyan-400 font-bold tracking-widest">KEYAUTH STYLE AUTHENTICATION SYSTEM</p>
    </div>
  </div>
  <div class="flex gap-4">
    <a href="/login" class="bg-zinc-900/80 hover:bg-zinc-800 border border-zinc-700 px-6 py-2.5 rounded-full text-xs font-semibold transition">Sign In</a>
    <a href="/dashboard" class="bg-gradient-to-r from-cyan-400 to-indigo-600 hover:opacity-90 px-6 py-2.5 rounded-full text-xs font-bold shadow-[0_0_20px_rgba(34,211,238,0.5)] transition">Dashboard</a>
  </div>
</nav>
<div class="relative z-10 flex flex-col items-center text-center pt-24 px-4">
  <h1 class="text-6xl md:text-7xl font-black bg-gradient-to-r from-cyan-300 via-cyan-400 to-indigo-500 bg-clip-text text-transparent max-w-5xl leading-tight">HSL CORP AUTH</h1>
  <p class="text-zinc-400 mt-6 font-medium text-lg max-w-2xl">Ultimate Hardware-Locked Licensing & Application Protection Infrastructure.</p>
  <a href="/login" class="bg-gradient-to-r from-cyan-400 to-indigo-600 hover:scale-105 px-9 py-4 rounded-2xl text-sm font-bold shadow-[0_0_30px_rgba(34,211,238,0.6)] mt-9 transition flex items-center gap-2">🚀 Get Started</a>
</div>
<footer class="relative z-10 text-center py-6 border-t border-white/5 text-xs text-zinc-600">&copy; 2026 HSL CORP. All rights reserved.</footer>
"""
    + CURSOR_SCRIPT
    + """
</body>
</html>"""
)

LOGIN = (
    """<!DOCTYPE html>
<html>
<head>"""
    + COMMON_HEAD
    + """</head>
<body class="flex items-center justify-center h-screen overflow-hidden relative">
<canvas id="c"></canvas>
<div class="relative z-10 w-[420px] glass rounded-[28px] p-9 text-center shadow-[0_0_60px_rgba(34,211,238,0.2)]">
  <div class="w-16 h-16 bg-gradient-to-r from-cyan-400 to-indigo-600 rounded-2xl mx-auto flex items-center justify-center shadow-[0_0_25px_#22d3ee]">👾</div>
  <h1 class="font-black text-2xl mt-5 text-white">HSL CORP</h1>
  <p class="text-xs text-zinc-400 mt-1">Sign in using verified OAuth endpoints</p>
  <a href="/auth/google" class="mt-8 w-full bg-white hover:bg-zinc-100 text-black rounded-xl py-3.5 flex justify-center items-center gap-3 font-bold text-sm shadow-lg transition">
    <img src="https://www.svgrepo.com/show/475656/google-color.svg" width=20> Continue with Google
  </a>
</div>
"""
    + CURSOR_SCRIPT
    + """
</body>
</html>"""
)

DASHBOARD_HTML = (
    """<!DOCTYPE html>
<html>
<head>"""
    + COMMON_HEAD
    + """
<style>.side-active { background: rgba(34,211,238,0.15); border: 1px solid rgba(34,211,238,0.4); color: #22d3ee !important; font-weight: bold; }</style>
</head>
<body class="flex h-screen text-white overflow-hidden relative">
<canvas id="c"></canvas>
<div class="w-[260px] bg-[#02040a]/90 backdrop-blur-2xl border-r border-cyan-400/10 flex flex-col relative z-10">
  <div class="p-5 flex items-center gap-3 border-b border-white/10">
    <div class="w-9 h-9 bg-gradient-to-r from-cyan-400 to-indigo-600 rounded-xl flex items-center justify-center shadow-[0_0_12px_#22d3ee]">👾</div>
    <div>
      <p class="font-black text-sm">HSL CORP</p>
      <p class="text-[9px] text-cyan-400 font-bold">Developer Console</p>
    </div>
  </div>
  <div class="p-3 space-y-1 text-xs" id="sidebar">
    <button onclick="showTab('overview')" id="btn-overview" class="side-active w-full text-left rounded-xl px-4 py-2.5 transition">🏠 Overview</button>
    <button onclick="showTab('applications')" id="btn-applications" class="w-full text-left text-zinc-400 hover:text-white px-4 py-2.5 transition">📦 Applications</button>
    <button onclick="showTab('tool_users')" id="btn-tool_users" class="w-full text-left text-zinc-400 hover:text-white px-4 py-2.5 transition">👤 Users ({{tool_user_count}}/{{limit_text}})</button>
    <button onclick="showTab('keys')" id="btn-keys" class="w-full text-left text-zinc-400 hover:text-white px-4 py-2.5 transition">🔑 License Keys</button>
    <button onclick="showTab('integrate')" id="btn-integrate" class="w-full text-left text-zinc-400 hover:text-white px-4 py-2.5 transition">🔌 Anti-Crack Integration</button>
    <button onclick="showTab('billing')" id="btn-billing" class="w-full text-left text-zinc-400 hover:text-white px-4 py-2.5 transition">💎 Billing / Upgrade</button>
  </div>
  <div class="mt-auto p-4 border-t border-white/10 flex items-center gap-3 bg-black/40">
    <img src="https://ui-avatars.com/api/?name={{name}}&background=22d3ee&color=fff" class="w-8 h-8 rounded-full border border-cyan-400/40">
    <div>
      <p class="text-[11px] font-bold truncate w-[110px]">{{email}}</p>
      <p class="text-[9px] {{plan_color}} font-bold">{{plan_text}}</p>
    </div>
    <a href="/logout" class="ml-auto text-[11px] text-red-400 hover:text-red-300 font-semibold">Logout</a>
  </div>
</div>
<div class="flex-1 overflow-y-auto relative z-10">
  <div class="h-14 bg-black/55 backdrop-blur-xl border-b border-cyan-400/10 flex items-center justify-between px-8">
    <p class="text-xs font-semibold tracking-wider text-cyan-300">HSL CONSOLE - {{plan_text}} PLAN</p>
  </div>
  <div class="p-8">
    <div id="tab-overview">
      <h1 class="text-2xl font-black">Dashboard Overview</h1>
      <div class="mt-6 grid grid-cols-[1.3fr_1fr_0.7fr] gap-4">
        <div class="glass-card rounded-2xl p-5">
          <p class="text-[10px] font-bold text-cyan-400">ACTIVE APPLICATION</p>
          <select id="appSelect" onchange="selectApp(this.value)" class="bg-black/80 border border-white/20 rounded-xl px-3 py-2.5 text-xs mt-3 w-full font-semibold focus:outline-none focus:border-cyan-400">{{app_options}}</select>
        </div>
        <div class="glass-card rounded-2xl p-5">
          <p class="text-[10px] font-bold text-zinc-400">MASTER APP TOKEN</p>
          <div class="mt-3 flex justify-between items-center bg-black/80 rounded-xl px-3 py-2 border border-white/10">
            <p id="tokenDisplay" class="text-xs font-mono text-zinc-300 truncate">{{active_token}}</p>
            <button onclick="copyToken()" class="text-[10px] bg-gradient-to-r from-cyan-400 to-indigo-600 px-3 py-1.5 rounded-lg font-bold">Copy</button>
          </div>
        </div>
        <div class="glass-card rounded-2xl p-5">
          <p class="text-[10px] font-bold text-zinc-400">PLAN LIMIT</p>
          <p class="text-xs font-bold mt-3">{{tool_user_count}} / {{limit_text}} Used</p>
        </div>
      </div>
      <div class="mt-6 glass-card rounded-2xl p-7">
        <p class="text-base font-bold text-white">+ Create New Secure User</p>
        <div class="flex gap-3 mt-4">
          <input id="newUsername" placeholder="Username" class="flex-1 bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
          <input id="newPassword" placeholder="Password" class="flex-1 bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
        </div>
        <button onclick="createUser()" class="mt-4 w-full bg-gradient-to-r from-cyan-400 to-indigo-600 py-3 rounded-xl text-sm font-bold shadow-[0_0_20px_rgba(34,211,238,0.4)]">Create User</button>
      </div>
    </div>
    <div id="tab-applications" class="hidden">
      <h1 class="text-2xl font-black">Applications</h1>
      <div class="glass-card mt-6 rounded-2xl p-7">
        <div class="space-y-3 mb-6">{{app_list_html}}</div>
        <div class="border-t border-white/10 pt-5">
          <p class="text-base font-bold">+ Create New App</p>
          <input id="newAppName" placeholder="App Name" class="mt-3 w-full bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
          <button onclick="createApp()" class="mt-4 w-full bg-gradient-to-r from-cyan-400 to-indigo-600 py-3 rounded-xl text-sm font-bold">Create Application</button>
        </div>
      </div>
    </div>
    <div id="tab-tool_users" class="hidden">
      <h1 class="text-2xl font-black">Users</h1>
      <div class="glass-card mt-6 rounded-2xl p-6">
        <div class="space-y-3 text-xs font-mono">{{tool_users_list_html}}</div>
      </div>
    </div>
    <div id="tab-keys" class="hidden"><h1 class="text-2xl font-black">Keys</h1></div>
    <div id="tab-integrate" class="hidden"><h1 class="text-2xl font-black">Integration</h1></div>
    <div id="tab-billing" class="hidden"><h1 class="text-2xl font-black">Billing</h1></div>
  </div>
</div>
"""
    + CURSOR_SCRIPT
    + """
<script>
function showTab(name) {
    document.querySelectorAll('[id^="tab-"]').forEach(d => d.classList.add('hidden'));
    document.getElementById('tab-' + name).classList.remove('hidden');
    document.querySelectorAll('#sidebar button').forEach(b => { b.classList.remove('side-active'); b.classList.add('text-zinc-400'); });
    let btn = document.getElementById('btn-' + name);
    if(btn) { btn.classList.add('side-active'); btn.classList.remove('text-zinc-400'); }
}
async function createApp() {
    let name = document.getElementById('newAppName').value.trim();
    if(!name) { alert('Enter Name!'); return; }
    let res = await fetch('/api/create_app', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: name}) });
    let data = await res.json();
    if(data.error) { alert(data.error); } else { alert('App Created!'); location.reload(); }
}
async function deleteApp(token) {
    if(!confirm('Delete application?')) return;
    let res = await fetch('/api/delete_app', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({token: token}) });
    let data = await res.json();
    alert(data.message); location.reload();
}
function copyToken() { navigator.clipboard.writeText(document.getElementById('tokenDisplay').innerText); alert('Copied!'); }
function selectApp(token) { document.getElementById('tokenDisplay').innerText = token; }
async function createUser() {
    let u = document.getElementById('newUsername').value.trim();
    let p = document.getElementById('newPassword').value.trim();
    let token = document.getElementById('tokenDisplay').innerText;
    if(!u || !p) { alert('Fill username and password!'); return; }
    let res = await fetch('/api/create_user', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: u, password: p, app_token: token, expiry_type: 'lifetime'}) });
    let data = await res.json();
    alert(data.message || data.error);
    if(res.ok) location.reload();
}
async function deleteUser(username) {
    if(!confirm('Delete ' + username + '?')) return;
    let res = await fetch('/api/delete_user', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: username}) });
    let data = await res.json();
    alert(data.message); location.reload();
}
async function resetHwid(username) {
    let res = await fetch('/api/reset_hwid', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: username}) });
    let data = await res.json();
    alert(data.message); location.reload();
}
async function toggleBan(username) {
    let res = await fetch('/api/toggle_ban', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: username}) });
    let data = await res.json();
    alert(data.message); location.reload();
}
</script>
</body>
</html>
"""
)

# ==========================================
# FLASK ROUTES
# ==========================================

@app.route("/")
def home(): return render_template_string(LANDING)

@app.route("/login")
def login(): return render_template_string(LOGIN)

@app.route("/auth/google")
@rate_limit(max_requests=5, window_seconds=60)
def auth_google():
  redirect_uri = request.url_root.rstrip("/") + "/auth/callback"
  return google.authorize_redirect(redirect_uri)

@app.route("/auth/callback")
def callback():
  try:
    token = google.authorize_access_token()
    user = token.get("userinfo") or google.get("https://openidconnect.googleapis.com/v1/userinfo").json()
    session["user"] = user
    return redirect("/dashboard")
  except Exception:
    return redirect("/login")

@app.route("/dashboard")
def dash():
  if "user" not in session: return redirect("/login")
  email = session["user"]["email"]
  is_paid = email in PAID_USERS
  limit_text = "Unlimited" if is_paid else "10"
  plan_text = "PRO UNLIMITED" if is_paid else "FREE"
  plan_color = "text-green-400" if is_paid else "text-yellow-400"

  apps = db("SELECT * FROM apps WHERE owner_email=?", (email,), True)
  if not apps:
    app_options = "<option>No Apps Created</option>"
    active_token = "Create an app"
    app_list_html = "<p class='text-zinc-500 text-sm'>No apps yet</p>"
  else:
    app_options = "".join([f"<option value='{a[2]}'>{a[1]}</option>" for a in apps])
    active_token = apps[0][2]
    app_list_html = "".join([
        f"""<div class='bg-black/80 border border-white/10 rounded-xl px-4 py-3 flex justify-between items-center mb-2'>
                <div><span class='font-bold text-white'>{a[1]}</span><br><span class='text-xs text-zinc-500 font-mono'>{a[2][:25]}...</span></div>
                <button onclick="deleteApp('{a[2]}')" class='bg-red-900/50 border border-red-500/30 px-3 py-1.5 rounded-lg text-xs text-red-300 font-semibold'>Delete App</button>
            </div>""" for a in apps
    ])

  tool_users = db("SELECT id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at FROM tool_users WHERE app_token IN (SELECT token FROM apps WHERE owner_email=?)", (email,), True) if apps else []
  tool_user_count = len(tool_users)

  tool_users_list_html = ""
  for u in tool_users:
    hwid_short = (u[5][:15] + "...") if u[5] else "Not Bound"
    status_color = "text-green-400" if u[4] == "active" else "text-red-400"
    ban_text = "Ban" if u[4] == "active" else "Unban"
    tool_users_list_html += f"""
        <div class='flex justify-between items-center bg-black/80 border border-white/10 rounded-xl px-4 py-3 mb-2'>
            <div>
                <span class='text-white font-bold'>{u[1]}</span><span class='text-zinc-500'> / {u[2]}</span><br>
                <span class='text-[10px] text-zinc-500'>HWID: {hwid_short} | Status: <span class='{status_color}'>{u[4].upper()}</span></span>
            </div>
            <div class='flex gap-2'>
                <button onclick="toggleBan('{u[1]}')" class='bg-yellow-900/50 px-3 py-1 rounded-lg text-[10px]'>{ban_text}</button>
                <button onclick="resetHwid('{u[1]}')" class='bg-zinc-800 px-3 py-1 rounded-lg text-[10px]'>Reset HWID</button>
                <button onclick="deleteUser('{u[1]}')" class='bg-red-900/50 px-3 py-1 rounded-lg text-[10px]'>Delete</button>
            </div>
        </div>"""
  if not tool_users_list_html: tool_users_list_html = "<p class='text-center text-zinc-600 text-xs mt-10'>No registered users found.</p>"

  html = DASHBOARD_HTML.replace("{{name}}", session["user"].get("name", "User")) \
      .replace("{{email}}", email).replace("{{app_options}}", app_options) \
      .replace("{{active_token}}", active_token).replace("{{app_list_html}}", app_list_html) \
      .replace("{{keys_list_html}}", "").replace("{{tool_user_count}}", str(tool_user_count)) \
      .replace("{{limit_text}}", limit_text).replace("{{plan_text}}", plan_text) \
      .replace("{{plan_color}}", plan_color).replace("{{percent}}", "10") \
      .replace("{{tool_users_list_html}}", tool_users_list_html)
  return render_template_string(html)

@app.route("/api/create_app", methods=["POST"])
def api_create_app():
  if "user" not in session: return jsonify({"error": "Unauthorized"}), 401
  email = session["user"]["email"]
  name = request.json.get("name", "").strip()
  if not name: return jsonify({"error": "Invalid app name"})
  token = f"HSL_{''.join(random.choices(string.ascii_uppercase + string.digits, k=24))}"
  db("INSERT INTO apps (name, token, owner_email, created_at) VALUES (?,?,?,?)", (name, token, email, datetime.now().isoformat()))
  return jsonify({"token": token})

@app.route("/api/delete_app", methods=["POST"])
def api_delete_app():
  if "user" not in session: return jsonify({"error": "Unauthorized"}), 401
  token = request.json.get("token")
  db("DELETE FROM apps WHERE token=?", (token,))
  db("DELETE FROM tool_users WHERE app_token=?", (token,))
  return jsonify({"message": "Application deleted successfully."})

@app.route("/api/create_user", methods=["POST"])
def api_create_user():
  data = request.json or {}
  app_token = data.get("app_token")
  username = str(data.get("username", "")).strip()
  password = str(data.get("password", "")).strip()
  if not username or not password or not app_token:
    return jsonify({"message": "Missing required fields!"}), 400

  try:
    db("INSERT INTO tool_users (username, password, app_token, status, created_at, expiry_type) VALUES (?,?,?,?,?,?)",
       (username, password, app_token, "active", datetime.now().isoformat(), "lifetime"))
    return jsonify({"message": f"User Created: {username}"}), 200
  except sqlite3.IntegrityError:
    return jsonify({"message": "Username already exists in this application!"}), 409
  except Exception:
    return jsonify({"message": "Could not create user."}), 500

@app.route("/api/delete_user", methods=["POST"])
def api_delete_user():
  if "user" not in session: return jsonify({"error": "Unauthorized"}), 401
  db("DELETE FROM tool_users WHERE username=?", (request.json.get("username"),))
  return jsonify({"message": "Deleted successfully"})

@app.route("/api/reset_hwid", methods=["POST"])
def api_reset_hwid():
  if "user" not in session: return jsonify({"error": "Unauthorized"}), 401
  username = request.json.get("username")
  db("UPDATE tool_users SET hwid=NULL, status='active' WHERE username=?", (username,))
  return jsonify({"message": f"HWID Reset for {username}"})

@app.route("/api/toggle_ban", methods=["POST"])
def api_toggle_ban():
  if "user" not in session: return jsonify({"error": "Unauthorized"}), 401
  username = request.json.get("username")
  res = db("SELECT status FROM tool_users WHERE username=?", (username,), True)
  if not res: return jsonify({"message": "User not found"})
  new_status = "banned" if res[0][0] == "active" else "active"
  db("UPDATE tool_users SET status=? WHERE username=?", (new_status, username))
  return jsonify({"message": f"{username} status updated to {new_status.upper()}"})

@app.route("/logout")
def logout(): session.clear(); return redirect("/")

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000, debug=False)