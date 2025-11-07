# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP
# /deaktiver [besked]  -> slår auto fra, poster status-EMBED m. live-ur, rydder kanalen (beholder status)
# /aktiver   [besked]  -> slår auto til, poster info-EMBED, rydder kanalen (beholder info)
# Auto vagtplan kl. 12, oprydning ved midnat (respekterer on/off)
# State i planday_state.json
# Guild-sync for instant slash-commands (brug DISCORD_GUILD_ID)

import os
import json
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Optional

import discord
from discord import app_commands
from discord.ext import tasks

# (valgfrit) .env support
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# -------------------- Konfiguration --------------------
TOKEN = os.getenv("DISCORD_TOKEN")
ROLE_DISP = "Disponent"
CHANNEL_NAME = "🗓️┃planday-dagens-vagtplan"

TZ = ZoneInfo("Europe/Copenhagen")
DAILY_H, DAILY_M = 12, 0

def _parse_guild_id() -> Optional[int]:
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

# -------------------- State --------------------
# {
#   "enabled": true/false,
#   "last_notice": { guild_id: message_id },
#   "disabled_since": { guild_id: iso_timestamp },
#   "disabled_by": { guild_id: user_mention },
#   "note": { guild_id: str }
# }

def _default_state():
    return {"enabled": True, "last_notice": {}, "disabled_since": {}, "disabled_by": {}, "note": {}}

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("enabled", True)
            data.setdefault("last_notice", {})
            data.setdefault("disabled_since", {})
            data.setdefault("disabled_by", {})
            data.setdefault("note", {})
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

# -------------------- Hjælpere --------------------
def format_duration(delta: dt.timedelta) -> str:
    secs = int(delta.total_seconds())
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"{h:02}:{m:02}:{s:02}"

def dansk_dato(d: dt.date) -> str:
    DAYS = ["mandag","tirsdag","onsdag","torsdag","fredag","lørdag","søndag"]
    MONTHS = ["januar","februar","marts","april","maj","juni","juli","august","september","oktober","november","december"]
    return f"{DAYS[d.weekday()]} den {d.day}. {MONTHS[d.month - 1]}"

async def cleanup_channel_keep_one(guild: discord.Guild, keep_message_id: int):
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return
    async for msg in ch.history(limit=500):
        if msg.id == keep_message_id or msg.pinned:
            continue
        try:
            await msg.delete()
        except Exception:
            pass

async def post_message_embed(guild: discord.Guild, embed: discord.Embed) -> Optional[int]:
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return None
    m = await ch.send(embed=embed)
    return m.id

async def edit_message_embed(guild: discord.Guild, msg_id: int, embed: discord.Embed):
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return
    try:
        msg = await ch.fetch_message(msg_id)
        await msg.edit(embed=embed)
    except Exception:
        pass

async def delete_status_message_if_any(guild: discord.Guild):
    gid = str(guild.id)
    msg_id = state.get("last_notice", {}).get(gid)
    if msg_id:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if ch:
            try:
                msg = await ch.fetch_message(msg_id)
                await msg.delete()
            except Exception:
                pass
    state["last_notice"].pop(gid, None)
    state["disabled_since"].pop(gid, None)
    state["disabled_by"].pop(gid, None)
    state["note"].pop(gid, None)
    save_state()

# -------------------- Status-embeds --------------------
def build_offline_embed(who: str, since_iso: str, note: Optional[str]) -> discord.Embed:
    try:
        since = dt.datetime.fromisoformat(since_iso)
    except Exception:
        since = dt.datetime.now(TZ)
    now = dt.datetime.now(TZ)
    elapsed = format_duration(now - since)
    stamp = since.astimezone(TZ).strftime("%d-%m-%Y kl. %H:%M:%S")

    e = discord.Embed(
        title="⛔ Planday er ikke tilgængelig lige nu",
        color=discord.Color.red(),
        timestamp=now
    )
    e.add_field(name="Deaktiveret af", value=who, inline=True)
    e.add_field(name="Siden", value=f"**{stamp}**", inline=True)
    e.add_field(name="Nedetid (live)", value=f"**{elapsed}**", inline=False)
    if note:
        e.add_field(name="Besked", value=note, inline=False)
    e.set_footer(text="Systemet sender ikke automatisk beskeder, før det aktiveres igen.")
    return e

def build_online_embed(who: str, total: str, note: Optional[str]) -> discord.Embed:
    now = dt.datetime.now(TZ)
    e = discord.Embed(
        title="✅ Planday er aktiveret igen",
        description=f"Aktiveret af {who}",
        color=discord.Color.green(),
        timestamp=now
    )
    e.add_field(name="Nedetid i alt", value=f"**{total}**", inline=False)
    if note:
        e.add_field(name="Besked", value=note, inline=False)
    e.set_footer(text="Planday | Vagtplan")
    return e

# -------------------- Vagtplan embed (simpel auto) --------------------
def build_vagtplan_embed():
    today = dt.datetime.now(TZ).date()
    embed = discord.Embed(
        title=f"Dagens vagtplan for {dansk_dato(today)}",
        description="Husk og stemple ind hvad bil du kører i.",
        color=0x2b90d9,
    )
    embed.add_field(name="🕒 Starttid", value=f"{dansk_dato(today)} kl. 19:30", inline=False)
    embed.add_field(name="✅ Deltager", value="Ingen endnu", inline=True)
    embed.add_field(name="🕓 Deltager senere", value="Ingen endnu", inline=True)
    embed.add_field(name="❌ Fraværende", value="Ingen endnu", inline=True)
    embed.add_field(name="🧭 Disponering", value="Ingen endnu", inline=True)
    embed.add_field(name="🗒️ Besked", value="Automatisk daglig vagtplan – god vagt i aften ☕", inline=False)
    embed.set_footer(text="Planday | Vagtplan")
    return embed

# -------------------- Nedetidsur (opdater embed hvert 30s) --------------------
@tasks.loop(seconds=30)
async def downtime_updater():
    try:
        for guild in bot.guilds:
            gid = str(guild.id)
            msg_id = state.get("last_notice", {}).get(gid)
            since_iso = state.get("disabled_since", {}).get(gid)
            who = state.get("disabled_by", {}).get(gid)
            note = state.get("note", {}).get(gid)
            if not msg_id or not since_iso or not who:
                continue
            embed = build_offline_embed(who, since_iso, note)
            await edit_message_embed(guild, msg_id, embed)
    except Exception as e:
        print("[downtime_updater] fejl:", e)

# -------------------- Slash Commands --------------------
@tree.command(
    name="deaktiver",
    description="Deaktiver automatisk Planday-udsendelse og vis status med live ur (valgfri besked).",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.describe(besked="Valgfri besked der vises i status-embedden")
@app_commands.checks.has_role(ROLE_DISP)
async def deaktiver_cmd(interaction: discord.Interaction, besked: Optional[str] = None):
    if not state.get("enabled", True):
        await interaction.response.send_message("Planday er allerede deaktiveret.", ephemeral=True)
        return

    state["enabled"] = False
    gid = str(interaction.guild.id)
    since_iso = dt.datetime.now(TZ).isoformat()
    who = interaction.user.mention
    state["disabled_since"][gid] = since_iso
    state["disabled_by"][gid] = who
    state["note"][gid] = besked.strip() if besked else None
    save_state()

    embed = build_offline_embed(who, since_iso, state["note"][gid])
    msg_id = await post_message_embed(interaction.guild, embed)

    if msg_id:
        await cleanup_channel_keep_one(interaction.guild, msg_id)

    state["last_notice"][gid] = msg_id
    save_state()

    if not downtime_updater.is_running():
        downtime_updater.start()

    await interaction.response.send_message("🔴 Planday er nu **deaktiveret**.", ephemeral=True)

@tree.command(
    name="aktiver",
    description="Aktiver automatisk Planday-udsendelse igen (valgfri besked).",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.describe(besked="Valgfri besked der vises i aktiverings-embedden")
@app_commands.checks.has_role(ROLE_DISP)
async def aktiver_cmd(interaction: discord.Interaction, besked: Optional[str] = None):
    if state.get("enabled", True):
        await interaction.response.send_message("Planday er allerede aktiveret.", ephemeral=True)
        return

    gid = str(interaction.guild.id)
    state["enabled"] = True

    total = "00:00:00"
    if gid in state["disabled_since"]:
        try:
            since = dt.datetime.fromisoformat(state["disabled_since"][gid])
        except Exception:
            since = dt.datetime.now(TZ)
        total = format_duration(dt.datetime.now(TZ) - since)

    who = interaction.user.mention
    embed = build_online_embed(who, total, besked.strip() if besked else None)

    msg_id = await post_message_embed(interaction.guild, embed)

    if msg_id:
        await cleanup_channel_keep_one(interaction.guild, msg_id)

    state["last_notice"][gid] = msg_id
    state["disabled_since"].pop(gid, None)
    state["disabled_by"].pop(gid, None)
    state["note"].pop(gid, None)
    save_state()

    await interaction.response.send_message("🟢 Planday er **aktiveret** igen.", ephemeral=True)

@tree.command(name="vagtplan", description="Send en simpel vagtplan (demo).",
              guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan_cmd(interaction: discord.Interaction):
    if not state.get("enabled", True):
        await interaction.response.send_message("⛔ Planday er deaktiveret – aktiver først med /aktiver.", ephemeral=True)
        return
    ch = discord.utils.get(interaction.guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        await interaction.response.send_message(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
        return
    gid = str(interaction.guild.id)
    keep_id = state.get("last_notice", {}).get(gid)
    async for msg in ch.history(limit=50):
        if msg.author == bot.user and msg.id != keep_id:
            try:
                await msg.delete()
            except Exception:
                pass
    await ch.send(content="@everyone", embed=build_vagtplan_embed())
    await interaction.response.send_message("✅ Vagtplan sendt med @everyone.", ephemeral=True)

@tree.command(name="ping", description="Test at botten svarer",
              guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)

@tree.command(name="sync", description="Tving slash-kommando sync",
              guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def sync_cmd(interaction: discord.Interaction):
    gid = interaction.guild_id
    guild_obj = discord.Object(id=gid)
    await tree.sync(guild=guild_obj)
    cmds = await tree.fetch_commands(guild=guild_obj)
    await interaction.response.send_message("Synk: " + ", ".join(c.name for c in cmds), ephemeral=True)

@tree.command(name="cleanup_global", description="Fjern gamle globale commands",
              guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
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
        gid = str(guild.id)
        keep_id = state.get("last_notice", {}).get(gid)
        async for msg in ch.history(limit=50):
            if msg.author == bot.user and msg.id != keep_id:
                try:
                    await msg.delete()
                except Exception:
                    pass
        await ch.send(content="@everyone", embed=build_vagtplan_embed())
        print(f"[AUTO] Ny vagtplan sendt til {guild.name}")

@tasks.loop(time=dt.time(hour=0, minute=0, tzinfo=TZ))
async def midnight_cleanup():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            continue
        gid = str(guild.id)
        keep_id = state.get("last_notice", {}).get(gid)
        async for msg in ch.history(limit=200):
            if msg.author == bot.user and msg.id != keep_id:
                try:
                    await msg.delete()
                except Exception:
                    pass
        print(f"[AUTO] Vagtplan slettet ved midnat i {guild.name}")

# -------------------- Error-handling --------------------
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        if isinstance(error, app_commands.errors.MissingRole):
            msg = f"Du mangler rollen **{ROLE_DISP}** for at bruge denne kommando."
        else:
            msg = f"Fejl: {type(error).__name__}: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

# -------------------- Start --------------------
@bot.event
async def on_ready():
    print(f"✅ Logget ind som {bot.user}")
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            # undgå dubletter: tøm globalt, sync kun til guild
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
    if not state.get("enabled", True) and not downtime_updater.is_running():
        downtime_updater.start()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN mangler i miljøvariablerne")
    bot.run(TOKEN)
