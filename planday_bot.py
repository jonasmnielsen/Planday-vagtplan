# -*- coding: utf-8 -*-
# Planday | Vagtplan
# - Auto-post hver dag kl. (auto_post_hour:auto_post_minute) med knapper
# - Starttid vises i embed (auto_start_time)
# - Admin får ÉN DM pr vagtplan som opdateres (redigeres)
# - Reminder: X minutter FØR starttid -> DM til dem der ikke har reageret (kun én gang)

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

CONFIG_FILE = "config.json"
STATE_FILE = "planday_state.json"

def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Kunne ikke læse config.json:", e)
        return {}

cfg = load_config()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN mangler i miljøvariablerne")

ROLE_DISP = cfg.get("role_disponent", "Disponent")
CHANNEL_NAME = cfg.get("channel_name", "🗓️┃planday-dagens-vagtplan")
STAFF_ROLE_NAME = os.getenv("STAFF_ROLE_NAME", cfg.get("staff_role_name", "Redder"))

ADMIN_DISCORD_ID = int(os.getenv("ADMIN_DISCORD_ID", str(cfg.get("admin_discord_id", "442403117414350848"))))

TZ = ZoneInfo(cfg.get("timezone", "Europe/Copenhagen"))

AUTO_POST_H = int(cfg.get("auto_post_hour", 12))
AUTO_POST_M = int(cfg.get("auto_post_minute", 0))
AUTO_START_TIME = cfg.get("auto_start_time", "19:30").strip()
AUTO_MESSAGE = cfg.get("auto_message", "Automatisk vagtplan – husk at stemple ind hvad bil du kører i.")

REMINDER_BEFORE_START_MINUTES = int(cfg.get("reminder_before_start_minutes", 30))
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

# ---------- Discord client ----------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # kræves for rollemedlemmer (mangler)
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------- State ----------
# {
#   "enabled": true/false,
#   "admin_dm": { guild_id: { vagtplan_msg_id: admin_dm_msg_id } },
#   "current_vagtplan": { guild_id: { "msg_id": int, "channel_id": int, "date": "YYYY-MM-DD" } },
#   "reminder_sent_for_date": { guild_id: { "YYYY-MM-DD": true/false } }
# }

def _default_state():
    return {
        "enabled": True,
        "admin_dm": {},
        "current_vagtplan": {},
        "reminder_sent_for_date": {}
    }

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
            s.setdefault("enabled", True)
            s.setdefault("admin_dm", {})
            s.setdefault("current_vagtplan", {})
            s.setdefault("reminder_sent_for_date", {})
            return s
    except Exception:
        return _default_state()

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Kunne ikke gemme state:", e)

state = load_state()

# ---------- Helpers ----------
def now() -> dt.datetime:
    return dt.datetime.now(TZ)

def today_iso() -> str:
    return now().date().isoformat()

def dansk_dato(d: dt.date) -> str:
    DAYS = ["mandag","tirsdag","onsdag","torsdag","fredag","lørdag","søndag"]
    MONTHS = ["januar","februar","marts","april","maj","juni","juli","august","september","oktober","november","december"]
    return f"{DAYS[d.weekday()]} den {d.day}. {MONTHS[d.month - 1]}"

def parse_hhmm(hhmm: str) -> tuple[int, int]:
    parts = hhmm.strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return h, m

def start_dt_today() -> dt.datetime:
    h, m = parse_hhmm(AUTO_START_TIME)
    t = now().date()
    return dt.datetime(t.year, t.month, t.day, h, m, tzinfo=TZ)

def role_member_ids(guild: discord.Guild, role_name: str) -> set[int]:
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        return set()
    return {m.id for m in role.members if not m.bot}

def names_from_ids(guild: discord.Guild, ids: set[int]) -> list[str]:
    out = []
    for uid in ids:
        mem = guild.get_member(uid)
        if mem:
            out.append(mem.display_name)
    return sorted(out, key=str.casefold)

def get_admin_dm_id(guild_id: int, vagt_msg_id: int) -> Optional[int]:
    return state.get("admin_dm", {}).get(str(guild_id), {}).get(str(vagt_msg_id))

def set_admin_dm_id(guild_id: int, vagt_msg_id: int, dm_msg_id: int):
    gid = str(guild_id)
    state.setdefault("admin_dm", {})
    state["admin_dm"].setdefault(gid, {})
    state["admin_dm"][gid][str(vagt_msg_id)] = dm_msg_id
    save_state()

def set_current_vagtplan(guild_id: int, channel_id: int, msg_id: int):
    gid = str(guild_id)
    state.setdefault("current_vagtplan", {})
    state["current_vagtplan"][gid] = {"msg_id": int(msg_id), "channel_id": int(channel_id), "date": today_iso()}
    save_state()

def get_current_vagtplan(guild_id: int) -> Optional[dict]:
    cur = state.get("current_vagtplan", {}).get(str(guild_id))
    if not cur:
        return None
    if cur.get("date") != today_iso():
        return None
    return cur

def reminder_sent_today(guild_id: int) -> bool:
    gid = str(guild_id)
    d = today_iso()
    return bool(state.get("reminder_sent_for_date", {}).get(gid, {}).get(d, False))

def mark_reminder_sent_today(guild_id: int):
    gid = str(guild_id)
    d = today_iso()
    state.setdefault("reminder_sent_for_date", {})
    state["reminder_sent_for_date"].setdefault(gid, {})
    state["reminder_sent_for_date"][gid][d] = True
    save_state()

# ---------- Vagtplan registreringer ----------
registreringer: Dict[int, Dict[str, list]] = {}

def get_msg_data(msg_id: int):
    if msg_id not in registreringer:
        registreringer[msg_id] = {"deltager": [], "senere": [], "fravaer": [], "disp": []}
    return registreringer[msg_id]

def compute_missing_ids(guild: discord.Guild, msg_id: int) -> set[int]:
    data = get_msg_data(msg_id)

    def strip_to_id(mention: str) -> Optional[int]:
        s = mention.replace("<@!", "").replace("<@", "").replace(">", "")
        return int(s) if s.isdigit() else None

    responded = set()
    for k in ["deltager", "senere", "fravaer", "disp"]:
        for m in data[k]:
            uid = strip_to_id(m)
            if uid:
                responded.add(uid)

    required = role_member_ids(guild, STAFF_ROLE_NAME)
    if not required:
        required = {m.id for m in guild.members if not m.bot}

    return required - responded

# ---------- Admin DM (én besked der opdateres) ----------
def build_admin_overview_text(guild: discord.Guild, msg_id: int) -> str:
    data = get_msg_data(msg_id)

    def ids_from(cat: str) -> set[int]:
        out = set()
        for mention in data[cat]:
            s = mention.replace("<@!", "").replace("<@", "").replace(">", "")
            if s.isdigit():
                out.add(int(s))
        return out

    deltager_ids = ids_from("deltager")
    senere_ids = ids_from("senere")
    fravaer_ids = ids_from("fravaer")
    disp_ids = ids_from("disp")

    required = role_member_ids(guild, STAFF_ROLE_NAME)
    if not required:
        required = {m.id for m in guild.members if not m.bot}

    missing = required - (deltager_ids | senere_ids | fravaer_ids | disp_ids)

    stamp = now().strftime("%d-%m-%Y %H:%M:%S")

    return (
        f"📌 **Vagtplan status** (opdateret {stamp})\n"
        f"🆔 Vagtplan-besked: `{msg_id}`\n"
        f"👥 Grundlag: **{STAFF_ROLE_NAME}** = {len(required)}\n\n"
        f"✅ Deltager: **{len(deltager_ids)}**\n   {', '.join(names_from_ids(guild, deltager_ids)) if deltager_ids else 'Ingen endnu'}\n"
        f"🕓 Deltager senere: **{len(senere_ids)}**\n   {', '.join(names_from_ids(guild, senere_ids)) if senere_ids else 'Ingen endnu'}\n"
        f"❌ Fraværende: **{len(fravaer_ids)}**\n   {', '.join(names_from_ids(guild, fravaer_ids)) if fravaer_ids else 'Ingen endnu'}\n"
        f"🧭 Disponering: **{len(disp_ids)}**\n   {', '.join(names_from_ids(guild, disp_ids)) if disp_ids else 'Ingen endnu'}\n\n"
        f"⏳ Mangler at reagere: **{len(missing)}**\n   {', '.join(names_from_ids(guild, missing)) if missing else 'Alle har reageret ✅'}"
    )

async def upsert_admin_dm(guild: discord.Guild, vagt_msg_id: int):
    try:
        admin_user = bot.get_user(ADMIN_DISCORD_ID) or await bot.fetch_user(ADMIN_DISCORD_ID)
        if not admin_user:
            return

        content = build_admin_overview_text(guild, vagt_msg_id)[:1900]
        existing_id = get_admin_dm_id(guild.id, vagt_msg_id)

        if existing_id:
            try:
                dm_msg = await admin_user.fetch_message(existing_id)
                await dm_msg.edit(content=content)
                return
            except Exception:
                pass

        dm_msg = await admin_user.send(content)
        set_admin_dm_id(guild.id, vagt_msg_id, dm_msg.id)
    except Exception as e:
        print("[admin_dm] fejl:", e)

# ---------- Embed + Buttons ----------
def build_vagtplan_embed(starttid: str, besked: str, data: dict) -> discord.Embed:
    today = now().date()
    e = discord.Embed(
        title=f"Dagens vagtplan for {dansk_dato(today)}",
        description="Husk og stemple ind hvad bil du kører i.",
        color=0x2b90d9
    )
    e.add_field(name="🕒 Starttid", value=f"{dansk_dato(today)} kl. {starttid}", inline=False)
    e.add_field(name="✅ Deltager", value=("\n".join(data["deltager"]) if data["deltager"] else "Ingen endnu"), inline=True)
    e.add_field(name="🕓 Deltager senere", value=("\n".join(data["senere"]) if data["senere"] else "Ingen endnu"), inline=True)
    e.add_field(name="❌ Fraværende", value=("\n".join(data["fravaer"]) if data["fravaer"] else "Ingen endnu"), inline=True)
    e.add_field(name="🧭 Disponering", value=("\n".join(data["disp"]) if data["disp"] else "Ingen endnu"), inline=True)
    e.add_field(name="🗒️ Besked", value=besked or "Ingen besked sat", inline=False)
    e.set_footer(text="Planday | Vagtplan")
    return e

class VagtplanView(discord.ui.View):
    def __init__(self, starttid: str, besked: str):
        super().__init__(timeout=None)
        self.starttid = starttid
        self.besked = besked

    async def update_status(self, interaction: discord.Interaction, kategori: str):
        msg_id = interaction.message.id
        user_mention = interaction.user.mention
        data = get_msg_data(msg_id)

        # fjern fra alle kategorier
        for k in data.keys():
            if user_mention in data[k]:
                data[k].remove(user_mention)

        # tilføj til valgt kategori
        data[kategori].append(user_mention)

        embed = build_vagtplan_embed(self.starttid, self.besked, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("✅ Registreret", ephemeral=True)

        if interaction.guild:
            await upsert_admin_dm(interaction.guild, msg_id)

    @discord.ui.button(label="Deltager", style=discord.ButtonStyle.success, emoji="✅")
    async def btn_deltager(self, interaction: discord.Interaction, _):
        await self.update_status(interaction, "deltager")

    @discord.ui.button(label="Deltager senere", style=discord.ButtonStyle.primary, emoji="🕓")
    async def btn_senere(self, interaction: discord.Interaction, _):
        await self.update_status(interaction, "senere")

    @discord.ui.button(label="Fraværende", style=discord.ButtonStyle.danger, emoji="❌")
    async def btn_fravaer(self, interaction: discord.Interaction, _):
        await self.update_status(interaction, "fravaer")

    @discord.ui.button(label="Disponent", style=discord.ButtonStyle.secondary, emoji="🧭")
    async def btn_disponent(self, interaction: discord.Interaction, _):
        if not any(r.name == ROLE_DISP for r in getattr(interaction.user, "roles", [])):
            await interaction.response.send_message("Kun **Disponent** kan bruge denne knap.", ephemeral=True)
            return

        msg_id = interaction.message.id
        data = get_msg_data(msg_id)
        mention = interaction.user.mention

        if mention in data["disp"]:
            data["disp"].remove(mention)
        else:
            data["disp"].append(mention)

        embed = build_vagtplan_embed(self.starttid, self.besked, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("🧭 Opdateret", ephemeral=True)

        if interaction.guild:
            await upsert_admin_dm(interaction.guild, msg_id)

# ---------- Slash: ping + sync ----------
@tree.command(name="ping", description="Test at botten svarer", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
async def ping_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!", ephemeral=True)

@tree.command(name="sync", description="Tving slash-kommando sync", guild=discord.Object(id=GUILD_ID) if GUILD_ID else None)
@app_commands.checks.has_role(ROLE_DISP)
async def sync_cmd(interaction: discord.Interaction):
    guild_obj = discord.Object(id=interaction.guild_id)
    await tree.sync(guild=guild_obj)
    cmds = await tree.fetch_commands(guild=guild_obj)
    await interaction.response.send_message("Synk: " + ", ".join(c.name for c in cmds), ephemeral=True)

# ---------- Auto post hver dag kl. 12:00 ----------
@tasks.loop(time=dt.time(hour=AUTO_POST_H, minute=AUTO_POST_M, tzinfo=TZ))
async def auto_post():
    await bot.wait_until_ready()
    if not state.get("enabled", True):
        return

    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            continue

        # Slet gamle bot-beskeder i kanalen (så der kun ligger én)
        try:
            async for msg in ch.history(limit=100):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
        except Exception:
            pass

        data = {"deltager": [], "senere": [], "fravaer": [], "disp": []}
        embed = build_vagtplan_embed(AUTO_START_TIME, AUTO_MESSAGE, data)
        view = VagtplanView(AUTO_START_TIME, AUTO_MESSAGE)

        sent = await ch.send(content="@everyone", embed=embed, view=view)
        get_msg_data(sent.id)

        set_current_vagtplan(guild.id, ch.id, sent.id)

        # nulstil "reminder sendt" for i dag, så den kan sende kl. 19:00
        gid = str(guild.id)
        state.setdefault("reminder_sent_for_date", {})
        state["reminder_sent_for_date"].setdefault(gid, {})
        state["reminder_sent_for_date"][gid][today_iso()] = False
        save_state()

        await upsert_admin_dm(guild, sent.id)

# ---------- Reminder loop: 30 min før start ----------
@tasks.loop(seconds=60)
async def reminder_loop():
    await bot.wait_until_ready()
    if not state.get("enabled", True):
        return

    trigger_dt = start_dt_today() - dt.timedelta(minutes=REMINDER_BEFORE_START_MINUTES)
    # Vi sender når "nu" er på/efter trigger, men kun én gang pr dag
    for guild in bot.guilds:
        cur = get_current_vagtplan(guild.id)
        if not cur:
            continue
        if reminder_sent_today(guild.id):
            continue
        if now() < trigger_dt:
            continue

        channel = guild.get_channel(int(cur["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue

        vagt_msg_id = int(cur["msg_id"])
        missing = compute_missing_ids(guild, vagt_msg_id)

        if missing:
            link = f"https://discord.com/channels/{guild.id}/{channel.id}/{vagt_msg_id}"
            text = f"{REMINDER_DM_TEXT}\n🔗 {link}"

            for uid in list(missing):
                try:
                    user = bot.get_user(uid) or await bot.fetch_user(uid)
                    if user:
                        await user.send(text[:1900])
                except Exception:
                    pass

        mark_reminder_sent_today(guild.id)
        await upsert_admin_dm(guild, vagt_msg_id)

# ---------- Errors ----------
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

# ---------- Start ----------
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
            print("Global commands synced.")
    except Exception as e:
        print("Fejl ved sync:", e)

    if not auto_post.is_running():
        auto_post.start()
    if not reminder_loop.is_running():
        reminder_loop.start()

bot.run(TOKEN)

