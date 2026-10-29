import os
import asyncio
import sqlite3
import datetime
import requests
import discord
from discord.ext import commands
from discord import app_commands

# =========================================================
# HSL-CORP | APP-FIRST BRUTAL AUTH CONTROL BOT
# =========================================================
RAILWAY_URL = os.getenv(
    "RAILWAY_URL",
    "https://hsl-corp-production-9678.up.railway.app"
).rstrip("/")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
OWNER_ID = int(os.getenv("OWNER_ID", "1517901703263944758"))
DB_PATH = os.getenv("BOT_DB_PATH", "hsl.db")

RED, GREEN, CYAN, YELLOW, PURPLE = (
    0xFF304F, 0x00F5A0, 0x00D9FF, 0xF1C40F, 0xA855F7
)

TICK_EMOJI = "<a:tick:1441097547350282260>"
RED_EMOJI = "<a:red:1528079861283946538>"
RESET_EMOJI = "<a:reset:1441087243996496079>"
PIKACHU_URL = "https://cdn.discordapp.com/emojis/1320787117881823252.webp?size=44&animated=true"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

# =========================================================
# DATABASE ENGINE
# =========================================================
def db(query, params=(), fetch=False):
    with sqlite3.connect(DB_PATH, timeout=15) as con:
        con.execute("PRAGMA busy_timeout = 15000")
        cur = con.cursor()
        cur.execute(query, params)
        result = cur.fetchall() if fetch else None
        con.commit()
        return result

def init_db():
    db("""
        CREATE TABLE IF NOT EXISTS discord_whitelist (
            user_id TEXT NOT NULL,
            command TEXT NOT NULL,
            UNIQUE(user_id, command)
        )
    """)
    db("""
        CREATE TABLE IF NOT EXISTS apps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE
        )
    """)
    db("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

init_db()

# =========================================================
# HELPERS & EMBED BUILDERS
# =========================================================
def make_embed(title, description, color=CYAN, member=None, command_name=None):
    e = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    if member and command_name:
        e.set_author(name=f"{member.display_name} used #{command_name}", icon_url=member.display_avatar.url)
    else:
        e.set_author(name="HSL Auth • Created by JATIN")
        
    e.set_footer(text="HSL Auth • Created by JATIN", icon_url=PIKACHU_URL)
    e.set_thumbnail(url=PIKACHU_URL)
    return e

def clean(value, limit=180):
    return discord.utils.escape_markdown(
        str(value if value is not None else "Unknown").replace("`", "'")
    )[:limit]

def is_allowed(user_id, command):
    if user_id == OWNER_ID:
        return True
    return bool(db(
        "SELECT 1 FROM discord_whitelist WHERE user_id=? AND command=?",
        (str(user_id), command.lower()),
        fetch=True
    ))

async def deny(interaction, command):
    if is_allowed(interaction.user.id, command):
        return False
    message = make_embed(
        "⛔ ACCESS DENIED",
        "```diff\n- You are not authorized to use this command.\n```",
        RED
    )
    if interaction.response.is_done():
        await interaction.followup.send(embed=message, ephemeral=True)
    else:
        await interaction.response.send_message(embed=message, ephemeral=True)
    return True

async def api(method, path, **kwargs):
    kwargs.setdefault("timeout", 15)
    return await asyncio.to_thread(
        requests.request,
        method,
        f"{RAILWAY_URL}{path}",
        **kwargs
    )

def get_apps():
    return db("SELECT name, token FROM apps ORDER BY name COLLATE NOCASE", fetch=True)

def get_log_channel_id():
    rows = db("SELECT value FROM bot_settings WHERE key='log_channel_id'", fetch=True)
    try:
        return int(rows[0][0]) if rows and rows[0][0] else None
    except (TypeError, ValueError):
        return None

async def audit(text, color=CYAN):
    channel_id = get_log_channel_id()
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return
    try:
        await channel.send(embed=make_embed("🧾 HSL-CORP • AUDIT TRAIL", text, color))
    except discord.HTTPException:
        pass

def parse_api_response(response):
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}
    except ValueError:
        return {"message": response.text[:800]}

async def get_app_users(app_token):
    response = await api("GET", "/api/bot/users", params={"app_token": app_token})
    payload = parse_api_response(response)
    if response.status_code != 200:
        raise RuntimeError(f"Users API HTTP {response.status_code}: {payload.get('message', response.text[:300])}")
    users = payload.get("users", [])
    if not isinstance(users, list):
        raise RuntimeError("Invalid response: 'users' must be a list.")
    return users

# =========================================================
# APP SELECTOR + USER ACTIONS
# =========================================================
class AppActionSelect(discord.ui.Select):
    def __init__(self, owner_id, action, apps, target_username=None):
        self.owner_id = owner_id
        self.action = action
        self.target_username = target_username

        try:
            dropdown_emoji = discord.PartialEmoji.from_str(RED_EMOJI)
        except Exception:
            dropdown_emoji = "📦"

        options = [
            discord.SelectOption(
                label=str(name)[:100],
                value=str(token),
                description="Choose this application",
                emoji=dropdown_emoji
            )
            for name, token in apps[:25]
        ]
        super().__init__(placeholder="📦 STEP 1 — SELECT APPLICATION", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and not is_allowed(interaction.user.id, self.action):
            return await interaction.response.send_message("❌ You don't have permission to use this menu.", ephemeral=True)

        app_token = self.values[0]
        app_rows = [row for row in get_apps() if row[1] == app_token]
        app_name = app_rows[0][0] if app_rows else "Selected application"

        if self.action == "delete_app":
            try:
                db("DELETE FROM apps WHERE token=?", (app_token,))
                await interaction.response.edit_message(
                    embed=make_embed("APPLICATION DELETED", f"Application **{clean(app_name)}** has been successfully removed.", RED, interaction.user, "delete_app"),
                    view=None
                )
            except Exception as exc:
                await interaction.response.edit_message(embed=make_embed("🔴 ERROR", clean(exc), RED, interaction.user, "delete_app"), view=None)
            return

        try:
            users = await get_app_users(app_token)
        except Exception as exc:
            return await interaction.response.edit_message(embed=make_embed("🔴 APP USERS API ERROR", f"{clean(exc, 1200)}", RED), view=None)

        if self.action == "list":
            lines = []
            for u in users[:35]:
                username = clean(u.get("username", "Unknown"), 70)
                status = clean(u.get("status", "unknown"), 30)
                hwid = str(u.get("hwid") or "Not bound")
                status_icon = "🟢" if status.lower() == 'active' else "🔴"
                lines.append(f"{status_icon} `{username}` • `{status}` • HWID `{clean(hwid[:12])}`")

            return await interaction.response.edit_message(
                embed=make_embed(f"{clean(app_name)} • USERS ({len(users)})", "\n".join(lines)[:3900] or "No users returned.", CYAN, interaction.user, "list"),
                view=None
            )

        if self.target_username:
            matched_user = next((u for u in users if str(u.get("username", "")).casefold() == self.target_username.casefold()), None)
            if not matched_user:
                return await interaction.response.edit_message(
                    embed=make_embed("🔴 USER NOT FOUND", f"User `{clean(self.target_username)}` not found in **{clean(app_name)}**.", RED, interaction.user, self.action),
                    view=None
                )
            return await interaction.response.edit_message(
                embed=make_embed("CONFIRM ACTION", f"**Application:** `{clean(app_name)}`\n**Action:** `{self.action.upper()}`\n**Username:** `{clean(self.target_username)}`", RED, interaction.user, self.action),
                view=ConfirmActionView(self.owner_id, self.action, app_name, app_token, self.target_username)
            )

        if not users:
            return await interaction.response.edit_message(
                embed=make_embed("NO USERS", f"No accounts returned for **{clean(app_name)}**.", YELLOW, interaction.user, self.action),
                view=None
            )

        await interaction.response.edit_message(
            embed=make_embed(f"{self.action.upper()} • {clean(app_name)}", f"**Application:** `{clean(app_name)}`\n**Users:** `{len(users)}`\n\n👇 **STEP 2 — SELECT USERNAME**", RED if self.action in ("ban", "delete") else CYAN, interaction.user, self.action),
            view=UserSelectView(self.owner_id, self.action, app_name, app_token, users)
        )

class AppActionView(discord.ui.View):
    def __init__(self, owner_id, action, apps, target_username=None):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(AppActionSelect(owner_id, action, apps, target_username))

class UserSelect(discord.ui.Select):
    def __init__(self, owner_id, action, app_name, app_token, users):
        self.owner_id = owner_id
        self.action = action
        self.app_name = app_name
        self.app_token = app_token

        options = []
        for user in users[:25]:
            username = str(user.get("username", ""))
            if username:
                status = str(user.get("status", "unknown"))
                opt_emoji = "🟢" if status.lower() == "active" else "🔴"
                options.append(discord.SelectOption(label=username[:100], value=username[:100], description=f"Status: {status}"[:100], emoji=opt_emoji))

        if not options:
            options = [discord.SelectOption(label="No selectable usernames", value="__none__", description="No usernames found", emoji="⚠️")]

        super().__init__(placeholder="👤 STEP 2 — SELECT USERNAME", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and not is_allowed(interaction.user.id, self.action):
            return await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)

        username = self.values[0]
        if username == "__none__":
            return await interaction.response.send_message("No user selected.", ephemeral=True)

        await interaction.response.edit_message(
            embed=make_embed("CONFIRM ACTION", f"**Application:** `{clean(self.app_name)}`\n**Action:** `{self.action.upper()}`\n**Username:** `{clean(username)}`", RED, interaction.user, self.action),
            view=ConfirmActionView(self.owner_id, self.action, self.app_name, self.app_token, username)
        )

class UserSelectView(discord.ui.View):
    def __init__(self, owner_id, action, app_name, app_token, users):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(UserSelect(owner_id, action, app_name, app_token, users))

class ConfirmActionView(discord.ui.View):
    def __init__(self, owner_id, action, app_name, app_token, username):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.action = action
        self.app_name = app_name
        self.app_token = app_token
        self.username = username

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id and not is_allowed(interaction.user.id, self.action):
            await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="CONFIRM", emoji="⚡", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button):
        action_title = self.action.upper()
        paths = {
            "ban": "/api/bot/toggle_ban",
            "unban": "/api/bot/toggle_ban",
            "delete": "/api/bot/delete_user",
            "resethwid": "/api/bot/reset_hwid"
        }

        await interaction.response.edit_message(
            embed=make_embed(f"PROCESSING {action_title}...", f"Executing `{action_title}` for `{clean(self.username)}`...", CYAN, interaction.user, self.action),
            view=None
        )

        try:
            response = await api("POST", paths[self.action], json={"username": self.username, "app_token": self.app_token})
            payload = parse_api_response(response)
            message = str(payload.get("message", payload.get("error", "Done")))

            if not 200 <= response.status_code < 300:
                return await interaction.edit_original_response(embed=make_embed("🔴 ACTION FAILED", f"```{message[:900]}```", RED, interaction.user, self.action))

            success_embed = make_embed(f"{action_title} SUCCESS", f"Action completed for user `{clean(self.username)}`.", GREEN, interaction.user, self.action)
            await interaction.edit_original_response(embed=success_embed)
            await audit(f"**Action:** `{action_title}`\n**App:** `{clean(self.app_name)}`\n**Username:** `{clean(self.username)}`", GREEN)
        except Exception as exc:
            await interaction.edit_original_response(embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED, interaction.user, self.action))
        self.stop()

    @discord.ui.button(label="CANCEL", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button):
        await interaction.response.edit_message(embed=make_embed("ACTION CANCELLED", "No changes were made.", YELLOW, interaction.user, self.action), view=None)
        self.stop()

# =========================================================
# CREATE USER & KEY SELECTORS
# =========================================================
class CreateAppSelect(discord.ui.Select):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        self.owner_id = owner_id
        self.username = username
        self.password = password
        self.expiry_type = expiry_type
        self.days = days

        options = [discord.SelectOption(label=str(name)[:100], value=str(token), description="Create user in this app") for name, token in apps[:25]]
        super().__init__(placeholder="📦 SELECT APP FOR USER", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and not is_allowed(interaction.user.id, "create"):
            return await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)

        app_token = self.values[0]
        payload = {"username": self.username, "password": self.password, "app_token": app_token, "expiry_type": self.expiry_type}
        if self.expiry_type == "custom":
            payload["days"] = self.days

        await interaction.response.edit_message(embed=make_embed("CREATING USER...", "Please wait...", CYAN, interaction.user, "create"), view=None)

        try:
            response = await api("POST", "/api/create_user", json=payload)
            data = parse_api_response(response)
            message = str(data.get("message", data.get("error", "Done")))

            if response.status_code == 200:
                await interaction.edit_original_response(embed=make_embed("USER CREATED", f"User `{clean(self.username)}` successfully created!", GREEN, interaction.user, "create"))
            else:
                await interaction.edit_original_response(embed=make_embed("🔴 CREATE FAILED", f"```{message[:1000]}```", RED, interaction.user, "create"))
        except Exception as exc:
            await interaction.edit_original_response(embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED, interaction.user, "create"))

class CreateAppView(discord.ui.View):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        super().__init__(timeout=120)
        self.add_item(CreateAppSelect(owner_id, username, password, expiry_type, days, apps))

class CreateKeyAppSelect(discord.ui.Select):
    def __init__(self, owner_id, apps):
        self.owner_id = owner_id
        options = [discord.SelectOption(label=str(name)[:100], value=str(token), description="Generate key for this app") for name, token in apps[:25]]
        super().__init__(placeholder="🔑 SELECT APP FOR LICENSE KEY", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and not is_allowed(interaction.user.id, "createkey"):
            return await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)

        app_token = self.values[0]
        app_rows = [row for row in get_apps() if row[1] == app_token]
        app_name = app_rows[0][0] if app_rows else "Selected App"

        await interaction.response.edit_message(embed=make_embed("GENERATING KEY...", "Please wait...", CYAN, interaction.user, "createkey"), view=None)

        try:
            response = await api("POST", "/api/bot/create_key", json={"app_token": app_token})
            payload = parse_api_response(response)
            key_text = payload.get("key")

            if response.status_code == 200 and key_text:
                await interaction.edit_original_response(embed=make_embed("🔑 LICENSE KEY CREATED", f"**App:** `{clean(app_name)}`\n**Key:** `{clean(key_text)}`", GREEN, interaction.user, "createkey"))
            else:
                await interaction.edit_original_response(embed=make_embed("🔴 FAILED", "Could not generate key.", RED, interaction.user, "createkey"))
        except Exception as exc:
            await interaction.edit_original_response(embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED, interaction.user, "createkey"))

class CreateKeyAppView(discord.ui.View):
    def __init__(self, owner_id, apps):
        super().__init__(timeout=120)
        self.add_item(CreateKeyAppSelect(owner_id, apps))

# =========================================================
# BOT EVENTS & COMMANDS
# =========================================================
@bot.event
async def on_ready():
    print(f"[HSL-CORP] Online as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"[HSL-CORP] Synced {len(synced)} commands")
    except Exception as exc:
        print(f"Sync failed: {exc}")

@bot.tree.command(name="ping", description="🏓 Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_embed("🏓 PONG", f"Latency: `{round(bot.latency * 1000)} ms`", GREEN, interaction.user, "ping"))

@bot.tree.command(name="help", description="⚡ Command panel")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_embed("HSL-CORP PANEL", "Commands: `/create` `/createkey` `/list` `/ban` `/unban` `/deluser` `/resethwid` `/add_app` `/apps` `/delete_app`", RED, interaction.user, "help"))

@bot.tree.command(name="add_app", description="➕ Register an app")
async def add_app(interaction: discord.Interaction, app_name: str, app_token: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Unauthorized", RED), ephemeral=True)
    db("INSERT INTO apps (name, token) VALUES (?, ?) ON CONFLICT(token) DO UPDATE SET name=excluded.name", (app_name.strip(), app_token.strip()))
    await interaction.response.send_message(embed=make_embed("APP REGISTERED", f"**App:** `{clean(app_name)}`", GREEN))

@bot.tree.command(name="apps", description="📦 List apps")
async def apps_command(interaction: discord.Interaction):
    if await deny(interaction, "apps"): return
    apps = get_apps()
    text = "\n".join(f"📦 **{clean(name, 90)}**" for name, token in apps[:25]) or "No apps found."
    await interaction.response.send_message(embed=make_embed(f"REGISTERED APPS ({len(apps)})", text[:3900], CYAN))

@bot.tree.command(name="delete_app", description="🗑️ Delete app")
async def delete_app_cmd(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Unauthorized", RED), ephemeral=True)
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "No apps found.", YELLOW), ephemeral=True)
    await interaction.response.send_message(embed=make_embed("DELETE APP", "Select app to delete:", RED), view=AppActionView(interaction.user.id, "delete_app", apps))

@bot.tree.command(name="create", description="🚀 Create user")
@app_commands.choices(expiry_type=[app_commands.Choice(name="Custom Days", value="custom"), app_commands.Choice(name="Lifetime", value="lifetime")])
async def create(interaction: discord.Interaction, username: str, password: str, expiry_type: app_commands.Choice[str], days: int = 0):
    if await deny(interaction, "create"): return
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "No apps found.", YELLOW), ephemeral=True)
    view = CreateAppView(interaction.user.id, username.strip(), password.strip(), expiry_type.value, days, apps)
    await interaction.response.send_message(embed=make_embed("SELECT APP", f"Select app for user `{clean(username)}`:", CYAN), view=view)

@bot.tree.command(name="createkey", description="🔑 Generate license key")
async def createkey(interaction: discord.Interaction):
    if await deny(interaction, "createkey"): return
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "No apps found.", YELLOW), ephemeral=True)
    view = CreateKeyAppView(interaction.user.id, apps)
    await interaction.response.send_message(embed=make_embed("SELECT APP", "Select app to generate key:", CYAN), view=view)

async def show_app_action(interaction, action, username=None):
    if await deny(interaction, action): return
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "No apps found.", YELLOW), ephemeral=True)
    await interaction.response.send_message(embed=make_embed(f"{action.upper()} • SELECT APP", "Select app:", CYAN), view=AppActionView(interaction.user.id, action, apps, target_username=username))

@bot.tree.command(name="list", description="📜 List users")
async def list_users(interaction: discord.Interaction):
    await show_app_action(interaction, "list")

@bot.tree.command(name="ban", description="🔨 Ban user")
async def ban(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "ban", username)

@bot.tree.command(name="unban", description="🔓 Unban user")
async def unban(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "unban", username)

@bot.tree.command(name="deluser", description="🗑️ Delete user")
async def delete_user(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "delete", username)

@bot.tree.command(name="resethwid", description="🔄 Reset HWID")
async def reset_hwid(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "resethwid", username)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing.")

bot.run(BOT_TOKEN)