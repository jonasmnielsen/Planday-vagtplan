# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP
# Dansk version med starttid, besked, billede, auto-post kl. 12 og auto-slet kl. 00:00 med @everyone-tag

import os
import datetime as dt
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from discord.ext import tasks

# -------------------- Konfiguration --------------------
TOKEN = os.getenv("DISCORD_TOKEN")
ROLE_DISP = "Disponent"
CHANNEL_NAME = "vagtplan"
TZ = ZoneInfo("Europe/Copenhagen")
DAILY_H = 12
DAILY_M = 0

# -------------------- Intents & Client --------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

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
        description="Server: SOSDAH - ZodiacRP",
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
            await interaction.response.send_message("Kun brugere med rollen **Disponent** kan bruge denne knap.", ephemeral=True)
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

# -------------------- Slash-kommando --------------------
@tree.command(name="vagtplan", description="Send dagens vagtplan med starttid, besked og billede")
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan_cmd(interaction: discord.Interaction):
    async def after_modal(inter: discord.Interaction, starttid: str, besked: str | None, billede: str | None):
        guild = inter.guild
        if guild is None:
            await inter.response.send_message("Kan kun bruges i en server.", ephemeral=True)
            return

        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if not ch:
            await inter.response.send_message(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
            return

        # Slet gamle vagtplaner
        async for msg in ch.history(limit=10):
            if msg.author == bot.user:
                await msg.delete()

        embed = build_embed(starttid, besked, billede, get_msg_data("ny"))
        view = VagtplanView(starttid, besked, billede)
        sent = await ch.send(content="@everyone", embed=embed, view=view)
        get_msg_data(sent.id)
        await inter.response.send_message("✅ Vagtplan sendt med @everyone.", ephemeral=True)

    await interaction.response.send_modal(BeskedModal(after_modal))

# -------------------- Auto-post hver dag kl. 12 --------------------
@tasks.loop(time=dt.time(hour=DAILY_H, minute=DAILY_M, tzinfo=TZ))
async def daily_post():
    await bot.wait_until_ready()
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

bot.run(TOKEN)
