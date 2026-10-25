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

# Custom Animated Emojis & Stickers Links Provided by You
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

    if bot.user:
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
        await interaction.response.send_message(
            embed=message,
            ephemeral=True
        )

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
    return db(
        "SELECT name, token FROM apps ORDER BY name COLLATE NOCASE",
        fetch=True
    )


def get_log_channel_id():
    rows = db(
        "SELECT value FROM bot_settings WHERE key='log_channel_id'",
        fetch=True
    )

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
        await channel.send(
            embed=make_embed("🧾 HSL-CORP • AUDIT TRAIL", text, color)
        )
    except discord.HTTPException:
        pass


def parse_api_response(response):
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}
    except ValueError:
        return {"message": response.text[:800]}


async def get_app_users(app_token):
    response = await api(
        "GET",
        "/api/bot/users",
        params={"app_token": app_token}
    )

    payload = parse_api_response(response)

    if response.status_code != 200:
        raise RuntimeError(
            f"Users API HTTP {response.status_code}: "
            f"{payload.get('message', response.text[:300])}"
        )

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

        options = [
            discord.SelectOption(
                label=str(name)[:100],
                value=str(token),
                description="Choose this application",
                emoji="📦"
            )
            for name, token in apps[:25]
        ]

        super().__init__(
            placeholder="📦 STEP 1 — SELECT APPLICATION",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "❌ You don't have permission to use this menu.",
                ephemeral=True
            )

        app_token = self.values[0]
        app_rows = [row for row in get_apps() if row[1] == app_token]
        app_name = app_rows[0][0] if app_rows else "Selected application"

        if self.action == "delete_app":
            try:
                db("DELETE FROM apps WHERE token=?", (app_token,))
                await interaction.response.edit_message(
                    embed=make_embed(
                        f"{RED_EMOJI} APPLICATION DELETED",
                        f"{TICK_EMOJI} Application **{clean(app_name)}** has been successfully removed from bot registry.",
                        RED,
                        interaction.user,
                        "delete_app"
                    ),
                    view=None
                )
            except Exception as exc:
                await interaction.response.edit_message(
                    embed=make_embed("🔴 ERROR", clean(exc), RED, interaction.user, "delete_app"),
                    view=None
                )
            return

        try:
            users = await get_app_users(app_token)
        except Exception as exc:
            return await interaction.response.edit_message(
                embed=make_embed(
                    "🔴 APP USERS API ERROR",
                    f"{clean(exc, 1200)}",
                    RED
                ),
                view=None
            )

        if self.action == "list":
            lines = []
            for u in users[:35]:
                username = clean(u.get("username", "Unknown"), 70)
                status = clean(u.get("status", "unknown"), 30)
                hwid = str(u.get("hwid") or "Not bound")

                lines.append(
                    f"{TICK_EMOJI if status.lower() == 'active' else RED_EMOJI} "
                    f"`{username}` • `{status}` • "
                    f"HWID `{clean(hwid[:12])}`"
                )

            return await interaction.response.edit_message(
                embed=make_embed(
                    f"{RED_EMOJI} {clean(app_name)} • USERS ({len(users)})",
                    "\n".join(lines)[:3900] or "No users returned for this app.",
                    CYAN,
                    interaction.user,
                    "list"
                ),
                view=None
            )

        if self.target_username:
            matched_user = next(
                (u for u in users if str(u.get("username", "")).casefold() == self.target_username.casefold()),
                None
            )
            if not matched_user:
                return await interaction.response.edit_message(
                    embed=make_embed(
                        "🔴 USER NOT FOUND",
                        f"User `{clean(self.target_username)}` not found in **{clean(app_name)}**.",
                        RED,
                        interaction.user,
                        self.action
                    ),
                    view=None
                )
            
            return await interaction.response.edit_message(
                embed=make_embed(
                    f"{RED_EMOJI} CONFIRM ACTION",
                    f"**Application:** `{clean(app_name)}`\n"
                    f"**Action:** `{self.action.upper()}`\n"
                    f"**Username:** `{clean(self.target_username)}`\n\n"
                    "Confirm only if the app and username are correct.",
                    RED,
                    interaction.user,
                    self.action
                ),
                view=ConfirmActionView(
                    self.owner_id,
                    self.action,
                    app_name,
                    app_token,
                    self.target_username
                )
            )

        if not users:
            return await interaction.response.edit_message(
                embed=make_embed(
                    "📭 NO USERS",
                    f"No accounts returned for **{clean(app_name)}**.",
                    YELLOW,
                    interaction.user,
                    self.action
                ),
                view=None
            )

        await interaction.response.edit_message(
            embed=make_embed(
                f"{RED_EMOJI} {self.action.upper()} • {clean(app_name)}",
                f"**Application:** `{clean(app_name)}`\n"
                f"**Users:** `{len(users)}`\n\n"
                "👇 **STEP 2 — Select a username.**",
                RED if self.action in ("ban", "delete") else CYAN,
                interaction.user,
                self.action
            ),
            view=UserSelectView(
                self.owner_id,
                self.action,
                app_name,
                app_token,
                users
            )
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
                options.append(
                    discord.SelectOption(
                        label=username[:100],
                        value=username[:100],
                        description=f"Status: {status}"[:100],
                        emoji="🟢" if status.lower() == "active" else "🔴"
                    )
                )

        if not options:
            options = [
                discord.SelectOption(
                    label="No selectable usernames",
                    value="__none__",
                    description="No usernames were returned",
                    emoji="⚠️"
                )
            ]

        super().__init__(
            placeholder="👤 STEP 2 — SELECT USERNAME",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                "❌ You don't have permission to use this menu.",
                ephemeral=True
            )

        username = self.values[0]
        if username == "__none__":
            return await interaction.response.send_message("No user selected.", ephemeral=True)

        await interaction.response.edit_message(
            embed=make_embed(
                f"{RED_EMOJI} CONFIRM ACTION",
                f"**Application:** `{clean(self.app_name)}`\n"
                f"**Action:** `{self.action.upper()}`\n"
                f"**Username:** `{clean(username)}`\n\n"
                "Confirm only if the app and username are correct.",
                RED,
                interaction.user,
                self.action
            ),
            view=ConfirmActionView(
                self.owner_id,
                self.action,
                self.app_name,
                self.app_token,
                username
            )
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
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ You don't have permission.", ephemeral=True)
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

        stages = [
            ("`[ █░░░░░░░░░ ] 10% • Initializing Secure Handshake`", 0.4),
            ("`[ ████░░░░░░ ] 40% • Dispatching API Payload`", 0.4),
            ("`[ ███████░░░ ] 70% • Processing Database State`", 0.4),
            ("`[ ██████████ ] 100% • Finalizing Protocol`", 0.3)
        ]

        await interaction.response.edit_message(
            embed=make_embed(
                f"{RED_EMOJI} PROCESSING {action_title}...",
                f"```ansi\n\x1b[1;36m🔄 Executing {action_title} Protocol...\x1b[0m\n```\n"
                f"» **Target User:** `{clean(self.username)}`\n» **App:** `{clean(self.app_name)}`\n\n{stages[0][0]}",
                CYAN,
                interaction.user,
                self.action
            ),
            view=None
        )
        await asyncio.sleep(stages[0][1])

        for bar_text, delay in stages[1:]:
            await interaction.edit_original_response(
                embed=make_embed(
                    f"{RED_EMOJI} PROCESSING {action_title}...",
                    f"```ansi\n\x1b[1;36m🔄 Executing {action_title} Protocol...\x1b[0m\n```\n"
                    f"» **Target User:** `{clean(self.username)}`\n» **App:** `{clean(self.app_name)}`\n\n{bar_text}",
                    CYAN,
                    interaction.user,
                    self.action
                )
            )
            await asyncio.sleep(delay)

        try:
            response = await api(
                "POST",
                paths[self.action],
                json={"username": self.username, "app_token": self.app_token}
            )
            payload = parse_api_response(response)
            message = str(payload.get("message", payload.get("error", "No message returned.")))

            if not 200 <= response.status_code < 300:
                return await interaction.edit_original_response(
                    embed=make_embed("🔴 ACTION FAILED", f"HTTP `{response.status_code}`\n```{message[:900]}```", RED, interaction.user, self.action)
                )

            if self.action == "delete":
                public_desc = f"{TICK_EMOJI} Key / User `{clean(self.username)}` permanently deleted from vault."
                public_title = f"{RED_EMOJI} LICENSE KEY DELETED"
            elif self.action == "resethwid":
                public_desc = f"{TICK_EMOJI} HWID for user `{clean(self.username)}` has been successfully reset."
                public_title = f"{RED_EMOJI} HWID RESET SUCCESSFUL"
            elif self.action in ("ban", "unban"):
                public_desc = f"{TICK_EMOJI} User `{clean(self.username)}` status updated: `{self.action.upper()}`."
                public_title = f"{RED_EMOJI} USER {self.action.upper()}"
            else:
                public_desc = f"{TICK_EMOJI} Action `{self.action.upper()}` executed for `{clean(self.username)}`."
                public_title = f"{RED_EMOJI} {self.action.upper()} SUCCESS"

            success_embed = make_embed(
                public_title,
                public_desc,
                RED if self.action in ("ban", "delete") else GREEN,
                interaction.user,
                self.action
            )
            await interaction.edit_original_response(embed=success_embed)
            await audit(f"**Action:** `{self.action.upper()}`\n**App:** `{clean(self.app_name)}`\n**Username:** `{clean(self.username)}`", GREEN)

        except Exception as exc:
            await interaction.edit_original_response(
                embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED, interaction.user, self.action)
            )
        self.stop()

    @discord.ui.button(label="CANCEL", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button):
        await interaction.response.edit_message(
            embed=make_embed("ACTION CANCELLED", "No changes were requested.", YELLOW, interaction.user, self.action),
            view=None
        )
        self.stop()


# =========================================================
# CREATE LICENSE APP SELECTOR
# =========================================================
class CreateAppSelect(discord.ui.Select):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        self.owner_id = owner_id
        self.username = username
        self.password = password
        self.expiry_type = expiry_type
        self.days = days

        options = [
            discord.SelectOption(
                label=str(name)[:100],
                value=str(token),
                description="Create license in this app",
                emoji="🚀"
            )
            for name, token in apps[:25]
        ]
        super().__init__(placeholder="📦 STEP 1 — SELECT APPLICATION", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)

        app_token = self.values[0]
        create_stages = [
            ("`[ █░░░░░░░░░ ] 10% • Initializing Secure Handshake`", 0.4),
            ("`[ ████░░░░░░ ] 40% • Hashing Passwords & Preparing HWID`", 0.4),
            ("`[ ███████░░░ ] 70% • Registering Key in Vault`", 0.4),
            ("`[ ██████████ ] 100% • Provisioning Complete`", 0.3)
        ]

        await interaction.response.edit_message(
            embed=make_embed(
                f"{RED_EMOJI} PROVISIONING CLIENT ACCOUNT..",
                f"```ansi\n\x1b[1;36m🔄 Creating Zero-Leak Credentials...\x1b[0m\n```\n"
                f"» **Username:** `{clean(self.username)}`\n» **Duration:** `{self.days if self.expiry_type == 'custom' else 'Lifetime'} Days`\n\n{create_stages[0][0]}",
                CYAN,
                interaction.user,
                "adduser"
            ),
            view=None
        )
        await asyncio.sleep(create_stages[0][1])

        for bar_text, delay in create_stages[1:]:
            await interaction.edit_original_response(
                embed=make_embed(
                    f"{RED_EMOJI} PROVISIONING CLIENT ACCOUNT..",
                    f"```ansi\n\x1b[1;36m🔄 Creating Zero-Leak Credentials...\x1b[0m\n```\n"
                    f"» **Username:** `{clean(self.username)}`\n» **Duration:** `{self.days if self.expiry_type == 'custom' else 'Lifetime'} Days`\n\n{bar_text}",
                    CYAN,
                    interaction.user,
                    "adduser"
                )
            )
            await asyncio.sleep(delay)

        payload = {"username": self.username, "password": self.password, "app_token": app_token, "expiry_type": self.expiry_type}
        if self.expiry_type == "custom":
            payload["days"] = self.days
            payload["expiry_days"] = self.days

        try:
            response = await api("POST", "/api/create_user", json=payload)
            data = parse_api_response(response)
            message = str(data.get("message", data.get("error", "No message returned.")))

            if response.status_code == 200 and any(w in message.lower() for w in ("created", "success")):
                app_rows = [row for row in get_apps() if row[1] == app_token]
                app_name = app_rows[0][0] if app_rows else "Selected application"
                expiry = "Lifetime" if self.expiry_type == "lifetime" else f"{self.days} day(s)"
                generated_key = data.get("key") or data.get("license_key") or data.get("generated_key")

                public_description = (
                    f"🟢 **CLIENT ACCOUNT CREATED**\n"
                    f"{TICK_EMOJI} **User** `{clean(self.username)}` **Created for HSL AUTH**\n\n"
                    f"» **Password:** `{clean(self.password)}`\n"
                    f"» **Subscription:** `default`\n"
                    f"» **Expires:** `{datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=self.days if self.expiry_type == 'custom' else 365)} UTC`\n"
                    f"» **HWID Binding:** `Locks on 1st Login` ⚡"
                )
                if generated_key:
                    public_description += f"\n» **Generated Key:** `{clean(generated_key, 150)}`"

                public_embed = make_embed(f"{RED_EMOJI} CLIENT ACCOUNT CREATED", public_description, RED, interaction.user, "adduser")
                await interaction.edit_original_response(embed=public_embed)

                private_embed = make_embed(
                    "🔐 PRIVATE LOGIN DETAILS",
                    f"**Application:** `{clean(app_name)}`\n**Username:** `{clean(self.username)}`\n**Password:** ||`{clean(self.password)}`||\n**Expiry:** `{expiry}`",
                    GREEN,
                    interaction.user,
                    "adduser"
                )
                await interaction.followup.send(embed=private_embed, ephemeral=True)
                await audit(f"**License created**\n**App:** `{clean(app_name)}`\n**Username:** `{clean(self.username)}`", GREEN)
            else:
                await interaction.edit_original_response(
                    embed=make_embed("🔴 CREATE FAILED", f"HTTP `{response.status_code}`\n```{message[:1000]}```", RED, interaction.user, "adduser")
                )
        except Exception as exc:
            await interaction.edit_original_response(embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED, interaction.user, "adduser"))


class CreateAppView(discord.ui.View):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(CreateAppSelect(owner_id, username, password, expiry_type, days, apps))


# =========================================================
# BOT EVENTS & COMMAND REGISTRATION
# =========================================================
@bot.event
async def on_ready():
    print(f"[HSL-CORP] Online as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"[HSL-CORP] Synced {len(synced)} slash commands")
    except Exception as exc:
        print(f"[HSL-CORP] Command sync failed: {exc}")


@bot.tree.command(name="ping", description="🏓 Check HSL-CORP bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed("🏓 SYSTEM ONLINE", f"Latency: `{round(bot.latency * 1000)} ms`\n```diff\n+ HSL-CORP BOT OPERATIONAL\n```", GREEN, interaction.user, "ping")
    )


@bot.tree.command(name="help", description="⚡ HSL-CORP command panel")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed(
            f"{RED_EMOJI} HSL-CORP • CONTROL PANEL",
            "**USER ACTIONS**\n`/create` `/list` `/ban` `/unban` `/deluser` `/resethwid`\n\n**APP MANAGEMENT**\n`/add_app` `/apps` `/delete_app`",
            RED,
            interaction.user,
            "help"
        )
    )


@bot.tree.command(name="add_app", description="➕ Register an app for the app dropdown")
async def add_app(interaction: discord.Interaction, app_name: str, app_token: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Only owner can add apps.", RED, interaction.user, "add_app"), ephemeral=True)
    
    db("INSERT INTO apps (name, token) VALUES (?, ?) ON CONFLICT(token) DO UPDATE SET name=excluded.name", (app_name.strip(), app_token.strip()))
    await interaction.response.send_message(embed=make_embed(f"{RED_EMOJI} APP REGISTERED", f"**App:** `{clean(app_name)}`", GREEN, interaction.user, "add_app"))


@bot.tree.command(name="apps", description="📦 List apps registered in this bot")
async def apps_command(interaction: discord.Interaction):
    if await deny(interaction, "apps"): return
    apps = get_apps()
    text = "\n".join(f"📦 **{clean(name, 90)}**" for name, token in apps[:25]) or "No apps registered."
    await interaction.response.send_message(embed=make_embed(f"📦 REGISTERED APPS ({len(apps)})", text[:3900], CYAN, interaction.user, "apps"))


@bot.tree.command(name="delete_app", description="🗑️ Delete a registered application from bot")
async def delete_app_cmd(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=make_embed("⛔ OWNER ONLY", "Only owner can delete apps.", RED, interaction.user, "delete_app"), ephemeral=True)
    
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "No registered apps found.", YELLOW, interaction.user, "delete_app"), ephemeral=True)

    await interaction.response.send_message(
        embed=make_embed(f"{RED_EMOJI} DELETE APPLICATION", "👇 **Select the application you want to delete:**", RED, interaction.user, "delete_app"),
        view=AppActionView(interaction.user.id, "delete_app", apps)
    )


@bot.tree.command(name="create", description="🚀 Create a license after selecting an app")
@app_commands.choices(expiry_type=[app_commands.Choice(name="Custom Days", value="custom"), app_commands.Choice(name="Lifetime", value="lifetime")])
async def create(interaction: discord.Interaction, username: str, password: str, expiry_type: app_commands.Choice[str], days: int = 0):
    if await deny(interaction, "create"): return
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "Register an app first.", YELLOW, interaction.user, "adduser"), ephemeral=True)
    
    view = CreateAppView(interaction.user.id, username.strip(), password.strip(), expiry_type.value, days, apps)
    await interaction.response.send_message(
        embed=make_embed(f"{RED_EMOJI} HSL Auth Provisioning System", f"**Username:** `{clean(username)}`\n*Select target Application below:*", CYAN, interaction.user, "adduser"),
        view=view
    )


async def show_app_action(interaction, action, username=None):
    if await deny(interaction, action): return
    apps = get_apps()
    if not apps:
        return await interaction.response.send_message(embed=make_embed("📦 NO APPS", "No apps found.", YELLOW, interaction.user, action), ephemeral=True)

    # Use RESET_EMOJI for resethwid and RED_EMOJI for others like ban/delete/list
    header_emoji = RESET_EMOJI if action == "resethwid" else RED_EMOJI

    await interaction.response.send_message(
        embed=make_embed(
            f"{header_emoji} {action.upper()} • SELECT APPLICATION",
            f"{f'{RESET_EMOJI} **Target User:** `{clean(username)}`\n' if username else ''}{RESET_EMOJI} Please choose which application belongs to:\n\n👇 **STEP 1 — SELECT APPLICATION**",
            RED if action in ("ban", "delete", "deluser") else CYAN,
            interaction.user,
            action
        ),
        view=AppActionView(interaction.user.id, action, apps, target_username=username)
    )


@bot.tree.command(name="list", description="📜 List users in a selected application")
async def list_users(interaction: discord.Interaction):
    await show_app_action(interaction, "list")


@bot.tree.command(name="ban", description="🔨 Select app then user to ban")
async def ban(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "ban", username)


@bot.tree.command(name="unban", description="🔓 Select app then user to unban")
async def unban(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "unban", username)


@bot.tree.command(name="deluser", description="🗑️ Permanently delete a client user account")
async def delete_user(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "delete", username)


@bot.tree.command(name="resethwid", description="🔄 Reset HWID for a user")
async def reset_hwid(interaction: discord.Interaction, username: str = None):
    await show_app_action(interaction, "resethwid", username)


@bot.tree.command(name="set_log_channel", description="📋 Set HSL-CORP audit channel")
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if await deny(interaction, "set_log_channel"): return
    db("INSERT INTO bot_settings (key, value) VALUES ('log_channel_id', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(channel.id),))
    await interaction.response.send_message(embed=make_embed(f"{TICK_EMOJI} AUDIT CHANNEL SET", f"Log channel: {channel.mention}", GREEN, interaction.user, "set_log_channel"))


@bot.tree.command(name="update_gmail", description="📧 Update a license account Gmail")
async def update_gmail(interaction: discord.Interaction, username: str, email: str):
    if await deny(interaction, "update_gmail"): return
    await interaction.response.defer(thinking=True)
    try:
        response = await api("POST", "/api/bot/update_gmail", json={"username": username.strip(), "gmail": email.strip()})
        data = parse_api_response(response)
        await interaction.followup.send(embed=make_embed(f"{TICK_EMOJI} GMAIL UPDATE", f"Response: {clean(data.get('message', 'Done'), 600)}", GREEN, interaction.user, "update_gmail"))
    except Exception as exc:
        await interaction.followup.send(embed=make_embed("🔴 API ERROR", clean(exc, 900), RED, interaction.user, "update_gmail"))


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it to your Railway Variables.")

bot.run(BOT_TOKEN)