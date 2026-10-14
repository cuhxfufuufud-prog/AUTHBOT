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
RAILWAY_URL = os.getenv("RAILWAY_URL", "https://hsl-corp-production-9678.up.railway.app").rstrip("/")
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
OWNER_ID = int(os.getenv("OWNER_ID", "1517901703263944758"))
DB_PATH = os.getenv("BOT_DB_PATH", "hsl.db")

RED, GREEN, CYAN, YELLOW, PURPLE = 0xFF304F, 0x00F5A0, 0x00D9FF, 0xF1C40F, 0xA855F7
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Local bot database: apps and Discord command permissions only.
def db(query, params=(), fetch=False):
    with sqlite3.connect(DB_PATH, timeout=15) as con:
        cur = con.cursor()
        cur.execute(query, params)
        result = cur.fetchall() if fetch else None
        con.commit()
        return result


def init_db():
    db("CREATE TABLE IF NOT EXISTS discord_whitelist (user_id TEXT NOT NULL, command TEXT NOT NULL, UNIQUE(user_id, command))")
    db("CREATE TABLE IF NOT EXISTS apps (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token TEXT NOT NULL UNIQUE)")
    db("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)")


init_db()


def make_embed(title, description, color=CYAN):
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
    e.set_author(name="HSL-CORP • AUTH SECURITY")
    e.set_footer(text="HSL-CORP // SECURE CONTROL INTERFACE")
    if bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)
    return e


def clean(value, limit=180):
    return discord.utils.escape_markdown(str(value if value is not None else "Unknown").replace("`", "'"))[:limit]


def is_allowed(user_id, command):
    if user_id == OWNER_ID:
        return True
    return bool(db("SELECT 1 FROM discord_whitelist WHERE user_id=? AND command=?", (str(user_id), command.lower()), fetch=True))


async def deny(interaction, command):
    if is_allowed(interaction.user.id, command):
        return False
    message = make_embed("⛔ ACCESS DENIED", "```diff\n- You are not authorized to use this command.\n```", RED)
    if interaction.response.is_done():
        await interaction.followup.send(embed=message, ephemeral=True)
    else:
        await interaction.response.send_message(embed=message, ephemeral=True)
    return True


async def api(method, path, **kwargs):
    kwargs.setdefault("timeout", 15)
    return await asyncio.to_thread(requests.request, method, f"{RAILWAY_URL}{path}", **kwargs)


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
    """Requires app.py /api/bot/users to filter by app_token server-side."""
    response = await api("GET", "/api/bot/users", params={"app_token": app_token})
    payload = parse_api_response(response)
    if response.status_code != 200:
        raise RuntimeError(f"Users API HTTP {response.status_code}: {payload.get('message', response.text[:300])}")
    users = payload.get("users", [])
    if not isinstance(users, list):
        raise RuntimeError("Invalid response: 'users' must be a list.")
    return users


# ---------------- APP SELECTOR + APP-SCOPED USER ACTIONS ----------------
class AppActionSelect(discord.ui.Select):
    def __init__(self, owner_id, action, apps):
        self.owner_id = owner_id
        self.action = action
        options = [discord.SelectOption(label=str(name)[:100], value=str(token), description="Choose this application") for name, token in apps[:25]]
        super().__init__(placeholder="📦 STEP 1 — SELECT APPLICATION", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("This panel belongs to another user.", ephemeral=True)
        app_token = self.values[0]
        app_rows = [row for row in get_apps() if row[1] == app_token]
        app_name = app_rows[0][0] if app_rows else "Selected application"
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            users = await get_app_users(app_token)
        except Exception as exc:
            return await interaction.followup.send(embed=make_embed("🔴 APP USERS API ERROR", f"{clean(exc, 1200)}\n\n**Note:** `/api/bot/users` must support `app_token` filtering in `app.py`.", RED), ephemeral=True)
        if self.action == "list":
            lines = []
            for u in users[:35]:
                username = clean(u.get("username", "Unknown"), 70)
                status = clean(u.get("status", "unknown"), 30)
                hwid = str(u.get("hwid") or "Not bound")
                lines.append(f"{'🟢' if status.lower() == 'active' else '🔴'} `{username}` • `{status}` • HWID `{clean(hwid[:12])}`")
            return await interaction.followup.send(embed=make_embed(f"👥 {clean(app_name)} • USERS ({len(users)})", "\n".join(lines)[:3900] or "No users returned for this app.", CYAN), ephemeral=True)
        if not users:
            return await interaction.followup.send(embed=make_embed("📭 NO USERS", f"No accounts returned for **{clean(app_name)}**.", YELLOW), ephemeral=True)
        await interaction.followup.send(embed=make_embed(f"⚡ {self.action.upper()} • {clean(app_name)}", f"**Application:** `{clean(app_name)}`\n**Users:** `{len(users)}`\n\n👇 **STEP 2 — Select a username.**", RED if self.action in ("ban", "delete") else CYAN), view=UserSelectView(self.owner_id, self.action, app_name, app_token, users), ephemeral=True)


class AppActionView(discord.ui.View):
    def __init__(self, owner_id, action, apps):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(AppActionSelect(owner_id, action, apps))


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
                options.append(discord.SelectOption(label=username[:100], value=username[:100], description=f"Status: {status}"[:100], emoji="🟢" if status.lower() == "active" else "🔴"))
        if not options:
            options = [discord.SelectOption(label="No selectable usernames", value="__none__", description="No usernames were returned")]
        super().__init__(placeholder="👤 STEP 2 — SELECT USERNAME", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("This panel belongs to another user.", ephemeral=True)
        username = self.values[0]
        if username == "__none__":
            return await interaction.response.send_message("No user selected.", ephemeral=True)
        await interaction.response.edit_message(embed=make_embed("⚠️ CONFIRM ACTION", f"**Application:** `{clean(self.app_name)}`\n**Action:** `{self.action.upper()}`\n**Username:** `{clean(username)}`\n\nConfirm only if the app and username are correct.", RED), view=ConfirmActionView(self.owner_id, self.action, self.app_name, self.app_token, username))


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
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This panel belongs to another user.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="CONFIRM", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        paths = {"ban": "/api/bot/toggle_ban", "unban": "/api/bot/toggle_ban", "delete": "/api/bot/delete_user", "resethwid": "/api/bot/reset_hwid"}
        try:
            # Avoid accidental toggles: determine status first, scoped to selected app.
            if self.action in ("ban", "unban"):
                users = await get_app_users(self.app_token)
                user = next((u for u in users if str(u.get("username", "")).casefold() == self.username.casefold()), None)
                if not user:
                    return await interaction.followup.send(embed=make_embed("🔴 USER NOT FOUND", "The username was not returned for this application. No action taken.", RED), ephemeral=True)
                status = str(user.get("status", "")).lower()
                banned = status in ("banned", "ban", "disabled")
                if self.action == "ban" and banned:
                    return await interaction.followup.send(embed=make_embed("⚠️ ALREADY BANNED", "No change made.", YELLOW), ephemeral=True)
                if self.action == "unban" and not banned:
                    return await interaction.followup.send(embed=make_embed("⚠️ NOT MARKED BANNED", "No change made because the API status is not a recognized banned state.", YELLOW), ephemeral=True)
            # The app API must validate app_token and scope the username to that app.
            response = await api("POST", paths[self.action], json={"username": self.username, "app_token": self.app_token})
            payload = parse_api_response(response)
            message = str(payload.get("message", payload.get("error", "No message returned.")))
            if not 200 <= response.status_code < 300:
                return await interaction.followup.send(embed=make_embed("🔴 ACTION FAILED", f"HTTP `{response.status_code}`\n```{message[:900]}```", RED), ephemeral=True)
            await interaction.followup.send(embed=make_embed(f"🟢 {self.action.upper()} REQUEST SENT", f"**App:** `{clean(self.app_name)}`\n**User:** `{clean(self.username)}`\n**API:** {clean(message, 600)}", GREEN), ephemeral=True)
            await audit(f"**Action:** `{self.action.upper()}`\n**App:** `{clean(self.app_name)}`\n**Username:** `{clean(self.username)}`\n**By:** {interaction.user.mention}", GREEN)
        except Exception as exc:
            await interaction.followup.send(embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED), ephemeral=True)
        self.stop()

    @discord.ui.button(label="CANCEL", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=make_embed("ACTION CANCELLED", "No changes were requested.", YELLOW), view=None)
        self.stop()


# ---------------- CREATE LICENSE: APP FIRST ----------------
class CreateAppSelect(discord.ui.Select):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        self.owner_id, self.username, self.password = owner_id, username, password
        self.expiry_type, self.days = expiry_type, days
        options = [discord.SelectOption(label=str(n)[:100], value=str(t), description="Create license in this app") for n, t in apps[:25]]
        super().__init__(placeholder="📦 STEP 1 — SELECT APPLICATION", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("This panel belongs to another user.", ephemeral=True)
        app_token = self.values[0]
        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = {"username": self.username, "password": self.password, "app_token": app_token, "expiry_type": self.expiry_type}
        if self.expiry_type == "custom":
            payload["days"] = self.days
            payload["expiry_days"] = self.days
        try:
            response = await api("POST", "/api/create_user", json=payload)
            data = parse_api_response(response)
            message = str(data.get("message", data.get("error", "No message returned.")))
            if response.status_code == 200 and any(x in message.lower() for x in ("created", "success")):
                app_rows = [row for row in get_apps() if row[1] == app_token]
                app_name = app_rows[0][0] if app_rows else "Selected app"
                expiry = "Lifetime" if self.expiry_type == "lifetime" else f"{self.days} day(s)"
                await interaction.edit_original_response(embed=make_embed("🟢 LICENSE CREATED", f"**App:** `{clean(app_name)}`\n**Username:** `{clean(self.username)}`\n**Password:** ||`{clean(self.password)}`||\n**Expiry:** `{expiry}`\n\n```diff\n+ Server accepted creation request\n```", GREEN), view=None)
                await audit(f"**License created**\n**App:** `{clean(app_name)}`\n**Username:** `{clean(self.username)}`\n**Expiry:** `{expiry}`\n**By:** <@{self.owner_id}>", GREEN)
            else:
                await interaction.edit_original_response(embed=make_embed("🔴 CREATE FAILED", f"HTTP `{response.status_code}`\n```{message[:1000]}```", RED), view=None)
        except Exception as exc:
            await interaction.edit_original_response(embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED), view=None)


class CreateAppView(discord.ui.View):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(CreateAppSelect(owner_id, username, password, expiry_type, days, apps))


# ---------------- BOT EVENTS ----------------
@bot.event
async def on_ready():
    print(f"[HSL-CORP] Online as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"[HSL-CORP] Synced {len(synced)} slash commands")
    except Exception as exc:
        print(f"[HSL-CORP] Command sync failed: {exc}")


@bot.tree.command(name="ping", description="Check HSL-CORP bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_embed("🏓 SYSTEM ONLINE", f"Latency: `{round(bot.latency * 1000)} ms`\n```diff\n+ HSL-CORP BOT OPERATIONAL\n```", GREEN), ephemeral=True)


@bot.tree.command(name="help", description="HSL-CORP command panel")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=make_embed("⚡ HSL-CORP • CONTROL PANEL", "**APP-FIRST USER ACTIONS**\n`/create` `/list` `/ban` `/unban` `/delete` `/resethwid`\n\n**APP MANAGEMENT**\n`/add_app` `/apps`\n\n**ACCESS & AUDIT**\n`/whitelist_add` `/whitelist_remove` `/whitelist_list`\n`/set_log_channel` `/notify_login` `/update_gmail`\n\n```ansi\n\x1b[1;31mSELECT APP → SELECT USER → CONFIRM\x1b[0m\n```", RED), ephemeral=True)


@bot.tree.command(name="add_app", description="Register an app for the app dropdown")
async def add_app(interaction: discord.Interaction, app_name: str, app_token: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Only the bot owner can register apps.", RED), ephemeral=True)
    app_name, app_token = app_name.strip(), app_token.strip()
    if not app_name or len(app_name) > 100 or not app_token or len(app_token) > 250:
        return await interaction.response.send_message(embed=make_embed("⚠️ INVALID INPUT", "App name/token is empty or too long.", YELLOW), ephemeral=True)
    try:
        db("INSERT INTO apps (name, token) VALUES (?, ?) ON CONFLICT(token) DO UPDATE SET name=excluded.name", (app_name, app_token))
    except sqlite3.Error as exc:
        return await interaction.response.send_message(embed=make_embed("🔴 DATABASE ERROR", clean(exc), RED), ephemeral=True)
    await interaction.response.send_message(embed=make_embed("🟢 APP REGISTERED", f"**App:** `{clean(app_name)}`\n**Token:** ||`{clean(app_token, 100)}`||", GREEN), ephemeral=True)


@bot.tree.command(name="apps", description="List apps registered in this bot")
async def apps_command(interaction: discord.Interaction):
    if await deny(interaction, "apps"):
        return
    apps = get_apps()
    text = "\n".join(f"📦 **{clean(n, 90)}** — token ||`{clean(t, 70)}`||" for n, t in apps[:25]) or "No apps registered. Use `/add_app`."
    await interaction.response.send_message(embed=make_embed(f"📦 REGISTERED APPS ({len(apps)})", text[:3900], CYAN), ephemeral=True)


@bot.tree.command(name="create", description="Create a license after selecting an app")
@app_commands.choices(expiry_type=[app_commands.Choice(name="Custom Days", value="custom"), app_commands.Choice(name="Lifetime", value="lifetime")])
async def create(interaction: discord.Interaction, username: str, password: str, expiry_type: app_commands.Choice[str], days: int = 0):
    if await deny(interaction, "create"):
        return
    username, password = username.strip(), password.strip()
    if not username or len(username) > 80 or not password or len(password) > 256:
        return await interaction.response.send_message(embed=make_embed("⚠️ INVALID INPUT", "Username must be 1–80 characters; password 1–256 characters.", YELLOW), ephemeral=True)
    if expiry_type.value == "custom" and not 1 <= days <= 36500:
        return await interaction.response.send_message(embed=make_embed("⚠️ INVALID EXPIRY", "For custom expiry, `days` must be 1–36500.", YELLOW), ephemeral=True)
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "Register an app first with `/add_app`.", YELLOW), ephemeral=True)
    view = CreateAppView(interaction.user.id, username, password, expiry_type.value, days, apps)
    expiry = "Lifetime" if expiry_type.value == "lifetime" else f"{days} day(s)"
    await interaction.response.send_message(embed=make_embed("⚡ LICENSE PROVISIONING", f"**Username:** `{clean(username)}`\n**Password:** ||`{clean(password)}`||\n**Expiry:** `{expiry}`\n\n👇 **STEP 1 — SELECT APPLICATION**", CYAN), view=view, ephemeral=True)


async def show_app_action(interaction, action):
    if await deny(interaction, action):
        return
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "Register an app first with `/add_app`.", YELLOW), ephemeral=True)
    await interaction.response.send_message(embed=make_embed(f"⚡ HSL-CORP • {action.upper()}", "👇 **STEP 1 — SELECT APPLICATION**\n\nAfter selecting an app, choose a username and confirm.", RED if action in ("ban", "delete") else CYAN), view=AppActionView(interaction.user.id, action, apps), ephemeral=True)


@bot.tree.command(name="list", description="List users in a selected application")
async def list_users(interaction: discord.Interaction):
    await show_app_action(interaction, "list")


@bot.tree.command(name="ban", description="Select app then user to ban")
async def ban(interaction: discord.Interaction):
    await show_app_action(interaction, "ban")


@bot.tree.command(name="unban", description="Select app then user to unban")
async def unban(interaction: discord.Interaction):
    await show_app_action(interaction, "unban")


@bot.tree.command(name="delete", description="Select app then user to delete")
async def delete_user(interaction: discord.Interaction):
    await show_app_action(interaction, "delete")


@bot.tree.command(name="resethwid", description="Select app then user to reset HWID")
async def reset_hwid(interaction: discord.Interaction):
    await show_app_action(interaction, "resethwid")


@bot.tree.command(name="set_log_channel", description="Set HSL-CORP audit channel")
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if await deny(interaction, "set_log_channel"):
        return
    db("INSERT INTO bot_settings (key, value) VALUES ('log_channel_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(channel.id),))
    await interaction.response.send_message(embed=make_embed("🟢 AUDIT CHANNEL SET", f"Log channel: {channel.mention}", GREEN), ephemeral=True)


@bot.tree.command(name="whitelist_add", description="Grant access to one command")
async def whitelist_add(interaction: discord.Interaction, member: discord.Member, command_name: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Only the owner can edit the whitelist.", RED), ephemeral=True)
    command_name = command_name.strip().lower().lstrip("/")
    db("INSERT OR IGNORE INTO discord_whitelist (user_id, command) VALUES (?, ?)", (str(member.id), command_name))
    await interaction.response.send_message(embed=make_embed("🟢 WHITELIST UPDATED", f"**Member:** {member.mention}\n**Command:** `{clean(command_name)}`", GREEN), ephemeral=True)


@bot.tree.command(name="whitelist_remove", description="Remove access to one command")
async def whitelist_remove(interaction: discord.Interaction, member: discord.Member, command_name: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Only the owner can edit the whitelist.", RED), ephemeral=True)
    command_name = command_name.strip().lower().lstrip("/")
    db("DELETE FROM discord_whitelist WHERE user_id=? AND command=?", (str(member.id), command_name))
    await interaction.response.send_message(embed=make_embed("🗑️ WHITELIST ENTRY REMOVED", f"**Member:** {member.mention}\n**Command:** `{clean(command_name)}`", YELLOW), ephemeral=True)


@bot.tree.command(name="whitelist_list", description="Show whitelist entries")
async def whitelist_list(interaction: discord.Interaction):
    if await deny(interaction, "whitelist_list"):
        return
    rows = db("SELECT user_id, command FROM discord_whitelist ORDER BY command", fetch=True)
    description = "\n".join(f"• <@{uid}> — `{clean(cmd, 80)}`" for uid, cmd in rows[:60]) or "Whitelist is empty."
    await interaction.response.send_message(embed=make_embed("🛡️ ACCESS CONTROL LIST", description[:3900], PURPLE), ephemeral=True)


@bot.tree.command(name="update_gmail", description="Update a license account Gmail")
async def update_gmail(interaction: discord.Interaction, username: str, email: str):
    if await deny(interaction, "update_gmail"):
        return
    username, email = username.strip(), email.strip()
    if not username or len(username) > 80 or "@" not in email or len(email) > 254:
        return await interaction.response.send_message(embed=make_embed("⚠️ INVALID INPUT", "Enter a valid username and email.", YELLOW), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        response = await api("POST", "/api/bot/update_gmail", json={"username": username, "gmail": email})
        data = parse_api_response(response)
        message = str(data.get("message", data.get("error", "No message returned.")))
        if response.status_code == 200:
            await interaction.followup.send(embed=make_embed("📧 GMAIL UPDATE RESPONSE", f"**Username:** `{clean(username)}`\n**Email:** `{clean(email)}`\n**API:** {clean(message, 600)}", GREEN), ephemeral=True)
            await audit(f"**Gmail update:** `{clean(username)}`\n**By:** {interaction.user.mention}", GREEN)
        else:
            await interaction.followup.send(embed=make_embed("🔴 GMAIL UPDATE FAILED", f"HTTP `{response.status_code}`\n```{message[:800]}```", RED), ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(embed=make_embed("🔴 API ERROR", clean(exc, 900), RED), ephemeral=True)


@bot.tree.command(name="notify_login", description="Send a manual login audit notification")
@app_commands.choices(status=[app_commands.Choice(name="Success", value="SUCCESS"), app_commands.Choice(name="Failed", value="FAILED")])
async def notify_login(interaction: discord.Interaction, logged_username: str, status: app_commands.Choice[str], ip_address: str = "Hidden/Proxy", hwid: str = "Not bound"):
    if await deny(interaction, "notify_login"):
        return
    channel_id = get_log_channel_id()
    channel = bot.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            pass
    if channel is None:
        return await interaction.response.send_message(embed=make_embed("⚠️ LOG CHANNEL NOT SET", "Use `/set_log_channel` first.", YELLOW), ephemeral=True)
    ok = status.value == "SUCCESS"
    now = datetime.datetime.now(datetime.timezone.utc)
    e = make_embed("🛡️ HSL-CORP • LOGIN AUDIT", "Login audit notification", GREEN if ok else RED)
    e.add_field(name="Username", value=f"`{clean(logged_username)}`", inline=True)
    e.add_field(name="Status", value="🟢 SUCCESS" if ok else "🔴 FAILED", inline=True)
    e.add_field(name="Timestamp", value=f"<t:{int(now.timestamp())}:F>", inline=False)
    e.add_field(name="IP", value=f"`{clean(ip_address, 100)}`", inline=True)
    e.add_field(name="HWID", value=f"`{clean(hwid, 180)}`", inline=False)
    await channel.send(content=f"🚨 Login audit • <@{OWNER_ID}>", embed=e, allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=OWNER_ID)]))
    await interaction.response.send_message(embed=make_embed("🟢 AUDIT DISPATCHED", f"Sent to {channel.mention}.", GREEN), ephemeral=True)


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it to your Railway Variables.")

bot.run(BOT_TOKEN)
