# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP
# /vagtplan (som før): modal (starttid, besked, billede) + knapper (Deltager, Senere, Fraværende, Disponent)
# /admin: modal (valgfri besked) -> knapper (Aktiver/Deaktiver)
# Deaktiver/Aktiver: rydder kanal (beholder kun status/aktiverings-embed), live nedetidsur, auto vagtplan kl. 12, nightly cleanup
# State i planday_state.json. Kræver rollen "Disponent". Ingen billedfunktion i status-embeds.

import os
import json
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Optional, Dict

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

# Midlertidige noter pr. bruger (fra /admin modal til knapper)
temp_notes: Dict[int, Optional[str]] = {}

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

async def edit_message_embed(guild: discord.Guild, msg_id: int, embed: discord.Embed):
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return
    try:
        msg = await ch.fetch_message(msg_id)
        await msg.edit(embed=embed)
    except Exception:
        pass

async def post_message_embed(guild: discord.Guild, embed: discord.Embed) -> Optional[int]:
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return None
    m = await ch.send(embed=embed)
    return m.id

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

# -------------------- Vagtplan (som før) --------------------
registreringer = {}

def get_msg_data(msg_id):
    if msg_id not in registreringer:
        registreringer[msg_id] = {"deltager": [], "senere": [], "fravaer": [], "disp": []}
    return registreringer[msg_id]

def build_vagtplan_embed_full(starttid: str, besked: str | None = None, img_url: str | None = None, data=None):
    today = dt.datetime.now(TZ).date()
    embed = discord.Embed(
        title=f"Dagens vagtplan for {dansk_dato(today)}",
        description="Husk og stemple ind hvad bil du kører i.",
        color=0x2b90d9
    )
    embed.add_field(name="🕒 Starttid", value=f"{dansk_dato(today)} kl. {starttid}", inline=False)

    deltager_str = "\n".join(data["deltager"]) if data and data["deltager"] else "Ingen endnu"
    senere_str = "\n".join(data["senere"]) if data and data["senere"] else "Ingen endnu"
    fravaer_str = "\n".join(data["fravaer"]) if data and data["fravaer"] else "Ingen endnu"
    disp_str = "\n".join(data["disp"]) if data and data["disp"] else "Ingen endnu"

    embed.add_field(name="✅ Deltager", value=deltager_str, inline=True)
    embed.add_field(name="🕓 Deltager senere", value=senere_str, inline=True)
    embed.add_field(name="❌ Fraværende", value=fravaer_str, inline=True)
    embed.add_field(name="🧭 Disponering", value=disp_str, inline=True)

    embed.add_field(name="🗒️ Besked", value=besked if besked else "Ingen besked sat", inline=False)
    embed.set_footer(text="Planday | Vagtplan")

    if img_url and img_url.startswith("http"):
        embed.set_image(url=img_url)

    return embed

class VagtplanView(discord.ui.View):
    def __init__(self, starttid, besked=None, img_url=None):
        super().__init__(timeout=None)
        self.starttid = starttid
        self.besked = besked
        self.img_url = img_url

    async def update_status(self, interaction: discord.Interaction, kategori: str):
        msg_id = interaction.message.id
        user_mention = interaction.user.mention
        data = get_msg_data(msg_id)
        for k in data.keys():
            if user_mention in data[k]:
                data[k].remove(user_mention)
        if kategori:
            data[kategori].append(user_mention)

        embed = build_vagtplan_embed_full(self.starttid, self.besked, self.img_url, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"✅ Registreret som **{kategori}**", ephemeral=True)

    @discord.ui.button(label="Deltager", style=discord.ButtonStyle.success, emoji="✅")
    async def deltager(self, interaction: discord.Interaction, _):
        await self.update_status(interaction, "deltager")

    @discord.ui.button(label="Deltager senere", style=discord.ButtonStyle.primary, emoji="🕓")
    async def senere(self, interaction: discord.Interaction, _):
        await self.update_status(interaction, "senere")

    @discord.ui.button(label="Fraværende", style=discord.ButtonStyle.danger, emoji="❌")
    async def fravaer(self, interaction: discord.Interaction, _):
        await self.update_status(interaction, "fravaer")

    @discord.ui.button(label="Disponent", style=discord.ButtonStyle.secondary, emoji="🧭")
    async def disponent(self, interaction: discord.Interaction, _):
        member = interaction.user
        if not any(r.name == ROLE_DISP for r in member.roles):
            await interaction.response.send_message("Kun **Disponent** kan bruge denne knap.", ephemeral=True)
            return
        msg_id = interaction.message.id
        data = get_msg_data(msg_id)
        mention = interaction.user.mention
        if mention in data["disp"]:
            data["disp"].remove(mention)
        else:
            data["disp"].append(mention)
        embed = build_vagtplan_embed_full(self.starttid, self.besked, self.img_url, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("🧭 Vagtplan opdateret ✅", ephemeral=True)

class BeskedModal(discord.ui.Modal, title="Opret dagens vagtplan"):
    starttid = discord.ui.TextInput(
        label="Starttid (fx 19:30)",
        placeholder="Skriv klokkeslæt her",
        required=True,
        max_length=10
    )
    besked = discord.ui.TextInput(
        label="Besked (valgfrit)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300
    )
    billede = discord.ui.TextInput(
        label="Link til billede (valgfrit)",
        placeholder="Indsæt link til et billede (fx https://i.imgur.com/...png)",
        required=False,
        max_length=400
    )
    def __init__(self, cb):
        super().__init__()
        self._cb = cb
    async def on_submit(self, interaction: discord.Interaction):
        await self._cb(interaction, str(self.starttid), str(self.besked), str(self.billede))

# -------------------- Admin: modal + knapper --------------------
def build_vagtplan_embed_auto():
    # enkel auto-embed til daily_post
    today = dt.datetime.now(TZ).date()
    e = discord.Embed(
        title=f"Dagens vagtplan for {dansk_dato(today)}",
        description="Husk og stemple ind hvad bil du kører i.",
        color=0x2b90d9,
    )
    e.add_field(name="🕒 Starttid", value=f"{dansk_dato(today)} kl. 19:30", inline=False)
    e.add_field(name="✅ Deltager", value="Ingen endnu", inline=True)
    e.add_field(name="🕓 Deltager senere", value="Ingen endnu", inline=True)
    e.add_field(name="❌ Fraværende", value="Ingen endnu", inline=True)
    e.add_field(name="🧭 Disponering", value="Ingen endnu", inline=True)
    e.add_field(name="🗒️ Besked", value="Automatisk daglig vagtplan – god vagt i aften ☕", inline=False)
    e.set_footer(text="Planday | Vagtplan")
    return e

def build_admin_offline_embed(who: str, since_iso: str, note: Optional[str]) -> discord.Embed:
    return build_offline_embed(who, since_iso, note)

def build_admin_online_embed(who: str, total: str, note: Optional[str]) -> discord.Embed:
    return build_online_embed(who, total, note)

class AdminActionView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: int = 120):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Kun den der åbnede panelet kan bruge disse knapper.", ephemeral=True)
            return False
        return True
    @discord.ui.button(label="Aktiver", style=discord.ButtonStyle.success, emoji="🟢")
    async def btn_activate(self, interaction: discord.Interaction, _):
        note = temp_notes.pop(self.owner_id, None)
        await interaction.response.defer(ephemeral=True, thinking=False)
        await do_aktiver(interaction, note)
    @discord.ui.button(label="Deaktiver", style=discord.ButtonStyle.danger, emoji="🔴")
    async def btn_deactivate(self, interaction: discord.Interaction, _):
        note = temp_notes.pop(self.owner_id, None)
        await interaction.response.defer(ephemeral=True, thinking=False)
        await do_deaktiver(interaction, note)

class AdminModal(discord.ui.Modal, title="Admin: Valgfri besked"):
    besked = discord.ui.TextInput(
        label="Besked (valgfrit)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300,
        placeholder="Skriv en kort besked (valgfrit)"
    )
    async def on_submit(self, interaction: discord.Interaction):
        note = str(self.besked).strip() if self.besked else None
        temp_notes[interaction.user.id] = note
        view = AdminActionView(owner_id=interaction.user.id)
        await interaction.response.send_message("Vælg handling for Planday:", view=view, ephemeral=True)

# -------------------- Handlers for actions --------------------
async def do_deaktiver(inter: discord.Interaction, note: Optional[str]):
    if not state.get("enabled", True):
        await inter.followup.send("Planday er allerede deaktiveret.", ephemeral=True)
        return
    state["enabled"] = False
    gid = str(inter.guild.id)
    since_iso = dt.datetime.now(TZ).isoformat()
    who = inter.user.mention
    state["disabled_since"][gid] = since_iso
    state["disabled_by"][gid] = who
    state["note"][gid] = note
    save_state()
    embed = build_admin_offline_embed(who, since_iso, note)
    ch = discord.utils.get(inter.guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        await inter.followup.send(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
        return
    msg = await ch.send(embed=embed)
    await cleanup_channel_keep_one(inter.guild, msg.id)
    state["last_notice"][gid] = msg.id
    save_state()
    if not downtime_updater.is_running():
        downtime_updater.start()
    await inter.followup.send("🔴 Planday er nu **deaktiveret**.", ephemeral=True)

async def do_aktiver(inter: discord.Interaction, note: Optional[str]):
    if state.get("enabled", True):
        await inter.followup.send("Planday er allerede aktiveret.", ephemeral=True)
        return
    gid = str(inter.guild.id)
    state["enabled"] = True
    total = "00:00:00"
    if gid in state["disabled_since"]:
        try:
            since = dt.datetime.fromisoformat(state["disabled_since"][gid])
        except Exception:
            since = dt.datetime.now(TZ)
        total = format_duration(dt.datetime.now(TZ) - since)
    who = inter.user.mention
    embed = build_admin_online_embed(who, total, note)
    ch = discord.utils.get(inter.guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        await inter.followup.send(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
        return
    msg = await ch.send(embed=embed)
    await cleanup_channel_keep_one(inter.guild, msg.id)
    state["last_notice"][gid] = msg.id
    state["disabled_since"].pop(gid, None)
    state["disabled_by"].pop(gid, None)
    state["note"].pop(gid, None)
    save_state()
    await inter.followup.send("🟢 Planday er **aktiveret** igen.", ephemeral=True)

# -------------------- Slash Commands --------------------
@tree.command(
    name="admin",
    description="Åbn admin-skabelon (indtast valgfri besked → vælg Aktiver/Deaktiver).",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def admin_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(AdminModal())

@tree.command(name="vagtplan", description="Send dagens vagtplan med starttid, besked og billede",
              guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan_cmd(interaction: discord.Interaction):
    if not state.get("enabled", True):
        await interaction.response.send_message("⛔ Planday er deaktiveret – aktiver via /admin.", ephemeral=True)
        return

    async def after_modal(inter: discord.Interaction, starttid: str, besked: str | None, billede: str | None):
        guild = inter.guild
        if guild is None:
            await inter.response.send_message("Kan kun bruges i en server.", ephemeral=True)
            return

        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            await inter.response.send_message(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
            return

        # Slet gamle vagtplaner fra botten (behold evt. keep/status hvis sat – men når vi er aktiveret burde den være væk)
        gid = str(guild.id)
        keep_id = state.get("last_notice", {}).get(gid)
        async for msg in ch.history(limit=50):
            if msg.author == bot.user and msg.id != keep_id:
                try:
                    await msg.delete()
                except Exception:
                    pass

        embed = build_vagtplan_embed_full(starttid, besked, billede, get_msg_data("ny"))
        view = VagtplanView(starttid, besked, billede)
        sent = await ch.send(content="@everyone", embed=embed, view=view)
        get_msg_data(sent.id)
        await inter.response.send_message("✅ Vagtplan sendt med @everyone.", ephemeral=True)

    await interaction.response.send_modal(BeskedModal(after_modal))

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
        embed = build_vagtplan_embed_auto()
        await ch.send(content="@everyone", embed=embed, view=None)
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

