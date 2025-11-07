# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP (med /aktiver & /deaktiver + live nedetidsur)
# Dansk version med starttid, besked, billede, auto-post kl. 12 og auto-slet kl. 00:00
# + Toggle-kommandoer til at aktivere/deaktivere automatisk udsendelse og statusbesked med live ur,
#   som viser hvor længe systemet har været deaktiveret. Uret opdateres løbende mens det er deaktiveret,
#   og ved aktivering vises den samlede nedetid.

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
DAILY_H = 12
DAILY_M = 0

STATE_FILE = "planday_state.json"  # persisterer aktiveret/deaktiveret, sidste statusbesked-id, og nedetidsstart pr. guild

# -------------------- Intents & Client --------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# -------------------- State --------------------
# Struktur:
# {
#   "enabled": true/false,
#   "last_notice": { guild_id(str): message_id(int) },
#   "disabled_since": { guild_id(str): iso_timestamp(str) },
# }

def _default_state():
    return {"enabled": True, "last_notice": {}, "disabled_since": {}}

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return _default_state()
            data.setdefault("enabled", True)
            data.setdefault("last_notice", {})
            data.setdefault("disabled_since", {})
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
    total = int(delta.total_seconds())
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def post_status_message(guild: discord.Guild, content: str) -> int | None:
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return None
    msg = await ch.send(content=content)
    return msg.id

async def edit_status_message(guild: discord.Guild, message_id: int, new_content: str):
    ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
    if not ch:
        return
    try:
        m = await ch.fetch_message(message_id)
    except Exception:
        m = None
    if m:
        try:
            await m.edit(content=new_content)
        except Exception:
            pass

async def delete_status_message_if_any(guild: discord.Guild):
    try:
        gid = str(guild.id)
        last_id = state.get("last_notice", {}).get(gid)
        if not last_id:
            return
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            return
        try:
            m = await ch.fetch_message(last_id)
        except Exception:
            m = None
        if m:
            await m.delete()
        # ryd referencer
        state["last_notice"].pop(gid, None)
        state["disabled_since"].pop(gid, None)
        save_state()
    except Exception as e:
        print("Fejl ved sletning af statusbesked:", e)

# -------------------- Dansk datoformat --------------------
DAYS = ["mandag","tirsdag","onsdag","torsdag","fredag","lørdag","søndag"]
MONTHS = ["januar","februar","marts","april","maj","juni","juli","august","september","oktober","november","december"]

def dansk_dato(d: dt.date) -> str:
    return f"{DAYS[d.weekday()]} den {d.day}. {MONTHS[d.month - 1]}"

# -------------------- Gem registreringer --------------------
registreringer = {}

def get_msg_data(msg_id):
    if msg_id not in registreringer:
        registreringer[msg_id] = {"deltager": [], "senere": [], "fravaer": [], "disp": []}
    return registreringer[msg_id]

# -------------------- Embed opsætning --------------------
def build_embed(starttid: str, besked: str | None = None, img_url: str | None = None, data=None):
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

# -------------------- Knapper --------------------
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

        embed = build_embed(self.starttid, self.besked, self.img_url, data)
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
        embed = build_embed(self.starttid, self.besked, self.img_url, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("🧭 Vagtplan opdateret ✅", ephemeral=True)

# -------------------- Modal --------------------
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

# -------------------- Slash-kommando: manuel vagtplan --------------------
@tree.command(name="vagtplan", description="Send dagens vagtplan med starttid, besked og billede")
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan_cmd(interaction: discord.Interaction):
    if not state.get("enabled", True):
        await interaction.response.send_message("⛔ Planday er deaktiveret – aktiver først med /aktiver.", ephemeral=True)
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

        async for msg in ch.history(limit=10):
            if msg.author == bot.user:
                await msg.delete()

        embed = build_embed(starttid, besked, billede, get_msg_data("ny"))
        view = VagtplanView(starttid, besked, billede)
        sent = await ch.send(content="@everyone", embed=embed, view=view)
        get_msg_data(sent.id)
        await inter.response.send_message("✅ Vagtplan sendt med @everyone.", ephemeral=True)

    await interaction.response.send_modal(BeskedModal(after_modal))

# -------------------- Status-tekster --------------------

def offline_text(who: str, since_iso: str) -> str:
    try:
        since = dt.datetime.fromisoformat(since_iso)
    except Exception:
        since = dt.datetime.now(TZ)
    now = dt.datetime.now(TZ)
    elapsed = format_duration(now - since)
    stamp = since.astimezone(TZ).strftime("%d-%m-%Y kl. %H:%M:%S")
    return (
        f":no_entry: **Planday er ikke tilgængelig lige nu**\n"
        f"Blev deaktiveret af {who} **{stamp}**.\n"
        f"⏱️ **Nedetid (live): {elapsed}**\n"
        f"Systemet sender ikke automatisk beskeder, før det aktiveres igen."
    )

# -------------------- Slash-kommandoer: /aktiver & /deaktiver --------------------
@tree.command(name="deaktiver", description="Deaktiver automatisk Planday-udsendelse og vis status med live ur")
@app_commands.checks.has_role(ROLE_DISP)
async def deaktiver_cmd(interaction: discord.Interaction):
    if not state.get("enabled", True):
        await interaction.response.send_message("Planday er allerede deaktiveret.", ephemeral=True)
        return

    state["enabled"] = False
    gid = str(interaction.guild.id) if interaction.guild else None
    since_iso = dt.datetime.now(TZ).isoformat()
    if gid:
        state.setdefault("disabled_since", {})[gid] = since_iso
    save_state()

    guild = interaction.guild
    who = interaction.user.mention
    if guild:
        text = offline_text(who, since_iso)
        msg_id = await post_status_message(guild, text)
        if msg_id:
            state.setdefault("last_notice", {})[gid] = msg_id
            save_state()
            if not downtime_updater.is_running():
                downtime_updater.start()

    await interaction.response.send_message("🔴 Planday er nu **deaktiveret**.", ephemeral=True)

@tree.command(name="aktiver", description="Aktiver automatisk Planday-udsendelse, stop live ur og vis samlet nedetid")
@app_commands.checks.has_role(ROLE_DISP)
async def aktiver_cmd(interaction: discord.Interaction):
    if state.get("enabled", True):
        await interaction.response.send_message("Planday er allerede aktiveret.", ephemeral=True)
        return

    state["enabled"] = True
    gid = str(interaction.guild.id) if interaction.guild else None

    total_text = ""
    if gid and gid in state.get("disabled_since", {}):
        try:
            since = dt.datetime.fromisoformat(state["disabled_since"][gid])
        except Exception:
            since = dt.datetime.now(TZ)
        now = dt.datetime.now(TZ)
        total_text = format_duration(now - since)

    # ryd statusbesked og disabled_since
    if interaction.guild:
        await delete_status_message_if_any(interaction.guild)

    save_state()

    who = interaction.user.mention
    if interaction.guild:
        ok_text = (
            f":white_check_mark: Planday er **aktiveret igen** af {who}. "
            f"Nedetid i alt: **{total_text or '00:00:00'}**."
        )
        await post_status_message(interaction.guild, ok_text)

    await interaction.response.send_message("🟢 Planday er **aktiveret** igen.", ephemeral=True)

# -------------------- Live nedetids-opdatering (kører mens deaktiveret) --------------------
@tasks.loop(seconds=30)
async def downtime_updater():
    # Opdater hver 30. sekund alle kendte statusbeskeder med nyt ur
    try:
        for guild in bot.guilds:
            gid = str(guild.id)
            msg_id = state.get("last_notice", {}).get(gid)
            since_iso = state.get("disabled_since", {}).get(gid)
            if not msg_id or not since_iso:
                continue
            # Construct fresh content
            # Bemærk: vi bevarer "deaktiveret af"-brugeren ved at hente seneste kendte besked og parse er upraktisk,
            # så vi gemmer ikke navnet separat. I praksis vil tekst stadig give mening.
            # For fuld præcision kan man udvide state til også at gemme "disabled_by" pr. guild.
            # Her forsøger vi at læse forrige besked for at bevare navnet hvis muligt.
            who = "en disponent"
            try:
                ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
                if ch:
                    m = await ch.fetch_message(msg_id)
                    if m and m.content:
                        # Prøv at hive mention ud mellem "af " og næste linjeskift
                        content = m.content
                        marker = "Blev deaktiveret af "
                        if marker in content:
                            rest = content.split(marker, 1)[1]
                            who = rest.split("\n", 1)[0]
            except Exception:
                pass

            new_text = offline_text(who, since_iso)
            await edit_status_message(guild, msg_id, new_text)
    except Exception as e:
        print("[downtime_updater] fejl:", e)

# -------------------- Auto-post hver dag kl. 12 --------------------
@tasks.loop(time=dt.time(hour=DAILY_H, minute=DAILY_M, tzinfo=TZ))
async def daily_post():
    await bot.wait_until_ready()
    if not state.get("enabled", True):
        print("[AUTO] Skippet (deaktiveret)")
        return
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if ch:
            async for msg in ch.history(limit=10):
                if msg.author == bot.user:
                    await msg.delete()
            besked = "Automatisk daglig vagtplan – god vagt i aften ☕"
            starttid = "19:30"
            img_url = None
            embed = build_embed(starttid, besked, img_url, {"deltager": [], "senere": [], "fravaer": [], "disp": []})
            await ch.send(content="@everyone", embed=embed, view=VagtplanView(starttid, besked, img_url))
            print(f"[AUTO] Ny vagtplan sendt til {guild.name} med @everyone")

# -------------------- Auto-slet ved midnat --------------------
@tasks.loop(time=dt.time(hour=0, minute=0, tzinfo=TZ))
async def midnight_cleanup():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if ch:
            async for msg in ch.history(limit=50):
                if msg.author == bot.user:
                    await msg.delete()
            print(f"[AUTO] Vagtplan slettet ved midnat i {guild.name}")

# -------------------- Start --------------------
@bot.event
async def on_ready():
    print(f"✅ Logget ind som {bot.user}")
    try:
        await tree.sync()
        print("Slash-kommandoer synkroniseret.")
    except Exception as e:
        print("Fejl ved sync:", e)
    if not daily_post.is_running():
        daily_post.start()
        print("📅 Automatisk daglig post aktiveret (kl. 12:00)")
    if not midnight_cleanup.is_running():
        midnight_cleanup.start()
        print("🕛 Automatisk sletning ved midnat aktiveret")
    # Hvis der var en aktiv deaktivering før bot-restart, genstart live-uret
    has_any_down = bool(state.get("disabled_since")) and bool(state.get("last_notice"))
    if has_any_down and not downtime_updater.is_running():
        downtime_updater.start()
        print("⏱️ Live nedetids-ur genoptaget")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN mangler i miljøvariablerne")
    bot.run(TOKEN)
