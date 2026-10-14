import discord
from discord.ext import commands
import sqlite3
import os
import datetime
import asyncio
import requests

RAILWAY_URL = "https://hsl-corp-production-9678.up.railway.app"

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
OWNER_ID = int(os.getenv("OWNER_ID", "1517901703263944758"))
TARGET_PING_USER = "<@1517901703263944758>"            # Tag/Mention User ID

DB_PATH = "hsl.db"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix=",", intents=intents, help_command=None)

def db(q, p=(), fetch=False):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(q, p)
    data = cur.fetchall() if fetch else None
    con.commit()
    con.close()
    return data

def init_db():
    db("CREATE TABLE IF NOT EXISTS discord_whitelist (user_id TEXT, command TEXT)")
    db("CREATE TABLE IF NOT EXISTS apps (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, token TEXT UNIQUE)")
    db("CREATE TABLE IF NOT EXISTS tool_users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, app_token TEXT, status TEXT DEFAULT 'active', hwid TEXT, gmail TEXT)")
    db("CREATE TABLE IF NOT EXISTS bot_settings (key TEXT UNIQUE, value TEXT)")
    
    # Auto-add gmail column if missing
    try:
        db("ALTER TABLE tool_users ADD COLUMN gmail TEXT")
    except Exception:
        pass
        
    print("✅ DB Ready")

init_db()

def get_log_channel_id():
    res = db("SELECT value FROM bot_settings WHERE key='log_channel_id'", fetch=True)
    if res and res[0][0]:
        return int(res[0][0])
    return 0

def is_owner_or_whitelisted(user_id, cmd_name):
    if user_id == OWNER_ID: return True
    res = db("SELECT * FROM discord_whitelist WHERE user_id=? AND command=?", (str(user_id), cmd_name.lower()), True)
    return len(res) > 0

def stylish_embed(title, desc, color=0x5865F2):
    e = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
    if bot.user:
        e.set_footer(text="HSL • AUTH SYSTEM", icon_url=bot.user.display_avatar.url)
    else:
        e.set_footer(text="HSL • AUTH SYSTEM")
    return e


async def api_request(method, url, **kwargs):
    """Run requests without blocking Discord's event loop."""
    kwargs.setdefault("timeout", 12)
    return await asyncio.to_thread(requests.request, method, url, **kwargs)


class CreateAppSelect(discord.ui.View):
    def __init__(self, owner_id: int, username: str, password: str, expiry_type: str, days: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.username = username
        self.password = password
        self.expiry_type = expiry_type
        self.days = days
        rows = db("SELECT name, token FROM apps ORDER BY name COLLATE NOCASE", fetch=True)
        if rows:
            self.add_item(CreateAppDropdown(rows, self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Ye create menu kisi aur ke liye hai.", ephemeral=True)
            return False
        return True


class CreateAppDropdown(discord.ui.Select):
    def __init__(self, apps, parent_view: CreateAppSelect):
        self.parent_view = parent_view
        options = [discord.SelectOption(label=str(name)[:100], value=token, description=f"App token: {token[:16]}…") for name, token in apps[:25]]
        super().__init__(placeholder="📦 Select application / EXE", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view = self.parent_view
        if view.expiry_type == "custom" and not (1 <= view.days <= 36500):
            return await interaction.response.send_message(
                embed=stylish_embed("⚠️ Invalid Expiry", "Custom expiry ke liye `/create` mein **days** 1–36500 set karo.", 0xF1C40F),
                ephemeral=True,
            )

        await interaction.response.edit_message(
            embed=stylish_embed("⏳ HSL AUTH • PROVISIONING", "```ansi\n\x1b[1;36m[■■■□□□□□□□] Connecting to secure auth server...\x1b[0m\n```\nApp selected. User license create ho raha hai…", 0x00D9FF),
            view=None,
        )
        payload = {
            "username": view.username,
            "password": view.password,
            "app_token": self.values[0],
            "expiry_type": view.expiry_type,
        }
        if view.expiry_type == "custom":
            payload["days"] = view.days
            payload["expiry_days"] = view.days

        try:
            response = await api_request("POST", f"{RAILWAY_URL}/api/create_user", json=payload)
            try:
                data = response.json()
            except ValueError:
                data = {"message": response.text[:500] or "Server returned a non-JSON response."}
            message = str(data.get("message", "Request failed."))
            if response.status_code == 200 and "created" in message.lower():
                expiry_label = "♾️ LIFETIME" if view.expiry_type == "lifetime" else f"⏳ {view.days} day(s)"
                embed = stylish_embed(
                    "🟢 LICENSE CREATED • HSL-CORP",
                    f"```ansi\n\x1b[1;32mACCESS GRANTED\x1b[0m\n```\n"
                    f"**Application token:** `{self.values[0][:12]}…`\n"
                    f"**Username:** `{discord.utils.escape_markdown(view.username)}`\n"
                    f"**Password:** `{discord.utils.escape_markdown(view.password)}`\n"
                    f"**Expiry:** {expiry_label}\n\n"
                    "```diff\n+ License registered on the auth server\n+ App token submitted\n```",
                    0x00F5A0,
                )
                await interaction.edit_original_response(embed=embed, view=None)
                await send_log(f"🟢 Created `{view.username}` for app token `{self.values[0][:12]}…` ({expiry_label})")
            else:
                await interaction.edit_original_response(
                    embed=stylish_embed("🔴 LICENSE CREATION FAILED", f"**HTTP:** `{response.status_code}`\n**Server:** {discord.utils.escape_markdown(message)[:2500]}", 0xFF304F),
                    view=None,
                )
        except Exception as exc:
            await interaction.edit_original_response(
                embed=stylish_embed("🔴 CONNECTION ERROR", f"Railway auth API se connect nahi hua.\n```{str(exc)[:1200]}```", 0xFF304F),
                view=None,
            )

async def send_log(message: str):
    channel_id = get_log_channel_id()
    channel = bot.get_channel(channel_id) if channel_id else None
    if channel:
        try:
            await channel.send(embed=stylish_embed("🧾 HSL AUTH AUDIT", message, 0x00D9FF))
        except discord.HTTPException:
            pass

@bot.event
async def on_ready():
    print(f"\n[HSL-CORP] Online as {bot.user} | ID={bot.user.id}")
    try:
        synced = await bot.tree.sync()
        print(f"[HSL-CORP] Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"[HSL-CORP] Sync Error: {e}")

# ========== HELP & PING ==========
@bot.tree.command(name="ping", description="⚡ Check latency")
async def ping_s(interaction: discord.Interaction):
    await interaction.response.send_message(embed=stylish_embed("🏓 Pong!", f"```Latency: {round(bot.latency*1000)}ms```", 0x00FF99))

@bot.tree.command(name="help", description="📜 Help menu")
async def help_s(interaction: discord.Interaction):
    desc = "**👑 AUTH:** `/create` `/list` `/ban` `/unban` `/delete` `/resethwid` `/apps` `/set_log_channel` `/notify_login` `/update_gmail`\n**🛡️ WL:** `/whitelist_add` `/whitelist_remove` `/whitelist_list`\n**⚡:** `/ping` `/help`"
    em = stylish_embed("✨ HSL AUTH - Help", desc, 0x5865F2)
    if bot.user: em.set_thumbnail(url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=em)

# ========== SET LOG CHANNEL COMMAND ==========
@bot.tree.command(name="set_log_channel", description="⚙️ Set the channel where login alerts will be posted")
async def set_log_channel_s(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_owner_or_whitelisted(interaction.user.id, "set_log_channel"):
        return await interaction.response.send_message(embed=stylish_embed("❌ Denied", "Sirf Owner/Whitelisted users!", 0xFF0000), ephemeral=True)
    
    db("INSERT OR REPLACE INTO bot_settings (key, value) VALUES ('log_channel_id', ?)", (str(channel.id),))
    await interaction.response.send_message(embed=stylish_embed("🎯 Log Channel Set", f"Login audit logs ab se {channel.mention} me aayenge! 🟢", 0x00FF99))

# ========== WHITELIST ==========
@bot.tree.command(name="whitelist_add", description="➕ Access do")
async def w_add(interaction: discord.Interaction, member: discord.Member, command_name: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=stylish_embed("❌ No Access", "Sirf Owner!", 0xFF0000), ephemeral=True)
    db("INSERT INTO discord_whitelist (user_id, command) VALUES (?,?)", (str(member.id), command_name.lower()))
    await interaction.response.send_message(embed=stylish_embed("✅ Added", f"{member.mention} → `{command_name}` 🟢", 0x00FF88))

@bot.tree.command(name="whitelist_remove", description="➖ Access hatao")
async def w_rem(interaction: discord.Interaction, member: discord.Member, command_name: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=stylish_embed("❌ No Access", "Sirf Owner!", 0xFF0000), ephemeral=True)
    db("DELETE FROM discord_whitelist WHERE user_id=? AND command=?", (str(member.id), command_name.lower()))
    await interaction.response.send_message(embed=stylish_embed("🗑️ Removed", f"{member.mention} ka `{command_name}` hata diya!", 0xFF5050))

@bot.tree.command(name="whitelist_list", description="📋 Whitelist dekho")
async def w_list(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = db("SELECT user_id, command FROM discord_whitelist", fetch=True)
    if not rows: return await interaction.followup.send(embed=stylish_embed("📭 Empty", "Koi whitelist nahi"))
    desc = "".join([f"<@{r[0]}> → `{r[1]}`\n" for r in rows])
    await interaction.followup.send(embed=stylish_embed("🛡️ Whitelist", desc))

# ========== GMAIL UPDATE COMMAND ==========
@bot.tree.command(name="update_gmail", description="📧 User ka Gmail update karein")
async def update_gmail_s(interaction: discord.Interaction, username: str, email: str):
    if not is_owner_or_whitelisted(interaction.user.id, "update_gmail"):
        return await interaction.response.send_message(embed=stylish_embed("❌ Denied", "No Access!", 0xFF0000), ephemeral=True)
    
    await interaction.response.defer()
    db("UPDATE tool_users SET gmail=? WHERE username=?", (email.strip(), username.strip()))
    
    try:
        requests.post(f"{RAILWAY_URL}/api/bot/update_gmail", json={"username": username.strip(), "gmail": email.strip()}, timeout=5)
    except Exception:
        pass
        
    await interaction.followup.send(embed=stylish_embed("📧 Gmail Updated", f"User: `{username}`\nNew Gmail: `{email}`", 0x00FF99))

# ========== PROFESSIONAL LOGIN NOTIFICATION COMMAND ==========
@bot.tree.command(name="notify_login", description="🚨 Professional login security log send karein")
async def notify_login_s(
    interaction: discord.Interaction, 
    logged_username: str, 
    ip_address: str = "Hidden/Proxy", 
    hwid: str = "Not Bound",
    status: str = "SUCCESS"
):
    if not is_owner_or_whitelisted(interaction.user.id, "notify_login"):
        return await interaction.response.send_message(embed=stylish_embed("❌ Denied", "No Access!", 0xFF0000), ephemeral=True)
    
    # Exact Time Calculations
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    ist_now = utc_now + datetime.timedelta(hours=5, minutes=30)
    
    time_utc_str = utc_now.strftime("%Y-%m-%d | %H:%M:%S UTC")
    time_ist_str = ist_now.strftime("%d %b %Y | %I:%M:%S %p IST")
    discord_timestamp = f"<t:{int(utc_now.timestamp())}:F> (<t:{int(utc_now.timestamp())}:R>)"

    embed_color = 0x00FF99 if status.upper() == "SUCCESS" else 0xFF0000
    status_icon = "🟢 AUTHENTICATED" if status.upper() == "SUCCESS" else "🔴 ACCESS DENIED"

    embed = discord.Embed(
        title="🛡️ HSL AUTHENTICATION AUDIT LOG",
        description=f"System detected a login attempt for account **`{logged_username}`**.",
        color=embed_color,
        timestamp=utc_now
    )
    
    embed.add_field(name="👤 Target User", value=f"```yaml\n{logged_username}\n```", inline=True)
    embed.add_field(name="⚡ Status", value=f"```diff\n+ {status_icon}\n```" if status.upper() == "SUCCESS" else f"```diff\n- {status_icon}\n```", inline=True)
    embed.add_field(name="🌐 Client IP", value=f"```ini\n[{ip_address}]\n```", inline=True)
    
    embed.add_field(name="🔒 HWID Fingerprint", value=f"`{hwid}`", inline=False)
    embed.add_field(name="⏰ Exact Login Time (IST)", value=f"`{time_ist_str}`", inline=True)
    embed.add_field(name="🌐 Standard Time (UTC)", value=f"`{time_utc_str}`", inline=True)
    embed.add_field(name="📅 Relative Timestamp", value=discord_timestamp, inline=False)

    if bot.user:
        embed.set_footer(text="HSL SECURE INFRASTRUCTURE • REALTIME LOGS", icon_url=bot.user.display_avatar.url)
    
    log_channel_id = get_log_channel_id()
    target_channel = bot.get_channel(log_channel_id) if log_channel_id != 0 else None
    
    if target_channel:
        await target_channel.send(content=f"🚨 **LOGIN DETECTED** | Attention: {TARGET_PING_USER}", embed=embed)
        await interaction.response.send_message(embed=stylish_embed("✅ Audit Sent", f"Login log dispatched to {target_channel.mention}", 0x00FF99), ephemeral=True)
    else:
        await interaction.response.send_message(content=f"🚨 **LOGIN DETECTED** | Attention: {TARGET_PING_USER}\n*(Tip: Set log channel using `/set_log_channel`)*", embed=embed)

# ========== AUTH COMMANDS ==========
@bot.tree.command(name="list", description="👤 All users")
async def list_s(interaction: discord.Interaction):
    try:
        if not is_owner_or_whitelisted(interaction.user.id, "list"):
            return await interaction.response.send_message(embed=stylish_embed("❌ Denied", "No Access!", 0xFF0000), ephemeral=True)
        await interaction.response.defer()
        
        res = requests.get(f"{RAILWAY_URL}/api/bot/users", timeout=10)
        data = res.json()
        users = data.get("users", [])
        
        if not users: 
            return await interaction.followup.send(embed=stylish_embed("📭 No Users", "Database khali hai"))
        
        desc = ""
        for u in users:
            emoji = "🟢" if u['status'] == 'active' else "🔴"
            hwid = (u['hwid'][:8]+"...") if u['hwid'] and u['hwid'] != "Not Bound" else "`No HWID`"
            desc += f"{emoji} `{u['username']}` | {u['status']} | {hwid}\n"
            
        await interaction.followup.send(embed=stylish_embed(f"👥 Total: {len(users)}", desc[:4000]))
    except Exception as e:
        print(e)
        msg = f"Error: {e}"
        if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
        else: await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="add_app", description="📦 Register an app token for the create dropdown")
async def add_app_s(interaction: discord.Interaction, app_name: str, app_token: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message(embed=stylish_embed("⛔ OWNER ONLY", "Sirf bot owner app token register kar sakta hai.", 0xFF304F), ephemeral=True)
    app_name = app_name.strip()
    app_token = app_token.strip()
    if not app_name or len(app_name) > 100 or not app_token or len(app_token) > 200:
        return await interaction.response.send_message(embed=stylish_embed("⚠️ Invalid Input", "App name 1–100 characters aur token 1–200 characters hona chahiye.", 0xF1C40F), ephemeral=True)
    try:
        db("INSERT INTO apps (name, token) VALUES (?, ?) ON CONFLICT(token) DO UPDATE SET name=excluded.name", (app_name, app_token))
        await interaction.response.send_message(embed=stylish_embed("🟢 APP REGISTERED", f"**App:** `{discord.utils.escape_markdown(app_name)}`\n**Token:** `{app_token[:10]}…`\n\nAb `/create` ke dropdown mein ye app available hai.", 0x00F5A0), ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(embed=stylish_embed("🔴 DATABASE ERROR", str(exc)[:1500], 0xFF304F), ephemeral=True)


@bot.tree.command(name="create", description="⚡ Create a license for a selected app")
@discord.app_commands.describe(username="New license username", password="New license password", expiry_type="Choose custom days or lifetime", days="Custom expiry days (1–36500; only for custom)")
@discord.app_commands.choices(expiry_type=[
    discord.app_commands.Choice(name="Custom Days", value="custom"),
    discord.app_commands.Choice(name="Lifetime", value="lifetime"),
])
async def create_s(
    interaction: discord.Interaction,
    username: str,
    password: str,
    expiry_type: discord.app_commands.Choice[str],
    days: int = 0,
):
    if not is_owner_or_whitelisted(interaction.user.id, "create"):
        return await interaction.response.send_message(embed=stylish_embed("⛔ ACCESS DENIED", "Is command ke liye permission nahi hai.", 0xFF304F), ephemeral=True)
    username, password = username.strip(), password.strip()
    if not username or len(username) > 80 or not password or len(password) > 256:
        return await interaction.response.send_message(embed=stylish_embed("⚠️ INVALID INPUT", "Username 1–80 characters aur password 1–256 characters hona chahiye.", 0xF1C40F), ephemeral=True)
    selected_expiry = expiry_type.value
    if selected_expiry == "custom" and not (1 <= days <= 36500):
        return await interaction.response.send_message(embed=stylish_embed("⚠️ EXPIRY REQUIRED", "`days` ko 1 se 36500 ke beech set karo. Example: `/create username:Test password:123 expiry_type:Custom Days days:30`", 0xF1C40F), ephemeral=True)
    rows = db("SELECT name, token FROM apps ORDER BY name COLLATE NOCASE", fetch=True)
    if not rows:
        return await interaction.response.send_message(embed=stylish_embed("📦 NO APPS REGISTERED", "Pehle `/add_app` use karke dashboard se app token register karo. Token dashboard ke app section se copy karo.", 0xF1C40F), ephemeral=True)
    view = CreateAppSelect(interaction.user.id, username, password, selected_expiry, days)
    expiry_text = "♾️ Lifetime" if selected_expiry == "lifetime" else f"⏳ {days} day(s)"
    await interaction.response.send_message(
        embed=stylish_embed("⚡ HSL-CORP • CREATE LICENSE", f"```ansi\n\x1b[1;36mSECURE LICENSE PROVISIONING\x1b[0m\n```\n**Username:** `{discord.utils.escape_markdown(username)}`\n**Expiry:** {expiry_text}\n\n👇 Neeche dropdown se target EXE / application select karo.", 0x00D9FF),
        view=view,
        ephemeral=True,
    )

@bot.tree.command(name="ban", description="🔨 Ban user")
async def ban_s(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    if not is_owner_or_whitelisted(interaction.user.id, "ban"):
        return await interaction.followup.send(embed=stylish_embed("❌ Denied", "No Access"), ephemeral=True)

    try:
        res = requests.post(f"{RAILWAY_URL}/api/bot/toggle_ban", json={"username": username.strip()}, timeout=10)
        msg = res.json().get("message", "Updated")
        await interaction.followup.send(embed=stylish_embed("🔨 Ban Toggle", f"`{msg}`", 0xFF0000))
    except Exception as e:
        await interaction.followup.send(embed=stylish_embed("❌ Error", str(e), 0xFF0000))

@bot.tree.command(name="unban", description="✅ Unban user")
async def unban_s(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    if not is_owner_or_whitelisted(interaction.user.id, "unban"):
        return await interaction.followup.send(embed=stylish_embed("❌ Denied", "No Access"), ephemeral=True)

    try:
        res = requests.post(f"{RAILWAY_URL}/api/bot/toggle_ban", json={"username": username.strip()}, timeout=10)
        msg = res.json().get("message", "Updated")
        await interaction.followup.send(embed=stylish_embed("✅ Unban Toggle", f"`{msg}`", 0x00FF99))
    except Exception as e:
        await interaction.followup.send(embed=stylish_embed("❌ Error", str(e), 0xFF0000))

@bot.tree.command(name="delete", description="🗑️ Delete user")
async def delete_s(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    if not is_owner_or_whitelisted(interaction.user.id, "delete"):
        return await interaction.followup.send(embed=stylish_embed("❌ Denied", "No Access"), ephemeral=True)

    try:
        res = requests.post(f"{RAILWAY_URL}/api/bot/delete_user", json={"username": username.strip()}, timeout=10)
        msg = res.json().get("message", "Deleted")
        await interaction.followup.send(embed=stylish_embed("🗑️ Deleted", f"`{username}` deleted from Dashboard!"))
    except Exception as e:
        await interaction.followup.send(embed=stylish_embed("❌ Error", str(e), 0xFF0000))

@bot.tree.command(name="resethwid", description="♻️ Reset HWID")
async def reset_s(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    if not is_owner_or_whitelisted(interaction.user.id, "resethwid"):
        return await interaction.followup.send(embed=stylish_embed("❌ Denied", "No Access"), ephemeral=True)

    try:
        res = requests.post(f"{RAILWAY_URL}/api/bot/reset_hwid", json={"username": username.strip()}, timeout=10)
        msg = res.json().get("message", "HWID Reset Successful")
        await interaction.followup.send(embed=stylish_embed("♻️ HWID Reset", f"**{username}** - {msg}", 0x5865F2))
    except Exception as e:
        await interaction.followup.send(embed=stylish_embed("❌ Error", str(e), 0xFF0000))

@bot.tree.command(name="apps", description="📦 Registered apps and dashboard")
async def apps_s(interaction: discord.Interaction):
    if not is_owner_or_whitelisted(interaction.user.id, "apps"):
        return await interaction.response.send_message(embed=stylish_embed("⛔ ACCESS DENIED", "No Access!", 0xFF304F), ephemeral=True)
    rows = db("SELECT name, token FROM apps ORDER BY name COLLATE NOCASE", fetch=True)
    if rows:
        lines = [f"🟢 **{discord.utils.escape_markdown(name)}** — `{token[:12]}…`" for name, token in rows[:30]]
        body = "\n".join(lines)
    else:
        body = "No app tokens registered yet. Use `/add_app` first."
    body += f"\n\n**Dashboard:** {RAILWAY_URL}/dashboard"
    await interaction.response.send_message(embed=stylish_embed(f"📦 HSL APPLICATIONS • {len(rows)}", body[:4000], 0x00D9FF), ephemeral=True)


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing. Add it in Railway Variables.")

bot.run(BOT_TOKEN)