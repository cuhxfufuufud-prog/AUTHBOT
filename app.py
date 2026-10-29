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

  # Bulletproof migration to enforce per-app unique constraint on username
  try:
      cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_users'")
      table_sql = cur.fetchone()
      if table_sql and "UNIQUE(username, app_token)" not in table_sql[0]:
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
          try:
              cur.execute("""
                    INSERT INTO tool_users (id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at)
                    SELECT id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at FROM tool_users_old
                """)
          except Exception as migrate_ex:
              print(f"Data migration row copy note: {migrate_ex}")
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
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
      "Segoe UI", Roboto, sans-serif;
    letter-spacing: .01em;
  }

  ::selection {
    background: rgba(0,246,255,.28);
    color: white;
  }

  ::-webkit-scrollbar { width: 7px; height: 7px; }
  ::-webkit-scrollbar-track { background: #02040a; }
  ::-webkit-scrollbar-thumb {
    background: linear-gradient(var(--cyan), var(--violet));
    border-radius: 999px;
  }

  #c {
    position: fixed;
    inset: 0;
    z-index: 0;
    pointer-events: none;
    opacity: .9;
  }

  body::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 1;
    pointer-events: none;
    background:
      linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px);
    background-size: 42px 42px;
    mask-image: linear-gradient(to bottom, black, transparent 90%);
  }

  body::after {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 2;
    pointer-events: none;
    background: repeating-linear-gradient(
      0deg,
      rgba(255,255,255,.012) 0px,
      rgba(255,255,255,.012) 1px,
      transparent 1px,
      transparent 4px
    );
    opacity: .45;
  }

  .glass {
    position: relative;
    overflow: hidden;
    backdrop-filter: blur(24px) saturate(140%);
    -webkit-backdrop-filter: blur(24px) saturate(140%);
    background:
      linear-gradient(145deg, rgba(15,20,35,.88), rgba(3,7,15,.74));
    border: 1px solid rgba(0,246,255,.18);
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,.055),
      0 25px 80px rgba(0,0,0,.45),
      0 0 45px rgba(0,246,255,.06);
  }

  .glass::before,
  .glass-card::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: linear-gradient(
      120deg,
      rgba(0,246,255,.055),
      transparent 28%,
      transparent 72%,
      rgba(124,58,237,.045)
    );
  }

  .glass-card {
    position: relative;
    overflow: hidden;
    background:
      linear-gradient(145deg, rgba(12,17,30,.86), rgba(4,7,14,.72));
    border: 1px solid rgba(0,246,255,.13);
    backdrop-filter: blur(18px) saturate(130%);
    -webkit-backdrop-filter: blur(18px) saturate(130%);
    transition:
      transform .28s cubic-bezier(.2,.8,.2,1),
      border-color .28s ease,
      box-shadow .28s ease,
      background .28s ease;
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,.035),
      0 12px 45px rgba(0,0,0,.30);
  }

  .glass-card:hover {
    border-color: rgba(0,246,255,.38);
    transform: translateY(-5px);
    background:
      linear-gradient(145deg, rgba(15,24,42,.92), rgba(4,8,17,.80));
    box-shadow:
      inset 0 1px 0 rgba(255,255,255,.06),
      0 18px 55px rgba(0,0,0,.48),
      0 0 35px rgba(0,246,255,.13);
  }

  input, select {
    transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
  }

  input:focus, select:focus {
    border-color: rgba(0,246,255,.65) !important;
    box-shadow: 0 0 0 3px rgba(0,246,255,.07), 0 0 22px rgba(0,246,255,.10);
  }

  button, a {
    transition:
      transform .2s ease,
      filter .2s ease,
      border-color .2s ease,
      box-shadow .2s ease,
      background .2s ease;
  }

  button:hover, a:hover { filter: brightness(1.08); }

  button:active, a:active { transform: scale(.97); }

  .bg-gradient-to-r {
    background-size: 180% 100%;
    animation: gradientShift 5s ease infinite;
  }

  @keyframes gradientShift {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
  }

  .glass-card::after,
  .glass::after {
    content: "";
    position: absolute;
    width: 55px;
    height: 55px;
    right: -28px;
    bottom: -28px;
    border: 1px solid rgba(0,246,255,.18);
    transform: rotate(45deg);
    pointer-events: none;
  }

  #cursor-dot {
    position: fixed;
    width: 7px;
    height: 7px;
    background: var(--cyan);
    border-radius: 50%;
    pointer-events: none;
    z-index: 9999;
    transform: translate(-50%, -50%);
    box-shadow:
      0 0 8px var(--cyan),
      0 0 20px var(--cyan),
      0 0 38px rgba(0,246,255,.7);
  }

  #cursor-crosshair {
    position: fixed;
    width: 34px;
    height: 34px;
    border: 1px solid rgba(0,246,255,.7);
    border-radius: 50%;
    pointer-events: none;
    z-index: 9998;
    transform: translate(-50%, -50%);
    box-shadow: 0 0 18px rgba(0,246,255,.16);
  }

  #cursor-crosshair::before {
    content: "";
    position: absolute;
    top: 50%;
    left: -8px;
    width: 48px;
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(0,246,255,.85), transparent);
    transform: translateY(-50%);
  }

  #cursor-crosshair::after {
    content: "";
    position: absolute;
    left: 50%;
    top: -8px;
    height: 48px;
    width: 1px;
    background: linear-gradient(180deg, transparent, rgba(0,246,255,.85), transparent);
    transform: translateX(-50%);
  }

  #cursor-orbit {
    position: fixed;
    width: 56px;
    height: 56px;
    border: 1px dashed rgba(124,58,237,.75);
    border-radius: 50%;
    pointer-events: none;
    z-index: 9997;
    transform: translate(-50%, -50%);
    animation: spinOrbit 5s linear infinite;
    box-shadow: 0 0 18px rgba(124,58,237,.10);
  }

  @keyframes spinOrbit {
    0% { transform: translate(-50%, -50%) rotate(0deg); }
    100% { transform: translate(-50%, -50%) rotate(360deg); }
  }

  .side-active {
    position: relative;
    overflow: hidden;
    background:
      linear-gradient(90deg, rgba(0,246,255,.16), rgba(124,58,237,.08)) !important;
    border: 1px solid rgba(0,246,255,.42) !important;
    color: #67f7ff !important;
    font-weight: 800;
    box-shadow:
      inset 3px 0 0 var(--cyan),
      0 0 24px rgba(0,246,255,.08);
  }

  .side-active::after {
    content: "";
    position: absolute;
    top: 0;
    right: 0;
    width: 2px;
    height: 100%;
    background: var(--cyan);
    box-shadow: 0 0 14px var(--cyan);
  }

  @media (max-width: 768px) {
    body { cursor: auto; }
    #cursor-dot, #cursor-crosshair, #cursor-orbit { display: none; }
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: .01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: .01ms !important;
    }
  }
</style>
"""

CURSOR_SCRIPT = """
<div id="cursor-dot"></div>
<div id="cursor-crosshair"></div>
<div id="cursor-orbit"></div>
<script>
  const dot = document.getElementById('cursor-dot');
  const cross = document.getElementById('cursor-crosshair');
  const orbit = document.getElementById('cursor-orbit');
  let mx = 0, my = 0, cx = 0, cy = 0;

  window.addEventListener('mousemove', (e) => {
    mx = e.clientX;
    my = e.clientY;
    dot.style.left = mx + 'px';
    dot.style.top = my + 'px';
  });

  function renderCursor() {
    cx += (mx - cx) * 0.18;
    cy += (my - cy) * 0.18;
    cross.style.left = cx + 'px';
    cross.style.top = cy + 'px';
    orbit.style.left = cx + 'px';
    orbit.style.top = cy + 'px';
    requestAnimationFrame(renderCursor);
  }
  renderCursor();

  const canvasElement = document.getElementById('c');
  const ctx = canvasElement.getContext('2d');
  
  function resizeCanvas() { 
    canvasElement.width = window.innerWidth; 
    canvasElement.height = window.innerHeight; 
  }
  resizeCanvas(); 
  window.onresize = resizeCanvas;
  
  let particlesArray = [];
  for(let i = 0; i < 90; i++) {
    particlesArray.push({ 
      x: Math.random() * canvasElement.width, 
      y: Math.random() * canvasElement.height, 
      r: Math.random() * 1.8 + 0.5, 
      vy: Math.random() * 0.6 + 0.2, 
      opacity: Math.random() * 0.7 + 0.3 
    });
  }
  
  window.addEventListener('mousemove', (e) => {
    if(Math.random() > 0.4) {
      particlesArray.push({ 
        x: e.clientX, 
        y: e.clientY, 
        r: Math.random() * 2 + 1, 
        vy: -(Math.random() * 1.2 + 0.4), 
        opacity: 1 
      });
      if(particlesArray.length > 120) {
        particlesArray.shift();
      }
    }
  });

  function animateParticles() {
    ctx.clearRect(0, 0, canvasElement.width, canvasElement.height);
    particlesArray.forEach(p => {
      p.y -= p.vy;
      if(p.opacity > 0.3) {
        p.opacity -= 0.005;
      }
      if(p.y < 0) { 
        p.y = canvasElement.height; 
        p.x = Math.random() * canvasElement.width; 
        p.opacity = Math.random() * 0.7 + 0.3; 
      }
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(34, 211, 238, ${p.opacity})`;
      ctx.shadowBlur = 10;
      ctx.shadowColor = '#22d3ee';
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
<head>
"""
    + COMMON_HEAD
    + """
</head>
<body class="text-white overflow-x-hidden relative min-h-screen flex flex-col justify-between">
<canvas id="c"></canvas>

<nav class="relative z-10 flex justify-between items-center px-10 py-5 bg-black/55 backdrop-blur-xl border-b border-cyan-400/15 shadow-[0_8px_35px_rgba(0,0,0,0.35)]">
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

<div class="relative z-10 flex flex-col items-center text-center pt-20 px-4">
  <div class="inline-flex items-center gap-2 border border-cyan-500/30 bg-cyan-500/10 px-4 py-1.5 rounded-full text-xs text-cyan-300 font-semibold mb-8 shadow-[0_0_15px_rgba(34,211,238,0.2)]">
    <span>⚡</span> Next-Gen Secure Software Panel
  </div>
  <h1 class="text-6xl md:text-7xl font-black bg-gradient-to-r from-cyan-300 via-cyan-400 to-indigo-500 bg-clip-text text-transparent drop-shadow-[0_0_35px_rgba(34,211,238,0.4)] max-w-5xl leading-tight">HSL CORP AUTH</h1>
  <p class="text-zinc-400 mt-6 font-medium text-lg max-w-2xl">Ultimate Hardware-Locked Licensing & Application Protection Infrastructure.</p>
  <a href="/login" class="bg-gradient-to-r from-cyan-400 to-indigo-600 hover:scale-105 px-9 py-4 rounded-2xl text-sm font-bold shadow-[0_0_30px_rgba(34,211,238,0.6)] mt-9 transition duration-300 flex items-center gap-2">🚀 Get Started</a>
</div>

<div class="relative z-10 w-full max-w-6xl mx-auto px-6 py-20">
  <div class="grid md:grid-cols-3 gap-8">
    <div class="glass-card rounded-2xl p-7">
      <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-2xl mb-4">🔐</div>
      <h3 class="font-bold text-lg text-cyan-300">Hardware-Locked Protection</h3>
      <p class="text-xs text-zinc-400 mt-2 leading-relaxed">Binds license activations to distinct motherboard hash signatures, completely mitigating account-sharing and unauthorized leaks.</p>
    </div>
    <div class="glass-card rounded-2xl p-7">
      <div class="w-12 h-12 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-2xl mb-4">🛡️</div>
      <h3 class="font-bold text-lg text-indigo-300">Hardened API Security</h3>
      <p class="text-xs text-zinc-400 mt-2 leading-relaxed">Protects endpoint payloads using dynamic SHA-256 signatures, defending against Fiddler, HTTP Debugger, and response spoofing.</p>
    </div>
    <div class="glass-card rounded-2xl p-7">
      <div class="w-12 h-12 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-2xl mb-4">⚡</div>
      <h3 class="font-bold text-lg text-cyan-300">Instant Admin Console</h3>
      <p class="text-xs text-zinc-400 mt-2 leading-relaxed">Manage user registrations, execute immediate HWID resets, issue instant bans, and monitor active app tokens in real time.</p>
    </div>
  </div>
</div>

<footer class="relative z-10 text-center py-6 border-t border-white/5 text-xs text-zinc-600">
  &copy; 2026 HSL CORP. All rights reserved.
</footer>
"""
    + CURSOR_SCRIPT
    + """
</body>
</html>"""
)

LOGIN = (
    """<!DOCTYPE html>
<html>
<head>
"""
    + COMMON_HEAD
    + """
</head>
<body class="flex items-center justify-center h-screen overflow-hidden relative">
<canvas id="c"></canvas>
<div class="relative z-10 w-[420px] glass rounded-[28px] p-9 text-center shadow-[0_0_60px_rgba(34,211,238,0.2)]">
  <div class="w-16 h-16 bg-gradient-to-r from-cyan-400 to-indigo-600 rounded-2xl mx-auto flex items-center justify-center shadow-[0_0_25px_#22d3ee]">👾</div>
  <h1 class="font-black text-2xl mt-5 text-white bg-gradient-to-r from-cyan-300 to-indigo-400 bg-clip-text text-transparent">HSL CORP</h1>
  <p class="text-xs text-zinc-400 mt-1">Sign in using verified OAuth endpoints</p>
  <a href="/auth/google" class="mt-8 w-full bg-white hover:bg-zinc-100 text-black rounded-xl py-3.5 flex justify-center items-center gap-3 font-bold text-sm shadow-lg hover:scale-[1.02] transition">
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
<head>
"""
    + COMMON_HEAD
    + """
<style>
  .side-active { 
    background: rgba(34,211,238,0.15); 
    border: 1px solid rgba(34,211,238,0.4); 
    color: #22d3ee !important; 
    font-weight: bold; 
  }
</style>
</head>
<body class="flex h-screen text-white overflow-hidden relative">
<canvas id="c"></canvas>

<div class="w-[260px] bg-[#02040a]/90 backdrop-blur-2xl border-r border-cyan-400/10 shadow-[15px_0_45px_rgba(0,0,0,0.35)] flex flex-col relative z-10">
  <div class="p-5 flex items-center gap-3 border-b border-white/10">
    <div class="w-9 h-9 bg-gradient-to-r from-cyan-400 to-indigo-600 rounded-xl flex items-center justify-center shadow-[0_0_12px_#22d3ee]">👾</div>
    <div>
      <p class="font-black text-sm tracking-wide">HSL CORP</p>
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
  <div class="h-14 bg-black/55 backdrop-blur-xl border-b border-cyan-400/10 shadow-[0_8px_35px_rgba(0,0,0,0.3)] flex items-center justify-between px-8">
    <p class="text-xs font-semibold tracking-wider text-cyan-300">HSL CONSOLE - {{plan_text}} PLAN</p>
    <button onclick="showTab('billing')" class="text-xs bg-gradient-to-r from-cyan-400 to-indigo-600 hover:opacity-90 text-white px-5 py-2 rounded-full font-bold shadow-[0_0_15px_rgba(34,211,238,0.4)] transition">Upgrade to Unlimited</button>
  </div>
  
  <div class="p-8">
    <div id="tab-overview">
      <h1 class="text-2xl font-black">Dashboard Overview</h1>
      <div class="mt-6 grid grid-cols-[1.3fr_1fr_0.7fr] gap-4">
        <div class="glass-card rounded-2xl p-5">
          <p class="text-[10px] font-bold text-cyan-400 tracking-wider">ACTIVE APPLICATION</p>
          <select id="appSelect" onchange="selectApp(this.value)" class="bg-black/80 border border-white/20 rounded-xl px-3 py-2.5 text-xs mt-3 w-full font-semibold focus:outline-none focus:border-cyan-400">{{app_options}}</select>
        </div>
        <div class="glass-card rounded-2xl p-5">
          <p class="text-[10px] font-bold text-zinc-400 tracking-wider">MASTER APP TOKEN</p>
          <div class="mt-3 flex justify-between items-center bg-black/80 rounded-xl px-3 py-2 border border-white/10">
            <p id="tokenDisplay" class="text-xs font-mono text-zinc-300 truncate">{{active_token}}</p>
            <button onclick="copyToken()" class="text-[10px] bg-gradient-to-r from-cyan-400 to-indigo-600 px-3 py-1.5 rounded-lg font-bold hover:scale-105 transition">Copy</button>
          </div>
        </div>
        <div class="glass-card rounded-2xl p-5">
          <p class="text-[10px] font-bold text-zinc-400 tracking-wider">PLAN LIMIT</p>
          <p class="text-xs font-bold mt-3">{{tool_user_count}} / {{limit_text}} Used</p>
          <div class="w-full bg-zinc-800 h-2 mt-3 rounded-full overflow-hidden">
            <div class="bg-gradient-to-r from-cyan-400 to-indigo-600 h-2 rounded-full" style="width:{{percent}}%"></div>
          </div>
        </div>
      </div>
      
      <div class="mt-6 glass-card rounded-2xl p-7">
        <p class="text-base font-bold text-white">+ Create New Secure User</p>
        <div class="flex gap-3 mt-4">
          <input id="newUsername" placeholder="Username" class="flex-1 bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
          <input id="newPassword" placeholder="Password" class="flex-1 bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
        </div>
        <div class="flex gap-3 mt-3">
          <select id="expiryType" onchange="toggleExpiryDays()" class="flex-1 bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-cyan-400">
            <option value="lifetime">Lifetime (No Expiry)</option>
            <option value="custom">Custom Expiry (Days)</option>
          </select>
          <input id="expiryDays" type="number" min="1" max="36500" value="30" placeholder="Days" class="hidden flex-1 bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
        </div>
        <p class="mt-2 text-[10px] text-zinc-500">Lifetime users never expire. Custom expiry starts from the time this user is created.</p>
        <button onclick="createUser()" class="mt-4 w-full bg-gradient-to-r from-cyan-400 to-indigo-600 hover:opacity-90 py-3 rounded-xl text-sm font-bold shadow-[0_0_20px_rgba(34,211,238,0.4)] transition">Create User</button>
      </div>
    </div>

    <div id="tab-applications" class="hidden">
      <h1 class="text-2xl font-black">Applications</h1>
      <div class="glass-card mt-6 rounded-2xl p-7">
        <div class="space-y-3 mb-6">{{app_list_html}}</div>
        <div class="border-t border-white/10 pt-5">
          <p class="text-base font-bold">+ Create New App</p>
          <input id="newAppName" placeholder="App Name" class="mt-3 w-full bg-black/80 border border-white/15 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-cyan-400">
          <button onclick="createApp()" class="mt-4 w-full bg-gradient-to-r from-cyan-400 to-indigo-600 py-3 rounded-xl text-sm font-bold shadow-[0_0_20px_rgba(34,211,238,0.4)] transition">Create Application</button>
        </div>
      </div>
    </div>

    <div id="tab-tool_users" class="hidden">
      <h1 class="text-2xl font-black">Username / Pass Users ({{tool_user_count}}/{{limit_text}})</h1>
      <div class="glass-card mt-6 rounded-2xl p-6">
        <div class="space-y-3 text-xs font-mono">{{tool_users_list_html}}</div>
      </div>
    </div>

    <div id="tab-keys" class="hidden">
      <div class="flex justify-between items-center mb-6">
        <h1 class="text-2xl font-black">License Keys</h1>
        <button onclick="createKey()" class="bg-gradient-to-r from-cyan-400 to-indigo-600 hover:opacity-90 px-5 py-2.5 rounded-xl text-xs font-bold shadow-[0_0_15px_rgba(34,211,238,0.4)] transition">+ Create New Key</button>
      </div>
      <div class="glass-card rounded-2xl p-6">
        <div class="space-y-3 text-xs font-mono">{{keys_list_html}}</div>
      </div>
    </div>

    <div id="tab-integrate" class="hidden">
      <h1 class="text-2xl font-black">Secure Client Integration Code</h1>
      <div class="glass-card mt-6 rounded-2xl p-7">
        <p class="text-xs font-bold text-cyan-300">Encrypted Payload Verification Script (Anti-Fiddler/Anti-HTTP Debugger)</p>
        <pre class="mt-4 bg-black/90 border border-white/10 rounded-xl p-5 text-xs font-mono overflow-x-auto text-green-400 leading-relaxed">
import requests
import subprocess
import hashlib

MY_APP_TOKEN = "{{active_token}}"
AUTH_URL = "https://YOUR-DOMAIN.com/api/auth_login"

def get_hwid():
    try:
        raw = subprocess.check_output('wmic baseboard get serialnumber', shell=True).decode()
        return raw.split('\\n')[1].strip()
    except Exception:
        return "UNKNOWN_HWID"

def secure_login(username, password):
    hwid = get_hwid()
    raw_sig = f"{username}:{hwid}:{MY_APP_TOKEN}"
    sig = hashlib.sha256(raw_sig.encode()).hexdigest()
    
    payload = {
        "username": username,
        "password": password,
        "hwid": hwid,
        "token": MY_APP_TOKEN,
        "sig": sig
    }
    
    try:
        res = requests.post(AUTH_URL, json=payload, timeout=10)
        return res.json()
    except Exception as e:
        return {"status": "error", "message": "Connection Tampered"}
</pre>
      </div>
    </div>

    <div id="tab-billing" class="hidden">
      <h1 class="text-2xl font-black">Billing / Plans</h1>
      <div class="grid grid-cols-2 gap-6 mt-6">
        <div class="glass-card rounded-2xl p-7">
          <p class="font-bold text-sm text-zinc-300">FREE PLAN</p>
          <p class="text-4xl font-black mt-2">₹0</p>
          <p class="text-xs text-zinc-400 mt-3 leading-relaxed">✓ 10 Users / Keys Only<br>✓ 2 Applications (With Delete Option)<br>✓ HWID Lock</p>
          <p class="mt-6 text-xs bg-zinc-800/80 rounded-full px-4 py-1.5 inline-block font-semibold">Current: {{plan_text}}</p>
        </div>
        <div class="glass-card rounded-2xl p-7 border-cyan-400/50 bg-cyan-500/10">
          <p class="font-bold text-sm text-cyan-400">PRO UNLIMITED</p>
          <p class="text-4xl font-black mt-2">₹499</p>
          <p class="text-xs text-zinc-200 mt-3 leading-relaxed">✓ Unlimited Users<br>✓ Unlimited Apps<br>✓ Unlimited Keys<br>✓ Anti-Crack Engine</p>
          <a href="https://wa.me/919999999999" target="_blank" class="mt-6 block text-center bg-gradient-to-r from-cyan-400 to-indigo-600 py-3 rounded-xl text-sm font-bold shadow-[0_0_25px_rgba(34,211,238,0.5)] transition">Buy on WhatsApp</a>
        </div>
      </div>
    </div>
  </div>
</div>
"""
    + CURSOR_SCRIPT
    + """
<script>
function showTab(name) {
    document.querySelectorAll('[id^="tab-"]').forEach(d => d.classList.add('hidden'));
    document.getElementById('tab-' + name).classList.remove('hidden');
    
    document.querySelectorAll('#sidebar button').forEach(b => {
        b.classList.remove('side-active');
        b.classList.add('text-zinc-400');
    });
    
    let btn = document.getElementById('btn-' + name);
    if(btn) {
        btn.classList.add('side-active');
        btn.classList.remove('text-zinc-400');
    }
}

async function createApp() {
    let name = document.getElementById('newAppName').value.trim();
    if(!name) {
        alert('Enter Name!');
        return;
    }
    let res = await fetch('/api/create_app', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name})
    });
    let data = await res.json();
    if(data.error) {
        alert(data.error);
    } else {
        alert('App Created! Token: ' + data.token);
        location.reload();
    }
}

async function deleteApp(token) {
    if(!confirm('Are you sure you want to delete this application? Associated users might also be affected.')) return;
    let res = await fetch('/api/delete_app', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token: token})
    });
    let data = await res.json();
    alert(data.message);
    location.reload();
}

async function createKey() {
    let token = document.getElementById('tokenDisplay').innerText;
    if(!token || token.includes('Create') || token.includes('Token')) {
        alert('Please select or create a valid application first!');
        return;
    }
    let res = await fetch('/api/create_key', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({app_token: token})
    });
    let data = await res.json();
    alert(data.message || data.error || 'Key creation processed.');
    if(res.ok) location.reload();
}

async function deleteKey(keyText) {
    if(!confirm('Are you sure you want to delete license key ' + keyText + '?')) return;
    let res = await fetch('/api/delete_key', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key_text: keyText})
    });
    let data = await res.json();
    alert(data.message);
    location.reload();
}

function copyToken() {
    let t = document.getElementById('tokenDisplay').innerText;
    navigator.clipboard.writeText(t);
    alert('Copied: ' + t);
}

function selectApp(token) {
    document.getElementById('tokenDisplay').innerText = token;
}

function toggleExpiryDays() {
    const type = document.getElementById('expiryType').value;
    const days = document.getElementById('expiryDays');
    if (type === 'custom') {
        days.classList.remove('hidden');
    } else {
        days.classList.add('hidden');
    }
}

async function createUser() {
    let u = document.getElementById('newUsername').value.trim();
    let p = document.getElementById('newPassword').value.trim();
    let token = document.getElementById('tokenDisplay').innerText;
    let expiry_type = document.getElementById('expiryType').value;
    let days = parseInt(document.getElementById('expiryDays').value || '30', 10);

    if(!u || !p) {
        alert('Fill username and password!');
        return;
    }
    if (expiry_type === 'custom' && (!Number.isInteger(days) || days < 1 || days > 36500)) {
        alert('Expiry days must be between 1 and 36500.');
        return;
    }

    let res = await fetch('/api/create_user', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: u, password: p, app_token: token, expiry_type: expiry_type, days: days})
    });
    let data = await res.json();
    alert(data.message || data.error || 'Request completed.');
    if (res.ok && data.message && (data.message.startsWith('User Created:') || data.message.includes('Created'))) location.reload();
}

async function deleteUser(username) {
    if(!confirm('Delete ' + username + '?')) return;
    let res = await fetch('/api/delete_user', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: username})
    });
    let data = await res.json();
    alert(data.message);
    location.reload();
}

async function resetHwid(username) {
    let res = await fetch('/api/reset_hwid', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: username})
    });
    let data = await res.json();
    alert(data.message);
    location.reload();
}

async function toggleBan(username) {
    let res = await fetch('/api/toggle_ban', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: username})
    });
    let data = await res.json();
    alert(data.message);
    location.reload();
}

async function editUser(oldU, oldP) {
    let newU = prompt("New Username:", oldU); 
    if(newU === null) return;
    
    let newP = prompt("New Password:", oldP); 
    if(newP === null) return;
    
    let res = await fetch('/api/edit_user', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            old_username: oldU, 
            new_username: newU.trim(), 
            new_password: newP.trim()
        })
    });
    let data = await res.json();
    alert(data.message);
    location.reload();
}
</script>
</body>
</html>
"""
)

# ==========================================
# FLASK WEB ROUTES
# ==========================================


@app.route("/")
def home():
  return render_template_string(LANDING)


@app.route("/login")
def login():
  return render_template_string(LOGIN)


@app.route("/auth/google")
@rate_limit(max_requests=5, window_seconds=60)
def auth_google():
  redirect_uri = request.url_root.rstrip("/") + "/auth/callback"
  return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def callback():
  try:
    token = google.authorize_access_token()
    user = token.get("userinfo") or google.get(
        "https://openidconnect.googleapis.com/v1/userinfo"
    ).json()
    session["user"] = user
    return redirect("/dashboard")
  except Exception:
    return redirect("/login")


@app.route("/dashboard")
def dash():
  if "user" not in session:
    return redirect("/login")

  email = session["user"]["email"]
  is_paid = email in PAID_USERS
  limit_text = "Unlimited" if is_paid else "10"
  plan_text = "PRO UNLIMITED" if is_paid else "FREE"
  plan_color = "text-green-400" if is_paid else "text-yellow-400"

  apps = db("SELECT * FROM apps WHERE owner_email=?", (email,), True)

  if not apps:
    app_options = "<option>No Apps Created</option>"
    active_token = "Create an app to get Token"
    app_list_html = (
        "<p class='text-zinc-500 text-sm'>No apps yet - Create one below</p>"
    )
  else:
    app_options = "".join(
        [f"<option value='{a[2]}'>{a[1]}</option>" for a in apps]
    )
    active_token = apps[0][2]
    app_list_html = "".join([
        f"""<div class='bg-black/80 border border-white/10 rounded-xl px-4 py-3 flex justify-between items-center mb-2'>
                <div>
                    <span class='font-bold text-white'>{a[1]}</span><br>
                    <span class='text-xs text-zinc-500 font-mono'>{a[2][:25]}...</span>
                </div>
                <button onclick="deleteApp('{a[2]}')" class='bg-red-900/50 border border-red-500/30 px-3 py-1.5 rounded-lg text-xs hover:bg-red-800 transition text-red-300 font-semibold'>Delete App</button>
            </div>"""
        for a in apps
    ])

  keys = (
      db(
          "SELECT * FROM keys WHERE app_token IN (SELECT token FROM apps WHERE owner_email=?)",
          (email,),
          True,
      )
      if apps
      else []
  )

  if keys:
    keys_list_html = "".join([
        (
            "<div class='flex justify-between items-center bg-black/80 border"
            " border-white/10 rounded-xl px-4 py-3 mb-2'>"
            f"<div><span class='font-bold text-white'>{k[1]}</span><br>"
            f"<span class='text-[10px] {'text-green-400' if k[3] == 'unused' else 'text-red-400'}'>● Status: {k[3]}</span></div>"
            f"<button onclick=\"deleteKey('{k[1]}')\" class='bg-red-900/50 border border-red-500/30 px-3 py-1.5 rounded-lg text-xs hover:bg-red-800 transition text-red-300 font-semibold'>Delete Key</button>"
            "</div>"
        )
        for k in keys
    ])
  else:
    keys_list_html = (
        "<p class='text-center text-zinc-600 text-xs mt-10'>No keys generated yet.</p>"
    )

  tool_users = (
      db(
          "SELECT id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at "
          "FROM tool_users WHERE app_token IN (SELECT token FROM apps WHERE owner_email=?)",
          (email,),
          True,
      )
      if apps
      else []
  )
  tool_user_count = len(tool_users)

  if tool_user_count == 0:
    percent = 10
  else:
    max_limit = 999999 if is_paid else 10
    percent = min(int(tool_user_count / max_limit * 100), 100)

  tool_users_list_html = ""
  for u in tool_users:
    hwid_short = (u[5][:15] + "...") if u[5] else "Not Bound"
    status_color = "text-green-400" if u[4] == "active" else "text-red-400"
    ban_text = "Ban" if u[4] == "active" else "Unban"
    expiry_type = u[7] if len(u) > 7 and u[7] else "lifetime"
    expires_at = u[8] if len(u) > 8 else None
    if expiry_type == "lifetime" or not expires_at:
      expiry_label = "Lifetime"
    else:
      expiry_label = f"Expires: {expires_at.replace('T', ' ')[:19]}"

    tool_users_list_html += f"""
        <div class='flex justify-between items-center bg-black/80 border border-white/10 rounded-xl px-4 py-3 mb-2'>
            <div>
                <span class='text-white font-bold'>{u[1]}</span><span class='text-zinc-500'> / {u[2]}</span><br>
                <span class='text-[10px] text-zinc-500'>HWID: {hwid_short} | Status: <span class='{status_color}'>{u[4].upper()}</span> | Expiry: <span class='text-cyan-300'>{expiry_label}</span></span>
            </div>
            <div class='flex gap-2 flex-wrap justify-end max-w-[60%]'>
                <button onclick="editUser('{u[1]}','{u[2]}')" class='bg-blue-900/50 border border-blue-500/30 px-3 py-1 rounded-lg text-[10px] hover:bg-blue-800 transition'>Edit</button>
                <button onclick="toggleBan('{u[1]}')" class='bg-yellow-900/50 border border-yellow-500/30 px-3 py-1 rounded-lg text-[10px] hover:bg-yellow-800 transition'>{ban_text}</button>
                <button onclick="resetHwid('{u[1]}')" class='bg-zinc-800 border border-white/10 px-3 py-1 rounded-lg text-[10px] hover:bg-zinc-700 transition'>Reset HWID</button>
                <button onclick="deleteUser('{u[1]}')" class='bg-red-900/50 border border-red-500/30 px-3 py-1 rounded-lg text-[10px] hover:bg-red-800 transition'>Delete</button>
            </div>
        </div>
        """

  if not tool_users_list_html:
    tool_users_list_html = (
        "<p class='text-center text-zinc-600 text-xs mt-10'>No registered users found.</p>"
    )

  html = (
      DASHBOARD_HTML.replace("{{name}}", session["user"].get("name", "User"))
      .replace("{{email}}", email)
      .replace("{{app_options}}", app_options)
      .replace("{{active_token}}", active_token)
      .replace("{{app_list_html}}", app_list_html)
      .replace("{{keys_list_html}}", keys_list_html)
      .replace("{{tool_user_count}}", str(tool_user_count))
      .replace("{{limit_text}}", limit_text)
      .replace("{{plan_text}}", plan_text)
      .replace("{{plan_color}}", plan_color)
      .replace("{{percent}}", str(percent))
      .replace("{{tool_users_list_html}}", tool_users_list_html)
  )
  return render_template_string(html)


# ==========================================
# REST API ENDPOINTS
# ==========================================


def check_limit(email):
  if email in PAID_USERS:
    return False

  tool_users = db(
      "SELECT COUNT(*) FROM tool_users WHERE app_token IN (SELECT token FROM apps WHERE owner_email=?)",
      (email,),
      True,
  )
  keys = db(
      "SELECT COUNT(*) FROM keys WHERE app_token IN (SELECT token FROM apps WHERE owner_email=?)",
      (email,),
      True,
  )

  user_count = tool_users[0][0] if tool_users else 0
  key_count = keys[0][0] if keys else 0

  return (user_count + key_count) >= 10


@app.route("/api/create_app", methods=["POST"])
def api_create_app():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  email = session["user"]["email"]
  apps = db("SELECT COUNT(*) FROM apps WHERE owner_email=?", (email,), True)

  if email not in PAID_USERS and apps[0][0] >= 2:
    return jsonify({
        "error": (
            "Free Plan limit reached. Max 2 applications allowed. Delete an"
            " existing app to create a new one."
        )
    })

  name = request.json.get("name", "").strip()
  if not name:
    return jsonify({"error": "Invalid app name"})

  random_str = "".join(
      random.choices(string.ascii_uppercase + string.digits, k=24)
  )
  token = f"HSL_{random_str}"

  db(
      "INSERT INTO apps (name, token, owner_email, created_at) VALUES (?,?,?,?)",
      (name, token, email, datetime.now().isoformat()),
  )

  return jsonify({"token": token})


@app.route("/api/delete_app", methods=["POST"])
def api_delete_app():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  email = session["user"]["email"]
  token = request.json.get("token")

  app_check = db(
      "SELECT * FROM apps WHERE token=? AND owner_email=?", (token, email), True
  )
  if not app_check:
    return jsonify({"message": "App not found or unauthorized!"})

  db("DELETE FROM apps WHERE token=?", (token,))
  db("DELETE FROM tool_users WHERE app_token=?", (token,))
  db("DELETE FROM keys WHERE app_token=?", (token,))

  return jsonify({"message": "Application deleted successfully."})


@app.route("/api/create_key", methods=["POST"])
def api_create_key():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  email = session["user"]["email"]
  data = request.json or {}
  app_token = data.get("app_token")

  if not app_token:
    return jsonify({"message": "App token missing!"}), 400

  app_check = db(
      "SELECT * FROM apps WHERE token=? AND owner_email=?", (app_token, email), True
  )
  if not app_check:
    return jsonify({"message": "Invalid App Token or unauthorized!"}), 403

  if check_limit(email):
    return jsonify({"message": "Plan limit reached!"}), 403

  random_key_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
  key_text = f"HSL-KEY-{random_key_part}"

  try:
    db(
        "INSERT INTO keys (key_text, app_token, status, created_at) VALUES (?,?,?,?)",
        (key_text, app_token, "unused", datetime.now().isoformat(timespec="seconds")),
    )
    return jsonify({"message": f"Key Created: {key_text}", "key": key_text}), 200
  except Exception as e:
    return jsonify({"message": "Could not create key due to a server error."}), 500


@app.route("/api/delete_key", methods=["POST"])
def api_delete_key():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  email = session["user"]["email"]
  key_text = request.json.get("key_text")

  if not key_text:
    return jsonify({"message": "Key text missing!"}), 400

  key_check = db(
      "SELECT * FROM keys WHERE key_text=? AND app_token IN (SELECT token FROM apps WHERE owner_email=?)",
      (key_text, email),
      True,
  )
  if not key_check:
    return jsonify({"message": "Key not found or unauthorized!"}), 403

  db("DELETE FROM keys WHERE key_text=?", (key_text,))
  return jsonify({"message": "License key deleted successfully."})


@app.route("/api/create_user", methods=["POST"])
def api_create_user():
  data = request.json or {}
  app_token = data.get("app_token")
  username = str(data.get("username", "")).strip()
  password = str(data.get("password", "")).strip()
  expiry_type = str(data.get("expiry_type", "lifetime")).strip().lower()

  if not username or not password or not app_token:
    return jsonify({"message": "Missing required fields!"}), 400

  if expiry_type not in ("lifetime", "custom"):
    return jsonify({"message": "Invalid expiry type."}), 400

  expires_at = None
  if expiry_type == "custom":
    try:
      days = int(data.get("days", 0))
    except (TypeError, ValueError):
      return jsonify({"message": "Expiry days must be a whole number."}), 400
    if days < 1 or days > 36500:
      return jsonify({"message": "Expiry days must be between 1 and 36500."}), 400
    expires_at = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")

  app_data = db("SELECT owner_email FROM apps WHERE token=?", (app_token,), True)
  if not app_data:
    return jsonify({"message": "Invalid App Token!"}), 400

  email = app_data[0][0]
  if check_limit(email):
    return jsonify({"message": "Plan limit reached!"}), 403

  try:
    db(
        "INSERT INTO tool_users (username, password, app_token, status, created_at, expiry_type, expires_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (username, password, app_token, "active", datetime.now().isoformat(timespec="seconds"), expiry_type, expires_at),
    )
    if expiry_type == "lifetime":
      return jsonify({"message": f"User Created: {username} (Lifetime)"}), 200
    return jsonify({"message": f"User Created: {username} (expires in {days} days)"}), 200
  except sqlite3.IntegrityError:
    return jsonify({"message": "Username already exists in this application!"}), 409
  except Exception as exc:
    app.logger.exception("Could not create tool user")
    return jsonify({"message": "Could not create user due to a server error."}), 500


@app.route("/api/delete_user", methods=["POST"])
def api_delete_user():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  username = request.json.get("username")
  db("DELETE FROM tool_users WHERE username=?", (username,))
  return jsonify({"message": "Deleted successfully"})


@app.route("/api/reset_hwid", methods=["POST"])
def api_reset_hwid():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  username = request.json.get("username")
  db(
      "UPDATE tool_users SET hwid=NULL, status='active' WHERE username=?",
      (username,),
  )
  return jsonify({"message": f"HWID Reset for {username}"})


@app.route("/api/toggle_ban", methods=["POST"])
def api_toggle_ban():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  username = request.json.get("username")
  res = db("SELECT status FROM tool_users WHERE username=?", (username,), True)

  if not res:
    return jsonify({"message": "User not found"})

  new_status = "banned" if res[0][0] == "active" else "active"
  db("UPDATE tool_users SET status=? WHERE username=?", (new_status, username))

  return jsonify({"message": f"{username} status updated to {new_status.upper()}"})


@app.route("/api/edit_user", methods=["POST"])
def api_edit_user():
  if "user" not in session:
    return jsonify({"error": "Unauthorized"}), 401

  old_u = request.json.get("old_username")
  new_u = request.json.get("new_username") or old_u
  new_p = request.json.get("new_password")

  try:
    db(
        "UPDATE tool_users SET username=?, password=? WHERE username=?",
        (new_u, new_p, old_u),
    )
    return jsonify({"message": f"Updated {old_u}"})
  except Exception:
        return jsonify({"message": "Update failed."})


# DISCORD WEBHOOK LOGGING
DISCORD_LOG_WEBHOOK_URL = "https://discord.com/api/webhooks/1564115981855498330/AuNKSNnxiwUu0t68ygJuXTqe9DAwdFseAMu14uhY8rkUhSxj6E214uMKj9W6n8Re0-dK"

def send_login_log(username, status_msg, hwid, ip_addr):
    if not DISCORD_LOG_WEBHOOK_URL or "YOUR_DISCORD" in DISCORD_LOG_WEBHOOK_URL:
        return
    
    current_time = int(time.time())
    timestamp_str = f"<t:{current_time}:F> (<t:{current_time}:R>)"
    
    if status_msg == "valid":
        color = 0x2ECC71
        status_badge = "🟢 `SUCCESS / AUTHENTICATED`"
    elif "HWID Bound" in status_msg:
        color = 0x3498DB
        status_badge = "🔵 `NEW DEVICE BOUND`"
    elif status_msg == "hwid_mismatch":
        color = 0xE67E22
        status_badge = "🟠 `HWID MISMATCH DETECTED`"
    else:
        color = 0xE74C3C
        status_badge = f"🔴 `FAILED ({status_msg.upper()})`"

    payload = {
        "username": "HSL AUTH BOT",
        "avatar_url": "https://images-ext-1.discordapp.net/external/H9ZL2wi0olXisnxXBPBNi8GO6sQebrD5_SwF-EfuulM/https/media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExZ3RqemR3c3A0MHl3NWw1NHE4a2FjdWVkdDdqdXppaXdxdHhobGF5ayZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/iBILBPeCHDVuELjOND/giphy.gif",
        "embeds": [{
            "title": "🛡️ HSL CORPORATION • LOGIN AUDIT LOG",
            "color": color,
            "description": f"A new authentication request was processed by the core system.",
            "fields": [
                {"name": "👤 User Identity", "value": f"```{username}```", "inline": True},
                {"name": "📊 Security Status", "value": status_badge, "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": True},
                {"name": "⏰ Timestamp", "value": timestamp_str, "inline": False},
                {"name": "🌐 IP Address", "value": f"[{ip_addr}](https://ip-api.com/#'{ip_addr}')", "inline": True},
                {"name": "💻 Hardware ID (HWID)", "value": f"```{hwid}```", "inline": False}
            ],
            "footer": {
                "text": "HSL AUTH SECURITY ENGINE v2.0 • Confidential Audit Trail",
                "icon_url": "https://i.imgur.com/8N41A5R.png"
            }
        }]
    }
    
    try:
        requests.post(DISCORD_LOG_WEBHOOK_URL, json=payload, timeout=3)
    except Exception as e:
        print(f"Failed to send Discord Log: {e}")


@app.route("/api/auth_login", methods=["POST"])
@rate_limit(max_requests=10, window_seconds=60)
def api_auth_login():
    data = request.json or {}

    username = data.get("username")
    password = data.get("password")
    hwid = data.get("hwid")
    token = data.get("token")
    client_sig = data.get("sig")
    client_ip = request.remote_addr

    if not username or not password or not token or not hwid:
        return jsonify({"status": "invalid", "message": "Malformed request parameters"}), 400

    expected_sig = hashlib.sha256(f"{username}:{hwid}:{token}".encode()).hexdigest()
    if client_sig and client_sig != expected_sig:
        send_login_log(username, "tampered", hwid, client_ip)
        return jsonify({"status": "tampered", "message": "Request payload tampered!"}), 403

    res = db("SELECT id, username, password, app_token, status, hwid, created_at, expiry_type, expires_at FROM tool_users WHERE username=? AND password=? AND app_token=?", (username, password, token), True)
    if not res:
        send_login_log(username, "invalid_credentials", hwid, client_ip)
        return jsonify({"status": "invalid", "message": "Incorrect credentials"})

    user_row = res[0]
    expiry_type = user_row[7] if len(user_row) > 7 and user_row[7] else "lifetime"
    expires_at = user_row[8] if len(user_row) > 8 else None
    
    def is_tool_user_expired(exp_str):
        if not exp_str:
            return False
        try:
            return datetime.now() > datetime.fromisoformat(exp_str)
        except Exception:
            return False

    if expiry_type == "custom" and is_tool_user_expired(expires_at):
        send_login_log(username, "expired", hwid, client_ip)
        return jsonify({"status": "expired", "message": "License expired. Contact the owner."}), 403

    if user_row[4] == "banned":
        send_login_log(username, "banned_attempt", hwid, client_ip)
        return jsonify({"status": "banned", "message": "Account suspended"})

    if not user_row[5]:
        db("UPDATE tool_users SET hwid=?, status='active' WHERE username=? AND app_token=?", (hwid, username, token))
        send_login_log(username, "valid (HWID Bound)", hwid, client_ip)
        return jsonify({"status": "valid", "message": "HWID Bound Successfully"})
    else:
        if user_row[5] == hwid:
            send_login_log(username, "valid", hwid, client_ip)
            return jsonify({"status": "valid", "message": "Authentication Success"})
        else:
            send_login_log(username, "hwid_mismatch", hwid, client_ip)
            return jsonify({"status": "hwid_mismatch", "message": "Hardware mismatch detected"})


# ==========================================
# DISCORD BOT INTEGRATION API ENDPOINTS (APP_TOKEN FILTERING FIXED)
# ==========================================





@app.route("/api/bot/toggle_ban", methods=["POST"])
def api_bot_toggle_ban():
  data = request.json or {}
  username = data.get("username")
  if not username:
    return jsonify({"status": "error", "message": "Username missing"}), 400

  res = db(
      "SELECT status FROM tool_users WHERE username=?", (username,), fetch=True
  )
  if not res:
    return jsonify({"status": "error", "message": "User not found"}), 404

  new_status = "banned" if res[0][0] == "active" else "active"
  db("UPDATE tool_users SET status=? WHERE username=?", (new_status, username))
  return jsonify({
      "status": "success",
      "message": f"User {username} status changed to {new_status}.",
  })


@app.route("/api/bot/reset_hwid", methods=["POST"])
def api_bot_reset_hwid():
  data = request.json or {}
  username = data.get("username")
  if not username:
    return jsonify({"status": "error", "message": "Username missing"}), 400

  db(
      "UPDATE tool_users SET hwid=NULL, status='active' WHERE username=?",
      (username,),
  )
  return jsonify({
      "status": "success",
      "message": f"HWID reset successfully for {username}.",
  })

# ==========================================
# DISCORD BOT INTEGRATION API ENDPOINTS (FIXED & KEYS ROUTE ADDED)
# ==========================================




@app.route("/api/bot/keys", methods=["GET"])
def api_bot_get_keys():
  app_token = request.args.get("app_token")
  
  if app_token:
    keys_rows = db(
        "SELECT key_text, status, app_token FROM keys WHERE app_token=?", (app_token,), fetch=True
    )
  else:
    keys_rows = db(
        "SELECT key_text, status, app_token FROM keys", fetch=True
    )
    
  keys_list = []
  for k in keys_rows:
    keys_list.append({
        "key": k[0],
        "status": k[1],
        "app_token": k[2],
    })
  return jsonify({"status": "success", "keys": keys_list})


@app.route("/api/bot/delete_user", methods=["POST"])
def api_bot_delete_user():
  data = request.json or {}
  username = data.get("username")
  if not username:
    return jsonify({"status": "error", "message": "Username missing"}), 400

  db("DELETE FROM tool_users WHERE username=?", (username,))
  return jsonify(
      {"status": "success", "message": f"User {username} deleted successfully."}
  )


# ==========================================
# DISCORD BOT INTEGRATION API ENDPOINTS (FIXED & KEYS ROUTE ADDED)
# ==========================================

@app.route("/api/bot/users", methods=["GET"])
def api_bot_get_users():
  app_token = request.args.get("app_token")
  
  if app_token:
    tool_users = db(
        "SELECT username, status, hwid, app_token FROM tool_users WHERE app_token=?", (app_token,), fetch=True
    )
  else:
    tool_users = db(
        "SELECT username, status, hwid, app_token FROM tool_users", fetch=True
    )
    
  users_list = []
  for u in tool_users:
    users_list.append({
        "username": u[0],
        "status": u[1],
        "hwid": u[2] if u[2] else "Not Bound",
        "app_token": u[3],
    })
  return jsonify({"status": "success", "users": users_list})


@app.route("/api/bot/keys", methods=["GET"])
def api_bot_get_keys():
  app_token = request.args.get("app_token")
  
  if app_token:
    keys_rows = db(
        "SELECT key_text, status, app_token FROM keys WHERE app_token=?", (app_token,), fetch=True
    )
  else:
    keys_rows = db(
        "SELECT key_text, status, app_token FROM keys", fetch=True
    )
    
  keys_list = []
  for k in keys_rows:
    keys_list.append({
        "key": k[0],
        "status": k[1],
        "app_token": k[2],
    })
  return jsonify({"status": "success", "keys": keys_list})


@app.route("/api/bot/delete_user", methods=["POST"])
def api_bot_delete_user():
  data = request.json or {}
  username = data.get("username")
  if not username:
    return jsonify({"status": "error", "message": "Username missing"}), 400

  db("DELETE FROM tool_users WHERE username=?", (username,))
  return jsonify(
      {"status": "success", "message": f"User {username} deleted successfully."}
  )


@app.route("/api/bot/delete_key", methods=["POST"])
def api_bot_delete_key():
  data = request.json or {}
  key_text = data.get("key") or data.get("key_text")
  if not key_text:
    return jsonify({"status": "error", "message": "Key missing"}), 400

  db("DELETE FROM keys WHERE key_text=?", (key_text,))
  return jsonify(
      {"status": "success", "message": f"License key {key_text} deleted successfully."}
  )

@app.route("/api/bot/create_key", methods=["POST"])
def api_bot_create_key():
    data = request.json or {}
    app_token = data.get("app_token")
    if not app_token:
        return jsonify({"status": "error", "message": "App token missing"}), 400
    
    random_key_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    key_text = f"HSL-KEY-{random_key_part}"
    try:
        db(
            "INSERT INTO keys (key_text, app_token, status, created_at) VALUES (?,?,?,?)",
            (key_text, app_token, "unused", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        return jsonify({"status": "success", "message": f"Key Created: {key_text}", "key": key_text}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": "Could not create key."}), 500


@app.route("/logout")
def logout():
  session.clear()
  return redirect("/")


# ==========================================
# SERVER RUNNER
# ==========================================

@app.route("/__temporary_db_backup", methods=["GET", "POST"])
def temporary_db_backup():
    expected_token = os.environ.get("DB_BACKUP_TOKEN", "")

    if len(expected_token) < 32:
        return "Backup route disabled", 404

    if request.method == "GET":
        return """
        <!doctype html>
        <html>
        <head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
        <body style="font-family:Arial;max-width:420px;margin:60px auto;padding:20px">
          <h2>HSL-CORP Database Backup</h2>
          <form method="POST">
            <label>Backup access token</label><br><br>
            <input type="password" name="token" required
                   autocomplete="current-password"
                   style="width:100%;padding:12px;box-sizing:border-box">
            <br><br>
            <button type="submit" style="padding:12px">Download Backup</button>
          </form>
        </body>
        </html>
        """

    supplied_token = request.form.get("token", "")

    if not secrets.compare_digest(supplied_token, expected_token):
        return "Unauthorized", 401

    backup_path = "/data/hsl_backup.db"

    if not os.path.isfile(backup_path):
        return "Backup file not found", 404

    return send_file(
        backup_path,
        as_attachment=True,
        download_name="hsl_backup.db",
        mimetype="application/octet-stream",
    )

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5000, debug=False)