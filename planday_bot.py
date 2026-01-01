# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP
# /vagtplan: modal (starttid, besked, billede) + knapper (Deltager, Senere, Fraværende, Disponent)
# /admin: modal (valgfri besked) -> knapper (Aktiver/Deaktiver)
# + Admin får ÉN DM pr vagtplan, og den DM bliver opdateret (redigeret) ved ændringer.
# + Reminder: 30 minutter efter vagtplan er postet -> DM til dem der ikke har reageret (kun én gang).

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

# -------------------- Config loader --------------------
CONFIG_FILE = "config.json"

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Kunne ikke læse config.json:", e)
        return {}

cfg = load_config()

# -------------------- Konfiguration --------------------
TOKEN = os.getenv("DISCORD_TOKEN")

ROLE_DISP = cfg.get("role_disponent", "Disponent")
CHANNEL_NAME = cfg.get("channel_name", "🗓️┃planday-dagens-vagtplan")
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", cfg.get("staff_role_name", "Redder"))

ADMIN_DISCORD_ID = int(os.getenv("ADMIN_DISCORD_ID", str(cfg.get("admin_discord_id", "442403117414350848"))))

TZ = ZoneInfo(cfg.get("timezone", "Europe/Copenhagen"))

START_TIME_H = int(cfg.get("start_time_h", 19))
START_TIME_M = int(cfg.get("start_time_m", 30))

REMINDER_AFTER_POST_MINUTES = int(cfg.get("reminder_after_post_minutes", 30))
REMINDER_DM_TEXT = cfg.get(
    "reminder_dm_text",
    "⏰ Husk at reagere på dagens vagtplan (Deltager / Senere / Fraværende)."
)

def _parse_guild_id() -> Optional[int]:
    raw = os.getenv("DISCORD_GUILD_ID", "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        gid = int(cfg.get("guild_id", 0))
        return gid if gid > 0 else None
    except Exception:
        return None

GUILD_ID = _parse_guild_id()
STATE_FILE = "planday_state.json"

# -------------------- Intents & Client --------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # kræves for rolle-liste (mangler)
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# -------------------- State --------------------
# {
#   "enabled": true/false,
#   "last_notice": { guild_id: message_id },
#   "disabled_since": { guild_id: iso_timestamp },
#   "disabled_by": { guild_id: user_mention },
#   "note": { guild_id: str },
#   "admin_dm": { guild_id: { vagtplan_msg_id: admin_dm_msg_id } },
#   "current_vagtplan": {
#       guild_id: { "msg_id": int, "channel_id": int, "date": "YYYY-MM-DD", "created_at": "ISO" }
#   },
#   "reminder_sent": { guild_id: { vagtplan_msg_id: true/false } }
# }

def _default_state():
    return {
        "enabled": True,
        "last_notice": {},
        "disabled_since": {},
        "disabled_by": {},
        "note": {},
        "admin_dm": {},
        "current_vagtplan": {},
        "reminder_sent": {}
    }

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("enabled", True)
            data.setdefault("last_notice", {})
            data.setdefault("disabled_since", {})
            data.setdefault("disabled_by", {})
            data.setdefault("note", {})
            data.setdefault("admin_dm", {})
            data.setdefault("current_vagtplan", {})
            data.setdefault("reminder_sent", {})
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

def _today_iso() -> str:
    return dt.datetime.now(TZ).date().isoformat()

def _now() -> dt.datetime:
    return dt.datetime.now(TZ)

def _strip_mention(m: str) -> str:
    m = m.replace("<@!", "").replace("<@", "").replace(">", "")
    return m if m.isdigit() else ""

def _role_member_ids(guild: discord.Guild, role_name: str) -> set[int]:
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        return set()
    return {m.id for m in role.members if not m.bot}

def _names_from_ids(guild: discord.Guild, ids: set[int]) -> list[str]:
    out = []
    for uid in ids:
        mem = guild.get_member(uid)
        if mem:
            out.append(mem.display_name)
    return sorted(out, key=str.casefold)

# -------------------- Kanal helpers --------------------
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

# -------------------- Admin DM (send én gang, rediger senere) --------------------
def _get_admin_dm_id(guild_id: int, vagt_msg_id: int) -> Optional[int]:
    gid = str(guild_id)
    return state.get("admin_dm", {}).get(gid, {}).get(str(vagt_msg_id))

def _set_admin_dm_id(guild_id: int, vagt_msg_id: int, dm_msg_id: int):
    gid = str(guild_id)
    state.setdefault("admin_dm", {})
    state["admin_dm"].setdefault(gid, {})
    state["admin_dm"][gid][str(vagt_msg_id)] = dm_msg_id
    save_state()

# -------------------- Vagtplan data --------------------
registreringer = {}

def get_msg_data(msg_id):
    if msg_id not in registreringer:
        registreringer[msg_id] = {"deltager": [], "senere": [], "fravaer": [], "disp": []}
    return registreringer[msg_id]

def _compute_missing_ids(guild: discord.Guild, msg_id: int) -> set[int]:
    data = get_msg_data(msg_id)
    deltagere_ids = {int(_strip_mention(x)) for x in data["deltager"] if _strip_mention(x)}
    senere_ids    = {int(_strip_mention(x)) for x in data["senere"]   if _strip_mention(x)}
    fravaer_ids   = {int(_strip_mention(x)) for x in data["fravaer"]  if _strip_mention(x)}
    disp_ids      = {int(_strip_mention(x)) for x in data["disp"]     if _strip_mention(x)}

    required_ids = _role_member_ids(guild, STAFF_ROLE_NAME)
    if not required_ids:
        required_ids = {m.id for m in guild.members if not m.bot}

    responded_ids = deltagere_ids | senere_ids | fravaer_ids | disp_ids
    return required_ids - responded_ids

def build_admin_overview_text(guild: discord.Guild, msg_id: int) -> str:
    data = get_msg_data(msg_id)

    deltagere_ids = {int(_strip_mention(x)) for x in data["deltager"] if _strip_mention(x)}
    senere_ids    = {int(_strip_mention(x)) for x in data["senere"]   if _strip_mention(x)}
    fravaer_ids   = {int(_strip_mention(x)) for x in data["fravaer"]  if _strip_mention(x)}
    disp_ids      = {int(_strip_mention(x)) for x in data["disp"]     if _strip_mention(x)}

    required_ids = _role_member_ids(guild, STAFF_ROLE_NAME)
    if not required_ids:
        required_ids = {m.id for m in guild.members if not m.bot}

    responded_ids = deltagere_ids | senere_ids | fravaer_ids | disp_ids
    missing_ids = required_ids - responded_ids

    deltagere_names = _names_from_ids(guild, deltagere_ids)
    senere_names    = _names_from_ids(guild, senere_ids)
    fravaer_names   = _names_from_ids(guild, fravaer_ids)
    disp_names      = _names_from_ids(guild, disp_ids)
    missing_names   = _names_from_ids(guild, missing_ids)

    now = _now().strftime("%d-%m-%Y %H:%M:%S")

    lines = []
    lines.append(f"📌 **Vagtplan status** (opdateret {now})")
    lines.append(f"🆔 Vagtplan-besked: `{msg_id}`")
    lines.append(f"👥 Grundlag: **{STAFF_ROLE_NAME}** = {len(required_ids)}")
    lines.append("")
    lines.append(f"✅ Deltager: **{len(deltagere_ids)}**")
    lines.append("   " + (", ".join(deltagere_names) if deltagere_names else "Ingen endnu"))
    lines.append(f"🕓 Deltager senere: **{len(senere_ids)}**")
    lines.append("   " + (", ".join(senere_names) if senere_names else "Ingen endnu"))
    lines.append(f"❌ Fraværende: **{len(fravaer_ids)}**")
    lines.append("   " + (", ".join(fravaer_names) if fravaer_names else "Ingen endnu"))
    lines.append(f"🧭 Disponering: **{len(disp_ids)}**")
    lines.append("   " + (", ".join(disp_names) if disp_names else "Ingen endnu"))
    lines.append("")
    lines.append(f"⏳ Mangler at reagere: **{len(missing_ids)}**")
    lines.append("   " + (", ".join(missing_names) if missing_names else "Alle har reageret ✅"))
    return "\n".join(lines)

async def upsert_admin_overview_dm(guild: discord.Guild, vagt_msg_id: int):
    """Sender EN DM til admin pr vagtplan og opdaterer (redigerer) den ved ændringer."""
    try:
        admin_user = bot.get_user(ADMIN_DISCORD_ID) or await bot.fetch_user(ADMIN_DISCORD_ID)
        if not admin_user:
            return

        overview = build_admin_overview_text(guild, vagt_msg_id)[:1900]
        existing_id = _get_admin_dm_id(guild.id, vagt_msg_id)

        if existing_id:
            try:
                dm_msg = await admin_user.fetch_message(existing_id)
                await dm_msg.edit(content=overview)
                return
            except Exception:
                pass

        dm_msg = await admin_user.send(overview)
        _set_admin_dm_id(guild.id, vagt_msg_id, dm_msg.id)

    except Exception as e:
        print("[upsert_admin_overview_dm] fejl:", e)

# -------------------- Track "dagens vagtplan" --------------------
def _set_current_vagtplan(guild_id: int, channel_id: int, msg_id: int):
    gid = str(guild_id)
    state.setdefault("current_vagtplan", {})
    state["current_vagtplan"][gid] = {
        "msg_id": int(msg_id),
        "channel_id": int(channel_id),
        "date": _today_iso(),
        "created_at": _now().isoformat()
    }
    save_state()

def _get_current_vagtplan(guild_id: int) -> Optional[dict]:
    gid = str(guild_id)
    cur = state.get("current_vagtplan", {}).get(gid)
    if not cur:
        return None
    if cur.get("date") != _today_iso():
        return None
    return cur

def _reminder_sent(guild_id: int, vagt_msg_id: int) -> bool:
    gid = str(guild_id)
    return bool(state.get("reminder_sent", {}).get(gid, {}).get(str(vagt_msg_id), False))

def _mark_reminder_sent(guild_id: int, vagt_msg_id: int):
    gid = str(guild_id)
    state.setdefault("reminder_sent", {})
    state["reminder_sent"].setdefault(gid, {})
    state["reminder_sent"][gid][str(vagt_msg_id)] = True
    save_state()

async def _dm_missing_users(guild: discord.Guild, channel: discord.TextChannel, vagt_msg_id: int):
    missing_ids = _compute_missing_ids(guild, vagt_msg_id)
    if not missing_ids:
        return

    link = f"https://discord.com/channels/{guild.id}/{channel.id}/{vagt_msg_id}"
    text = f"{REMINDER_DM_TEXT}\n🔗 {link}"

    for uid in list(missing_ids):
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            if user:
                await user.send(text[:1900])
        except Exception:
            # DM kan være lukket – ignorer
            pass

# -------------------- Status-embeds --------------------
def build_offline_embed(who: str, since_iso: str, note: Optional[str]) -> discord.Embed:
    try:
        since = dt.datetime.fromisoformat(since_iso)
    except Exception:
        since = _now()
    now = _now()
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
    now = _now()
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

# -------------------- Vagtplan embeds --------------------
def build_vagtplan_embed_full(starttid: str, besked: str | None = None, img_url: str | None = None, data=None):
    today = _now().date()
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

        if interaction.guild:
            await upsert_admin_overview_dm(interaction.guild, interaction.message.id)

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

        if interaction.guild:
            await upsert_admin_overview_dm(interaction.guild, interaction.message.id)

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

async def do_deaktiver(inter: discord.Interaction, note: Optional[str]):
    if not state.get("enabled", True):
        await inter.followup.send("Planday er allerede deaktiveret.", ephemeral=True)
        return
    state["enabled"] = False
    gid = str(inter.guild.id)
    since_iso = _now().isoformat()
    who = inter.user.mention
    state["disabled_since"][gid] = since_iso
    state["disabled_by"][gid] = who
    state["note"][gid] = note
    save_state()

    embed = build_offline_embed(who, since_iso, note)
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
            since = _now()
        total = format_duration(_now() - since)

    who = inter.user.mention
    embed = build_online_embed(who, total, note)
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
    description="Åbn admin-skabelon (valgfri besked → vælg Aktiver/Deaktiver).",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None
)
@app_commands.checks.has_role(ROLE_DISP)
async def admin_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(AdminModal())

@tree.command(
    name="vagtplan",
    description="Send dagens vagtplan med starttid, besked og billede",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None
)
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

        # Slet gamle vagtplaner fra botten (behold evt. status)
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

        # Track dagens vagtplan + nulstil reminder-flag for denne
        _set_current_vagtplan(guild.id, ch.id, sent.id)
        gid = str(guild.id)
        state.setdefault("reminder_sent", {})
        state["reminder_sent"].setdefault(gid, {})
        state["reminder_sent"][gid][str(sent.id)] = False
        save_state()

        # admin DM (én besked der opdateres)
        await upsert_admin_overview_dm(guild, sent.id)

        await inter.response.send_message("✅ Vagtplan sendt med @everyone.", ephemeral=True)

    await interaction.response.send_modal(BeskedModal(after_modal))

@tree.command(
    name="status_dm",
    description="Opdater admin-DM med seneste vagtplan status",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None
)
@app_commands.checks.has_role(ROLE_DISP)
async def status_dm_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("Kan kun bruges i en server.", ephemeral=True)
        return
    cur = _get_current_vagtplan(interaction.guild.id)
    if not cur:
        await interaction.response.send_message("Ingen aktiv vagtplan i dag fundet.", ephemeral=True)
        return
    await upsert_admin_overview_dm(interaction.guild, int(cur["msg_id"]))
    await interaction.response.send_message("📩 Admin-DM er opdateret.", ephemeral=True)

@tree.command(
    name="ping",
    description="Test at botten svarer",
    guild=discord.Object(id=GUILD_ID) if GUILD_ID else None
)
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)

# -------------------- Reminder loop --------------------
@tasks.loop(seconds=60)
async def reminder_loop():
    await bot.wait_until_ready()
    if not state.get("enabled", True):
        return

    for guild in bot.guilds:
        cur = _get_current_vagtplan(guild.id)
        if not cur:
            continue

        vagt_msg_id = int(cur["msg_id"])
        if _reminder_sent(guild.id, vagt_msg_id):
            continue

        created_at_iso = cur.get("created_at")
        if not created_at_iso:
            continue

        try:
            created_at = dt.datetime.fromisoformat(created_at_iso)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=TZ)
        except Exception:
            continue

        # Kun efter X minutter
        minutes_since = int((_now() - created_at).total_seconds() // 60)
        if minutes_since < REMINDER_AFTER_POST_MINUTES:
            continue

        channel = guild.get_channel(int(cur["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue

        # Send reminder til dem der mangler (hvis nogen mangler)
        missing = _compute_missing_ids(guild, vagt_msg_id)
        if missing:
            await _dm_missing_users(guild, channel, vagt_msg_id)

        # Markér sendt uanset (så den ikke bliver ved)
        _mark_reminder_sent(guild.id, vagt_msg_id)

        # Opdater admin DM
        await upsert_admin_overview_dm(guild, vagt_msg_id)

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

    if not reminder_loop.is_running():
        reminder_loop.start()

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN mangler i miljøvariablerne")
    bot.run(TOKEN)




