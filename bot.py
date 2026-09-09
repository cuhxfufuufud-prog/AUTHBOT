import discord
from discord.ext import commands
import sqlite3
import os
import datetime
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

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ {bot.user} | Synced {len(synced)} commands")
    except Exception as e:
        print(f"Sync Error: {e}")

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

@bot.tree.command(name="create", description="➕ New user")
async def create_s(interaction: discord.Interaction, username: str, password: str):
    try:
        if not is_owner_or_whitelisted(interaction.user.id, "create"):
            return await interaction.response.send_message(embed=stylish_embed("❌ Denied", "No Access!"), ephemeral=True)

        await interaction.response.defer()

        API_URL = f"{RAILWAY_URL}/api/create_user"
        APP_TOKEN = "HSL_K68EWHIKXG56NBRZHE26"

        payload = {
            "username": username.strip(),
            "password": password.strip(),
            "app_token": APP_TOKEN,
        }

        response = requests.post(API_URL, json=payload, timeout=10)
        data = response.json()

        if response.status_code == 200 and "User Created" in data.get("message", ""):
            await interaction.followup.send(embed=stylish_embed("✅ Created", f"```User: {username}\nPass: {password}```", 0x00FF99))
        else:
            msg = data.get("message", "User creation failed!")
            await interaction.followup.send(embed=stylish_embed("❌ Failed", msg, 0xFF0000))

    except Exception as e:
        print(f"CREATE ERROR: {e}")
        if interaction.response.is_done():
            await interaction.followup.send(embed=stylish_embed("❌ Error", str(e), 0xFF0000))
        else:
            await interaction.response.send_message(embed=stylish_embed("❌ Error", str(e), 0xFF0000), ephemeral=True)

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

@bot.tree.command(name="apps", description="📦 Apps list")
async def apps_s(interaction: discord.Interaction):
    try:
        if not is_owner_or_whitelisted(interaction.user.id, "apps"):
            return await interaction.response.send_message(embed=stylish_embed("❌ Denied", "No Access"), ephemeral=True)
        await interaction.response.defer()

        await interaction.followup.send(embed=stylish_embed("📦 Application Info", f"Manage your apps & tokens directly at:\n{RAILWAY_URL}/dashboard", 0x5865F2))
    except Exception as e:
        await interaction.followup.send(f"⚠️ Error: {e}", ephemeral=True)

bot.run(BOT_TOKEN)