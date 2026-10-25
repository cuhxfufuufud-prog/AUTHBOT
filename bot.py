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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# =========================================================
# DATABASE
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
# HELPERS
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
        
    e.set_footer(text="HSL Auth • Created by JATIN")

    if bot.user:
        e.set_thumbnail(url=bot.user.display_avatar.url)

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
# APP SELECTOR + APP-SCOPED USER ACTIONS
# =========================================================
class AppActionSelect(discord.ui.Select):
    def __init__(self, owner_id, action, apps):
        self.owner_id = owner_id
        self.action = action

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

        try:
            users = await get_app_users(app_token)
        except Exception as exc:
            return await interaction.response.edit_message(
                embed=make_embed(
                    "🔴 APP USERS API ERROR",
                    f"{clean(exc, 1200)}\n\n"
                    "**Note:** `/api/bot/users` must support "
                    "`app_token` filtering in `app.py`.",
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
                    f"{'🟢' if status.lower() == 'active' else '🔴'} "
                    f"`{username}` • `{status}` • "
                    f"HWID `{clean(hwid[:12])}`"
                )

            return await interaction.response.edit_message(
                embed=make_embed(
                    f"👥 {clean(app_name)} • USERS ({len(users)})",
                    "\n".join(lines)[:3900]
                    or "No users returned for this app.",
                    CYAN,
                    interaction.user,
                    "list"
                ),
                view=None
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

        # Update the original Step 1 message into Step 2 menu directly! (Purana Select Menu gayab)
        await interaction.response.edit_message(
            embed=make_embed(
                f"⚡ {self.action.upper()} • {clean(app_name)}",
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
            return await interaction.response.send_message(
                "No user selected.",
                ephemeral=True
            )

        await interaction.response.edit_message(
            embed=make_embed(
                "⚠️ CONFIRM ACTION",
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
        self.add_item(
            UserSelect(owner_id, action, app_name, app_token, users)
        )


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
            await interaction.response.send_message(
                "❌ You don't have permission to use this menu.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="CONFIRM",
        emoji="⚡",
        style=discord.ButtonStyle.danger
    )
    async def confirm(self, interaction: discord.Interaction, button):
        action_title = self.action.upper()
        
        paths = {
            "ban": "/api/bot/toggle_ban",
            "unban": "/api/bot/toggle_ban",
            "delete": "/api/bot/delete_user",
            "resethwid": "/api/bot/reset_hwid"
        }

        # Progress bar animation (10% -> 40% -> 70% -> 100%)
        stages = [
            ("`[ █░░░░░░░░░ ] 10% • Initializing Secure Handshake`", 0.4),
            ("`[ ████░░░░░░ ] 40% • Dispatching API Payload`", 0.4),
            ("`[ ███████░░░ ] 70% • Processing Database State`", 0.4),
            ("`[ ██████████ ] 100% • Finalizing Protocol`", 0.3)
        ]

        # First stage edit
        await interaction.response.edit_message(
            embed=make_embed(
                f"⚡ PROCESSING {action_title}...",
                "```ansi\n"
                f"\x1b[1;36m🔄 Executing {action_title} Protocol...\x1b[0m\n"
                "```\n"
                f"» **Target User:** `{clean(self.username)}`\n"
                f"» **App:** `{clean(self.app_name)}`\n\n"
                f"{stages[0][0]}",
                CYAN,
                interaction.user,
                self.action
            ),
            view=None
        )
        await asyncio.sleep(stages[0][1])

        # Remaining loading animation stages
        for bar_text, delay in stages[1:]:
            await interaction.edit_original_response(
                embed=make_embed(
                    f"⚡ PROCESSING {action_title}...",
                    "```ansi\n"
                    f"\x1b[1;36m🔄 Executing {action_title} Protocol...\x1b[0m\n"
                    "```\n"
                    f"» **Target User:** `{clean(self.username)}`\n"
                    f"» **App:** `{clean(self.app_name)}`\n\n"
                    f"{bar_text}",
                    CYAN,
                    interaction.user,
                    self.action
                )
            )
            await asyncio.sleep(delay)

        try:
            if self.action in ("ban", "unban"):
                users = await get_app_users(self.app_token)

                user = next(
                    (
                        u for u in users
                        if str(u.get("username", "")).casefold()
                        == self.username.casefold()
                    ),
                    None
                )

                if not user:
                    return await interaction.edit_original_response(
                        embed=make_embed(
                            "🔴 USER NOT FOUND",
                            "No action taken.",
                            RED,
                            interaction.user,
                            self.action
                        )
                    )

                status = str(user.get("status", "")).lower()
                banned = status in ("banned", "ban", "disabled")

                if self.action == "ban" and banned:
                    return await interaction.edit_original_response(
                        embed=make_embed(
                            "⚠️ ALREADY BANNED",
                            "No change made.",
                            YELLOW,
                            interaction.user,
                            self.action
                        )
                    )

                if self.action == "unban" and not banned:
                    return await interaction.edit_original_response(
                        embed=make_embed(
                            "⚠️ NOT MARKED BANNED",
                            "No change made.",
                            YELLOW,
                            interaction.user,
                            self.action
                        )
                    )

            response = await api(
                "POST",
                paths[self.action],
                json={
                    "username": self.username,
                    "app_token": self.app_token
                }
            )

            payload = parse_api_response(response)
            message = str(
                payload.get("message", payload.get("error", "No message returned."))
            )

            if not 200 <= response.status_code < 300:
                return await interaction.edit_original_response(
                    embed=make_embed(
                        "🔴 ACTION FAILED",
                        f"HTTP `{response.status_code}`\n"
                        f"```{message[:900]}```",
                        RED,
                        interaction.user,
                        self.action
                    )
                )

            if self.action == "delete":
                public_desc = f"✅ Key / User `{clean(self.username)}` permanently deleted from vault."
                public_title = "🗑️ LICENSE KEY DELETED"
            elif self.action == "resethwid":
                public_desc = f"✅ HWID for user `{clean(self.username)}` has been successfully reset."
                public_title = "🔄 HWID RESET SUCCESSFUL"
            elif self.action in ("ban", "unban"):
                public_desc = f"✅ User `{clean(self.username)}` status updated: `{self.action.upper()}`."
                public_title = f"🛡️ USER {self.action.upper()}"
            else:
                public_desc = f"✅ Action `{self.action.upper()}` executed for `{clean(self.username)}`."
                public_title = f"🟢 {self.action.upper()} SUCCESS"

            success_embed = make_embed(
                public_title,
                public_desc,
                RED if self.action in ("ban", "delete") else GREEN,
                interaction.user,
                self.action
            )
            await interaction.edit_original_response(embed=success_embed)

            await audit(
                f"**Action:** `{self.action.upper()}`\n"
                f"**App:** `{clean(self.app_name)}`\n"
                f"**Username:** `{clean(self.username)}`\n"
                f"**By:** {interaction.user.mention}",
                GREEN
            )

        except Exception as exc:
            await interaction.edit_original_response(
                embed=make_embed("🔴 API ERROR", clean(exc, 1000), RED, interaction.user, self.action)
            )

        self.stop()

    @discord.ui.button(
        label="CANCEL",
        emoji="✖️",
        style=discord.ButtonStyle.secondary
    )
    async def cancel(self, interaction: discord.Interaction, button):
        await interaction.response.edit_message(
            embed=make_embed(
                "ACTION CANCELLED",
                "No changes were requested.",
                YELLOW,
                interaction.user,
                self.action
            ),
            view=None
        )
        self.stop()


# =========================================================
# CREATE LICENSE: PUBLIC APP SELECTOR, PRIVATE CREDENTIALS
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

        super().__init__(
            placeholder="📦 STEP 1 — SELECT APPLICATION",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                embed=make_embed(
                    "⛔ ACCESS DENIED",
                    "❌ You don't have permission to use this menu.",
                    RED
                ),
                ephemeral=True
            )

        app_token = self.values[0]

        create_stages = [
            ("`[ █░░░░░░░░░ ] 10% • Initializing Secure Handshake`", 0.4),
            ("`[ ████░░░░░░ ] 40% • Hashing Passwords & Preparing HWID`", 0.4),
            ("`[ ███████░░░ ] 70% • Registering Key in Vault`", 0.4),
            ("`[ ██████████ ] 100% • Provisioning Complete`", 0.3)
        ]

        # Edit original message to remove dropdown menu immediately
        await interaction.response.edit_message(
            embed=make_embed(
                "⚡ PROVISIONING CLIENT ACCOUNT..",
                "```ansi\n"
                "\x1b[1;36m🔄 Creating Zero-Leak Credentials...\x1b[0m\n"
                "```\n"
                f"» **Username:** `{clean(self.username)}`\n"
                f"» **Target App:** `HSL AUTH SYSTEM`\n"
                f"» **Duration:** `{self.days if self.expiry_type == 'custom' else 'Lifetime'} Days`\n\n"
                f"{create_stages[0][0]}",
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
                    "⚡ PROVISIONING CLIENT ACCOUNT..",
                    "```ansi\n"
                    "\x1b[1;36m🔄 Creating Zero-Leak Credentials...\x1b[0m\n"
                    "```\n"
                    f"» **Username:** `{clean(self.username)}`\n"
                    f"» **Target App:** `HSL AUTH SYSTEM`\n"
                    f"» **Duration:** `{self.days if self.expiry_type == 'custom' else 'Lifetime'} Days`\n\n"
                    f"{bar_text}",
                    CYAN,
                    interaction.user,
                    "adduser"
                )
            )
            await asyncio.sleep(delay)

        payload = {
            "username": self.username,
            "password": self.password,
            "app_token": app_token,
            "expiry_type": self.expiry_type
        }

        if self.expiry_type == "custom":
            payload["days"] = self.days
            payload["expiry_days"] = self.days

        try:
            response = await api(
                "POST",
                "/api/create_user",
                json=payload
            )

            data = parse_api_response(response)
            message = str(
                data.get("message", data.get("error", "No message returned."))
            )

            if response.status_code == 200 and any(
                word in message.lower()
                for word in ("created", "success")
            ):
                app_rows = [
                    row for row in get_apps()
                    if row[1] == app_token
                ]
                app_name = (
                    app_rows[0][0]
                    if app_rows else "Selected application"
                )

                expiry = (
                    "Lifetime"
                    if self.expiry_type == "lifetime"
                    else f"{self.days} day(s)"
                )

                generated_key = (
                    data.get("key")
                    or data.get("license_key")
                    or data.get("generated_key")
                )

                public_description = (
                    f"🟢 **CLIENT ACCOUNT CREATED**\n"
                    f"✅ **User** `{clean(self.username)}` **Created for HSL AUTH**\n\n"
                    f"» **Password:** `{clean(self.password)}`\n"
                    f"» **Subscription:** `default`\n"
                    f"» **Expires:** `{datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=self.days if self.expiry_type == 'custom' else 365)} UTC`\n"
                    f"» **HWID Binding:** `Locks on 1st Login` ⚡"
                )

                if generated_key:
                    public_description += (
                        f"\n» **Generated Key:** "
                        f"`{clean(generated_key, 150)}`"
                    )

                public_embed = make_embed(
                    "🔴 CLIENT ACCOUNT CREATED",
                    public_description,
                    RED,
                    interaction.user,
                    "adduser"
                )
                await interaction.edit_original_response(embed=public_embed)

                private_embed = make_embed(
                    "🔐 PRIVATE LOGIN DETAILS",
                    f"**Application:** `{clean(app_name)}`\n"
                    f"**Username:** `{clean(self.username)}`\n"
                    f"**Password:** ||`{clean(self.password)}`||\n"
                    f"**Expiry:** `{expiry}`",
                    GREEN,
                    interaction.user,
                    "adduser"
                )

                await interaction.followup.send(
                    embed=private_embed,
                    ephemeral=True
                )

                await audit(
                    f"**License created**\n"
                    f"**App:** `{clean(app_name)}`\n"
                    f"**Username:** `{clean(self.username)}`\n"
                    f"**Expiry:** `{expiry}`\n"
                    f"**By:** <@{self.owner_id}>",
                    GREEN
                )

            else:
                await interaction.edit_original_response(
                    embed=make_embed(
                        "🔴 CREATE FAILED",
                        f"HTTP `{response.status_code}`\n"
                        f"```{message[:1000]}```",
                        RED,
                        interaction.user,
                        "adduser"
                    )
                )

        except Exception as exc:
            await interaction.edit_original_response(
                embed=make_embed(
                    "🔴 API ERROR",
                    clean(exc, 1000),
                    RED,
                    interaction.user,
                    "adduser"
                )
            )


class CreateAppView(discord.ui.View):
    def __init__(self, owner_id, username, password, expiry_type, days, apps):
        super().__init__(timeout=120)
        self.owner_id = owner_id

        self.add_item(
            CreateAppSelect(
                owner_id,
                username,
                password,
                expiry_type,
                days,
                apps
            )
        )


# =========================================================
# BOT EVENTS
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
        embed=make_embed(
            "🏓 SYSTEM ONLINE",
            f"Latency: `{round(bot.latency * 1000)} ms`\n"
            "```diff\n+ HSL-CORP BOT OPERATIONAL\n```",
            GREEN,
            interaction.user,
            "ping"
        )
    )


@bot.tree.command(name="help", description="⚡ HSL-CORP command panel")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=make_embed(
            "⚡ HSL-CORP • CONTROL PANEL",
            "**APP-FIRST USER ACTIONS**\n"
            "`/create` `/list` `/ban` `/unban` `/delete` `/resethwid`\n\n"
            "**APP MANAGEMENT**\n"
            "`/add_app` `/apps`\n\n"
            "**ACCESS & AUDIT**\n"
            "`/whitelist_add` `/whitelist_remove` `/whitelist_list`\n"
            "`/set_log_channel` `/notify_login` `/update_gmail`\n\n"
            "```ansi\n"
            "\x1b[1;31mSELECT APP → SELECT USER → CONFIRM\x1b[0m\n"
            "```",
            RED,
            interaction.user,
            "help"
        )
    )


@bot.tree.command(
    name="add_app",
    description="➕ Register an app for the app dropdown"
)
async def add_app(
    interaction: discord.Interaction,
    app_name: str,
    app_token: str
):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(
            embed=make_embed(
                "⛔ OWNER ONLY",
                "Only the bot owner can register apps.",
                RED,
                interaction.user,
                "add_app"
            ),
            ephemeral=True
        )

    app_name = app_name.strip()
    app_token = app_token.strip()

    if (
        not app_name
        or len(app_name) > 100
        or not app_token
        or len(app_token) > 250
    ):
        return await interaction.response.send_message(
            embed=make_embed(
                "⚠️ INVALID INPUT",
                "App name/token is empty or too long.",
                YELLOW,
                interaction.user,
                "add_app"
            ),
            ephemeral=True
        )

    try:
        db(
            "INSERT INTO apps (name, token) VALUES (?, ?) "
            "ON CONFLICT(token) DO UPDATE SET name=excluded.name",
            (app_name, app_token)
        )
    except sqlite3.Error as exc:
        return await interaction.response.send_message(
            embed=make_embed("🔴 DATABASE ERROR", clean(exc), RED, interaction.user, "add_app"),
            ephemeral=True
        )

    await interaction.response.send_message(
        embed=make_embed(
            "🟢 APP REGISTERED",
            f"**App:** `{clean(app_name)}`\n"
            f"**Token:** ||`{clean(app_token, 100)}`||",
            GREEN,
            interaction.user,
            "add_app"
        )
    )


@bot.tree.command(name="apps", description="📦 List apps registered in this bot")
async def apps_command(interaction: discord.Interaction):
    if await deny(interaction, "apps"):
        return

    apps = get_apps()

    text = "\n".join(
        f"📦 **{clean(name, 90)}** — token ||`{clean(token, 70)}`||"
        for name, token in apps[:25]
    ) or "No apps registered. Use `/add_app`."

    await interaction.response.send_message(
        embed=make_embed(
            f"📦 REGISTERED APPS ({len(apps)})",
            text[:3900],
            CYAN,
            interaction.user,
            "apps"
        )
    )


@bot.tree.command(
    name="create",
    description="🚀 Create a license after selecting an app"
)
@app_commands.choices(
    expiry_type=[
        app_commands.Choice(name="Custom Days", value="custom"),
        app_commands.Choice(name="Lifetime", value="lifetime")
    ]
)
async def create(
    interaction: discord.Interaction,
    username: str,
    password: str,
    expiry_type: app_commands.Choice[str],
    days: int = 0
):
    if await deny(interaction, "create"):
        return

    username = username.strip()
    password = password.strip()

    if (
        not username
        or len(username) > 80
        or not password
        or len(password) > 256
    ):
        return await interaction.response.send_message(
            embed=make_embed(
                "⚠️ INVALID INPUT",
                "Username must be 1–80 characters; "
                "password 1–256 characters.",
                YELLOW,
                interaction.user,
                "adduser"
            ),
            ephemeral=True
        )

    if expiry_type.value == "custom" and not 1 <= days <= 36500:
        return await interaction.response.send_message(
            embed=make_embed(
                "⚠️ INVALID EXPIRY",
                "For custom expiry, `days` must be 1–36500.",
                YELLOW,
                interaction.user,
                "adduser"
            ),
            ephemeral=True
        )

    apps = get_apps()

    if not apps:
        return await interaction.response.send_message(
            embed=make_embed(
                "📦 NO APPS",
                "Register an app first with `/add_app`.",
                YELLOW,
                interaction.user,
                "adduser"
            ),
            ephemeral=True
        )

    view = CreateAppView(
        interaction.user.id,
        username,
        password,
        expiry_type.value,
        days,
        apps
    )

    expiry = (
        "Lifetime"
        if expiry_type.value == "lifetime"
        else f"{days} day(s)"
    )

    await interaction.response.send_message(
        embed=make_embed(
            "⚡ HSL Auth Provisioning System",
            f"**Account Details Prepared:**\n\n"
            f"» **Username:** `{clean(username)}`\n"
            f"» **Password:** `{clean(password)}`\n"
            f"» **Duration:** `{expiry}`\n\n"
            "_____________________________________\n\n"
            "*Select target Application from the dropdown below to create account:* ⚡",
            CYAN,
            interaction.user,
            "adduser"
        ),
        view=view
    )


# =========================================================
# APP-SCOPED USER COMMANDS
# =========================================================
async def show_app_action(interaction, action):
    if await deny(interaction, action):
        return

    apps = get_apps()

    if not apps:
        return await interaction.response.send_message(
            embed=make_embed(
                "📦 NO APPS",
                "Register an app first with `/add_app`.",
                YELLOW,
                interaction.user,
                action
            ),
            ephemeral=True
        )

    await interaction.response.send_message(
        embed=make_embed(
            f"⚡ HSL-CORP • {action.upper()}",
            "👇 **STEP 1 — SELECT APPLICATION**\n\n"
            "After selecting an app, choose a username and confirm.",
            RED if action in ("ban", "delete") else CYAN,
            interaction.user,
            action
        ),
        view=AppActionView(interaction.user.id, action, apps)
    )


@bot.tree.command(
    name="list",
    description="📜 List users in a selected application"
)
async def list_users(interaction: discord.Interaction):
    await show_app_action(interaction, "list")


@bot.tree.command(name="ban", description="🔨 Select app then user to ban")
async def ban(interaction: discord.Interaction):
    await show_app_action(interaction, "ban")


@bot.tree.command(name="unban", description="🔓 Select app then user to unban")
async def unban(interaction: discord.Interaction):
    await show_app_action(interaction, "unban")


@bot.tree.command(name="delete", description="🗑️ Select app then user to delete")
async def delete_user(interaction: discord.Interaction):
    await show_app_action(interaction, "delete")


@bot.tree.command(
    name="resethwid",
    description="🔄 Select app then user to reset HWID"
)
async def reset_hwid(interaction: discord.Interaction):
    await show_app_action(interaction, "resethwid")


# =========================================================
# AUDIT & WHITELIST COMMANDS
# =========================================================
@bot.tree.command(
    name="set_log_channel",
    description="📋 Set HSL-CORP audit channel"
)
async def set_log_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    if await deny(interaction, "set_log_channel"):
        return

    db(
        "INSERT INTO bot_settings (key, value) VALUES ('log_channel_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(channel.id),)
    )

    await interaction.response.send_message(
        embed=make_embed(
            "🟢 AUDIT CHANNEL SET",
            f"Log channel: {channel.mention}",
            GREEN,
            interaction.user,
            "set_log_channel"
        )
    )


@bot.tree.command(
    name="whitelist_add",
    description="🛡️ Grant access to one command"
)
async def whitelist_add(
    interaction: discord.Interaction,
    member: discord.Member,
    command_name: str
):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(
            embed=make_embed(
                "⛔ OWNER ONLY",
                "Only the owner can edit the whitelist.",
                RED,
                interaction.user,
                "whitelist_add"
            ),
            ephemeral=True
        )

    command_name = command_name.strip().lower().lstrip("/")

    db(
        "INSERT OR IGNORE INTO discord_whitelist (user_id, command) "
        "VALUES (?, ?)",
        (str(member.id), command_name)
    )

    await interaction.response.send_message(
        embed=make_embed(
            "🟢 WHITELIST UPDATED",
            f"**Member:** {member.mention}\n"
            f"**Command:** `{clean(command_name)}`",
            GREEN,
            interaction.user,
            "whitelist_add"
        )
    )


@bot.tree.command(
    name="whitelist_remove",
    description="❌ Remove access to one command"
)
async def whitelist_remove(
    interaction: discord.Interaction,
    member: discord.Member,
    command_name: str
):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(
            embed=make_embed(
                "⛔ OWNER ONLY",
                "Only the owner can edit the whitelist.",
                RED,
                interaction.user,
                "whitelist_remove"
            ),
            ephemeral=True
        )

    command_name = command_name.strip().lower().lstrip("/")

    db(
        "DELETE FROM discord_whitelist WHERE user_id=? AND command=?",
        (str(member.id), command_name)
    )

    await interaction.response.send_message(
        embed=make_embed(
            "🗑️ WHITELIST ENTRY REMOVED",
            f"**Member:** {member.mention}\n"
            f"**Command:** `{clean(command_name)}`",
            YELLOW,
            interaction.user,
            "whitelist_remove"
        )
    )


@bot.tree.command(
    name="whitelist_list",
    description="📑 Show whitelist entries"
)
async def whitelist_list(interaction: discord.Interaction):
    if await deny(interaction, "whitelist_list"):
        return

    rows = db(
        "SELECT user_id, command FROM discord_whitelist ORDER BY command",
        fetch=True
    )

    description = "\n".join(
        f"• <@{uid}> — `{clean(cmd, 80)}`"
        for uid, cmd in rows[:60]
    ) or "Whitelist is empty."

    await interaction.response.send_message(
        embed=make_embed(
            "🛡️ ACCESS CONTROL LIST",
            description[:3900],
            PURPLE,
            interaction.user,
            "whitelist_list"
        )
    )


# =========================================================
# GMAIL UPDATE
# =========================================================
@bot.tree.command(
    name="update_gmail",
    description="📧 Update a license account Gmail"
)
async def update_gmail(
    interaction: discord.Interaction,
    username: str,
    email: str
):
    if await deny(interaction, "update_gmail"):
        return

    username = username.strip()
    email = email.strip()

    if (
        not username
        or len(username) > 80
        or "@" not in email
        or len(email) > 254
    ):
        return await interaction.response.send_message(
            embed=make_embed(
                "⚠️ INVALID INPUT",
                "Enter a valid username and email.",
                YELLOW,
                interaction.user,
                "update_gmail"
            ),
            ephemeral=True
        )

    await interaction.response.defer(thinking=True)

    try:
        response = await api(
            "POST",
            "/api/bot/update_gmail",
            json={"username": username, "gmail": email}
        )

        data = parse_api_response(response)
        message = str(
            data.get("message", data.get("error", "No message returned."))
        )

        if response.status_code == 200:
            await interaction.followup.send(
                embed=make_embed(
                    "📧 GMAIL UPDATE RESPONSE",
                    f"**Username:** `{clean(username)}`\n"
                    f"**Email:** `{clean(email)}`\n"
                    f"**API:** {clean(message, 600)}",
                    GREEN,
                    interaction.user,
                    "update_gmail"
                )
            )

            await audit(
                f"**Gmail update:** `{clean(username)}`\n"
                f"**By:** {interaction.user.mention}",
                GREEN
            )
        else:
            await interaction.followup.send(
                embed=make_embed(
                    "🔴 GMAIL UPDATE FAILED",
                    f"HTTP `{response.status_code}`\n"
                    f"```{message[:800]}```",
                    RED,
                    interaction.user,
                    "update_gmail"
                )
            )

    except Exception as exc:
        await interaction.followup.send(
            embed=make_embed("🔴 API ERROR", clean(exc, 900), RED, interaction.user, "update_gmail")
        )


# =========================================================
# LOGIN NOTIFICATION
# =========================================================
@bot.tree.command(
    name="notify_login",
    description="🔔 Send a manual login audit notification"
)
@app_commands.choices(
    status=[
        app_commands.Choice(name="Success", value="SUCCESS"),
        app_commands.Choice(name="Failed", value="FAILED")
    ]
)
async def notify_login(
    interaction: discord.Interaction,
    logged_username: str,
    status: app_commands.Choice[str],
    ip_address: str = "Hidden/Proxy",
    hwid: str = "Not bound"
):
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
        return await interaction.response.send_message(
            embed=make_embed(
                "⚠️ LOG CHANNEL NOT SET",
                "Use `/set_log_channel` first.",
                YELLOW,
                interaction.user,
                "notify_login"
            ),
            ephemeral=True
        )

    ok = status.value == "SUCCESS"
    now = datetime.datetime.now(datetime.timezone.utc)

    e = make_embed(
        "🛡️ HSL-CORP • LOGIN AUDIT",
        "Login audit notification",
        GREEN if ok else RED,
        interaction.user,
        "notify_login"
    )

    e.add_field(
        name="Username",
        value=f"`{clean(logged_username)}`",
        inline=True
    )
    e.add_field(
        name="Status",
        value="🟢 SUCCESS" if ok else "🔴 FAILED",
        inline=True
    )
    e.add_field(
        name="Timestamp",
        value=f"<t:{int(now.timestamp())}:F>",
        inline=False
    )
    e.add_field(
        name="IP",
        value=f"`{clean(ip_address, 100)}`",
        inline=True
    )
    e.add_field(
        name="HWID",
        value=f"`{clean(hwid, 180)}`",
        inline=False
    )

    await channel.send(
        content=f"🚨 Login audit • <@{OWNER_ID}>",
        embed=e,
        allowed_mentions=discord.AllowedMentions(
            users=[discord.Object(id=OWNER_ID)]
        )
    )

    await interaction.response.send_message(
        embed=make_embed(
            "🟢 AUDIT DISPATCHED",
            f"Sent to {channel.mention}.",
            GREEN,
            interaction.user,
            "notify_login"
        )
    )


# =========================================================
# START BOT
# =========================================================
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Add it to your Railway Variables."
    )

bot.run(BOT_TOKEN)