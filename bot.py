# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP
# Dansk version med starttid, besked, billede, auto-post kl. 12 og auto-slet kl. 00:00
# + Aktiver/deaktiver med live nedetidsur

import os
import json
import datetime as dt
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import tasks

# -------------------- Konfiguration --------------------
TOKEN = os.getenv("DISCORD_TOKEN")
ROLE_DISP = "Disponent"
CHANNEL_NAME = "🗓️┃planday-dagens-vagtplan"
TZ = ZoneInfo("Europe/Copenhagen")
DAILY_H, DAILY_M = 12, 0

def _parse_guild_id():
    raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    try:
        return int(raw) if raw.isdigit() else None
    except Exception:
        return None

GUILD_ID = _parse_guild_id()
STATE_FILE = "planday_state.json"

# -------------------- Intents & Client --------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# -------------------- Hjælpefunktioner --------------------
def format_duration(delta: dt.timedelta) -> str:
    secs = int(delta.total_seconds())
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h:02}:{m:02}:{s:02}"

def dansk_dato(d: dt.date) -> str:
    DAYS = ["mandag","tirsdag","onsdag","torsdag","fredag","lørdag","søndag"]
    MONTHS = ["januar","februar","marts","april","maj","juni","juli","august","september","oktober","november","december"]
    return f"{DAYS[d.weekday()]} den {d.day}. {MONTHS[d.month - 1]}"

# -------------------- State --------------------
def _default_state():
    return {"enabled": True, "last_notice": {}, "disabled_since": {}, "disabled_by": {}}

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("enabled", True)
            data.setdefault("last_notice", {})
            data.setdefault("disabled_since", {})
            data.setdefault("disabled_by", {})
            return data
    except Exception:
        return _default_state()

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Kunne ikke gemme state:", e)

state = load_state()

# -------------------- Tekstformat --------------------
def offline_text(who: str, since_iso: str) -> str:
    try:
        since = dt.datetime.fromisoformat(since_iso)
    except Exception:
        since = dt.datetime.now(TZ)

    now = dt.datetime.now(TZ)
    elapsed = format_duration(now - since)
    stamp = since.astimezone(TZ).strftime("%d-%m-%Y kl. %H:%M:%S")

    lines = [
        ":no_entry: **Planday er ikke tilgængelig lige nu**",
        f"Blev deaktiveret af {who} — **{stamp}**",
        f"🕒 **Nedetid (live): {elapsed}**",
        "Systemet sender ikke automatisk beskeder, før det aktiveres igen.",
    ]
    return "\n".join(lines)

# -------------------- Hjælpefunktioner til beskeder --------------------
async def post_status_message(guild: discord.Guild, content: str) -> int | None:
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return None
    msg = await ch.send(content=content)
    return msg.id

async def edit_status_message(guild: discord.Guild, msg_id: int, content: str):
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return
    try:
        msg = await ch.fetch_message(msg_id)
        await msg.edit(content=content)
    except Exception:
        pass

async def delete_status_message_if_any(guild: discord.Guild):
    gid = str(guild.id)
    msg_id = state.get("last_notice", {}).get(gid)
    if not msg_id:
        return
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return
    try:
        msg = await ch.fetch_message(msg_id)
        await msg.delete()
    except Exception:
        pass
    state["last_notice"].pop(gid, None)
    state["disabled_since"].pop(gid, None)
    state["disabled_by"].pop(gid, None)
    save_state()

# -------------------- Nedetidsur --------------------
@tasks.loop(seconds=30)
async def downtime_updater():
    try:
        for guild in bot.guilds:
            gid = str(guild.id)
            msg_id = state.get("last_notice", {}).get(gid)
            since_iso = state.get("disabled_since", {}).get(gid)
            who = state.get("disabled_by", {}).get(gid)
            if not msg_id or not since_iso or not who:
                continue
            await edit_status_message(guild, msg_id, offline_text(who, since_iso))
    except Exception as e:
        print("[downtime_updater] fejl:", e)

# -------------------- Embed & UI --------------------
def build_embed(starttid: str, besked=None, img_url=None, data=None):
    today = dt.datetime.now(TZ).date()
    embed = discord.Embed(
        title=f"Dagens vagtplan for {dansk_dato(today)}",
        description="Husk og stemple ind hvad bil du kører i.",
        color=0x2b90d9,
    )
    embed.add_field(name="🕒 Starttid", value=f"{dansk_dato(today)} kl. {starttid}", inline=False)
    deltager_str = "\n".join(data.get("deltager", [])) if data else "Ingen endnu"
    senere_str = "\n".join(data.get("senere", [])) if data else "Ingen endnu"
    fravaer_str = "\n".join(data.get("fravaer", [])) if data else "Ingen endnu"
    disp_str = "\n".join(data.get("disp", [])) if data else "Ingen endnu"
    embed.add_field(name="✅ Deltager", value=deltager_str, inline=True)
    embed.add_field(name="🕓 Deltager senere", value=senere_str, inline=True)
    embed.add_field(name="❌ Fraværende", value=fravaer_str, inline=True)
    embed.add_field(name="🧭 Disponering", value=disp_str, inline=True)
    embed.add_field(name="🗒️ Besked", value=besked or "Ingen besked sat", inline=False)
    if img_url and img_url.startswith("http"):
        embed.set_image(url=img_url)
    embed.set_footer(text="Planday | Vagtplan")
    return embed

# -------------------- Slash Commands --------------------
@tree.command(name="vagtplan", description="Send dagens vagtplan med starttid, besked og billede", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan_cmd(interaction: discord.Interaction):
    if not state.get("enabled", True):
        await interaction.response.send_message("⛔ Planday er deaktiveret – aktiver først med /aktiver.", ephemeral=True)
        return
    await interaction.response.send_message("Denne kommando er under udbygning.", ephemeral=True)

@tree.command(name="deaktiver", description="Deaktiver automatisk Planday og vis status", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def deaktiver_cmd(interaction: discord.Interaction):
    if not state.get("enabled", True):
        await interaction.response.send_message("Planday er allerede deaktiveret.", ephemeral=True)
        return
    state["enabled"] = False
    gid = str(interaction.guild.id)
    since_iso = dt.datetime.now(TZ).isoformat()
    who = interaction.user.mention
    state["disabled_since"][gid] = since_iso
    state["disabled_by"][gid] = who
    save_state()
    text = offline_text(who, since_iso)
    msg_id = await post_status_message(interaction.guild, text)
    state["last_notice"][gid] = msg_id
    save_state()
    if not downtime_updater.is_running():
        downtime_updater.start()
    await interaction.response.send_message("🔴 Planday er nu **deaktiveret**.", ephemeral=True)

@tree.command(name="aktiver", description="Aktiver automatisk Planday igen", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def aktiver_cmd(interaction: discord.Interaction):
    if state.get("enabled", True):
        await interaction.response.send_message("Planday er allerede aktiveret.", ephemeral=True)
        return
    gid = str(interaction.guild.id)
    state["enabled"] = True
    total = "00:00:00"
    if gid in state["disabled_since"]:
        since = dt.datetime.fromisoformat(state["disabled_since"][gid])
        total = format_duration(dt.datetime.now(TZ) - since)
    await delete_status_message_if_any(interaction.guild)
    save_state()
    who = interaction.user.mention
    await post_status_message(interaction.guild, f":white_check_mark: Planday er **aktiveret igen** af {who}. Nedetid i alt: **{total}**.")
    await interaction.response.send_message("🟢 Planday er **aktiveret** igen.", ephemeral=True)

@tree.command(name="ping", description="Test at botten svarer", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)

@tree.command(name="sync", description="Tving sync", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def sync_cmd(interaction: discord.Interaction):
    gid = interaction.guild_id
    guild_obj = discord.Object(id=gid)
    await tree.sync(guild=guild_obj)
    cmds = await tree.fetch_commands(guild=guild_obj)
    names = ", ".join(c.name for c in cmds)
    await interaction.response.send_message(f"Synkroniseret: {names}", ephemeral=True)

@tree.command(name="cleanup_global", description="Fjern gamle globale commands", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def cleanup_global_cmd(interaction: discord.Interaction):
    tree.clear_commands(guild=None)
    await tree.sync()
    await interaction.response.send_message("Globale kommandoer ryddet.", ephemeral=True)

# -------------------- Auto-opgaver --------------------
@tasks.loop(time=dt.time(hour=DAILY_H, minute=DAILY_M, tzinfo=TZ))
async def daily_post():
    await bot.wait_until_ready()
    if not state.get("enabled", True):
        print("[AUTO] Skippet (deaktiveret)")
        return
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            continue
        besked = "Automatisk daglig vagtplan – god vagt i aften ☕"
        starttid = "19:30"
        embed = build_embed(starttid, besked, None, {"deltager": [], "senere": [], "fravaer": [], "disp": []})
        await ch.send(content="@everyone", embed=embed)
        print(f"[AUTO] Ny vagtplan sendt til {guild.name}")

@tasks.loop(time=dt.time(hour=0, minute=0, tzinfo=TZ))
async def midnight_cleanup():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            continue
        async for msg in ch.history(limit=50):
            if msg.author == bot.user:
                await msg.delete()
        print(f"[AUTO] Vagtplan slettet ved midnat i {guild.name}")

# -------------------- Start --------------------
@bot.event
async def on_ready():
    print(f"✅ Logget ind som {bot.user}")
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            tree.clear_commands(guild=None)
            await tree.sync()
            await tree.sync(guild=guild_obj)
            cmds = await tree.fetch_commands(guild=guild_obj)
            print("Guild-commands:", [c.name for c in cmds])
        else:
            await tree.sync()
            cmds = await tree.fetch_commands()
            print("Global-commands:", [c.name for c in cmds])
    except Exception as e:
        print("Fejl ved sync:", e)

    if not daily_post.is_running():
        daily_post.start()
    if not midnight_cleanup.is_running():
        midnight_cleanup.start()
    if not downtime_updater.is_running() and not state.get("enabled", True):
        downtime_updater.start()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN mangler i miljøvariablerne")
    bot.run(TOKEN)
