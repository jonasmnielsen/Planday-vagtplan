# -*- coding: utf-8 -*-
# Planday | Vagtplan – SOSDAH - ZodiacRP
# Version uden Google Sheets

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

START_H = 19
START_M = 30
DAILY_H = 12
DAILY_M = 0

# -------------------- Intents & Client --------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# -------------------- Dansk datoformat --------------------
DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS = [
    "januar", "februar", "marts", "april", "maj", "juni",
    "juli", "august", "september", "oktober", "november", "december"
]
def dansk_dato(d: dt.date) -> str:
    return f"{DAYS[d.weekday()]} den {d.day}. {MONTHS[d.month - 1]}"

# -------------------- Gemte registreringer --------------------
registreringer = {}

def get_msg_data(msg_id):
    if msg_id not in registreringer:
        registreringer[msg_id] = {"deltager": [], "senere": [], "fravaer": [], "disp": []}
    return registreringer[msg_id]

# -------------------- Byg embed --------------------
def build_embed(besked=None, data=None):
    today = dt.datetime.now(TZ).date()
    title = f"Dagens vagtplan for {dansk_dato(today)}"
    embed = discord.Embed(title=title, description="Server: SOSDAH - ZodiacRP", color=0x2b90d9)
    embed.add_field(name="🕒 Starttid", value=f"{dansk_dato(today)} kl. {START_H:02d}:{START_M:02d}", inline=False)

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
    return embed

# -------------------- View med knapper --------------------
class VagtplanView(discord.ui.View):
    def __init__(self, besked=None, *, timeout=None):
        super().__init__(timeout=timeout)
        self.besked = besked

    async def update_status(self, interaction: discord.Interaction, kategori: str):
        msg_id = interaction.message.id
        user_mention = interaction.user.mention
        data = get_msg_data(msg_id)

        # Fjern brugeren fra alle kategorier
        for k in data.keys():
            if user_mention in data[k]:
                data[k].remove(user_mention)

        # Tilføj i valgt kategori
        if kategori:
            data[kategori].append(user_mention)

        # Opdater embed
        embed = build_embed(self.besked, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message(f"Registreret som **{kategori}** ✅", ephemeral=True)

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
        embed = build_embed(self.besked, data)
        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.send_message("Disponent-status opdateret ✅", ephemeral=True)

# -------------------- Slash-kommando --------------------
@tree.command(name="vagtplan", description="Send dagens vagtplan i kanal")
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan(interaction: discord.Interaction):
    def check_channel(guild):
        return discord.utils.get(guild.text_channels, name=CHANNEL_NAME)

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Denne kommando skal bruges i en server.", ephemeral=True)
        return

    ch = check_channel(guild)
    if not ch:
        await interaction.response.send_message(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
        return

    await interaction.response.send_message("Skriv beskeden til vagtplanen i chatten (inden for 30 sekunder)...", ephemeral=True)

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await bot.wait_for("message", check=check, timeout=30)
        besked = msg.content
    except:
        besked = None

    embed = build_embed(besked, get_msg_data("ny"))
    view = VagtplanView(besked)
    sent = await ch.send(embed=embed, view=view)
    get_msg_data(sent.id)  # init tom liste
    await interaction.followup.send("Vagtplan sendt ✅", ephemeral=True)

# -------------------- Auto-post kl. 12 --------------------
@tasks.loop(time=dt.time(hour=DAILY_H, minute=DAILY_M, tzinfo=TZ))
async def daily_post():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        ch = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
        if ch:
            embed = build_embed("Automatisk daglig post", get_msg_data("auto"))
            await ch.send(embed=embed, view=VagtplanView("Automatisk daglig post"))

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

bot.run(TOKEN)

# -------------------- Info-kommando med flotte embeds i DM --------------------
from discord import ui, Interaction, Embed, Color

class InfoButtons(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def send_embed_dm(self, interaction: Interaction, title: str, description: str, color: Color):
        embed = Embed(title=title, description=description, color=color)
        embed.set_footer(text="SOSDAH - ZodiacRP | Planday | Vagtplan")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig informationen som privat besked ✅", ephemeral=True)

    # 📻 Brug af radio
    @ui.button(label="📻 Brug af Radio", style=discord.ButtonStyle.primary)
    async def radio(self, interaction: Interaction, button: ui.Button):
        await self.send_embed_dm(
            interaction,
            "📻 Brug af Radio",
            "Retningslinjer for korrekt brug af radio under udrykning, på stationen og ved opgaver.",
            Color.blue()
        )

    # 🅿️ Parkering på stationerne
    @ui.button(label="🅿️ Parkering på stationerne", style=discord.ButtonStyle.primary)
    async def parkering(self, interaction: Interaction, button: ui.Button):
        view = ui.View(timeout=None)

        # Station 700 (ikke klar)
        @ui.button(label="🏭 Station 700", style=discord.ButtonStyle.danger)
        async def station700(inner_interaction: Interaction, btn: ui.Button):
            await inner_interaction.response.send_message(
                "🚧 **Parkering for Station 700 er endnu ikke tilgængelig.**\n\nDenne vejledning er under udarbejdelse.",
                ephemeral=True
            )

        # Station 701 (åbner PDF)
        @ui.button(label="🏢 Station 701", style=discord.ButtonStyle.success)
        async def station701(inner_interaction: Interaction, btn: ui.Button):
            embed = Embed(
                title="🏢 Parkering på Station 701",
                description=(
                    "Her finder du vejledningen til korrekt parkering på **Station 701** i Los Santos.\n\n"
                    "🔗 [Klik her for at åbne vejledningen (PDF)](file:///C:/Users/jonma/Downloads/Vejledning%20til%20parkering%20af%20k%C3%B8ret%C3%B8jer%20p%C3%A5%20SOS%20DAH%20Station%20701%20-%20Los%20Santos%20(1).pdf)\n\n"
                    "📍 **Kort opsummering:**\n"
                    "• Privatbiler foran stationen.\n"
                    "• Lederbiler langs væggen.\n"
                    "• 701-38, 701-02, 701-01 i værkstedet.\n"
                    "• 701-21 og 700-52 i lille hal.\n"
                    "• Skiltetrailer, autotrailer og Hilux i midterste hal."
                ),
                color=Color.green()
            )
            embed.set_footer(text="SOSDAH - ZodiacRP | Parkering 701")
            await inner_interaction.user.send(embed=embed)
            await inner_interaction.response.send_message("Jeg har sendt dig parkering for Station 701 ✅", ephemeral=True)

        view.add_item(station700)
        view.add_item(station701)

        await interaction.user.send(
            embed=Embed(
                title="🅿️ Vælg station for parkering",
                description="Vælg den station, du vil se parkeringsvejledningen for.",
                color=Color.blurple()
            ),
            view=view
        )
        await interaction.response.send_message("Jeg har sendt dig mulighederne som privat besked ✅", ephemeral=True)

    # 📋 Hændelsesrapport
    @ui.button(label="📋 Hændelsesrapport", style=discord.ButtonStyle.primary)
    async def haendelse(self, interaction: Interaction, button: ui.Button):
        embed = Embed(
            title="📋 Hændelsesrapport",
            description=(
                "Her kan du indsende en **hændelsesrapport** for en opgave, ulykke eller intern hændelse.\n\n"
                "🔗 [Klik her for at åbne Hændelsesrapporten (Google Form)](https://forms.gle/aty5vnRq8wkpRuQC7)\n\n"
                "📍 **Vejledning:**\n"
                "• Udfyld rapporten så detaljeret som muligt.\n"
                "• Sørg for at angive tidspunkt, sted og involverede personer.\n"
                "• Ved alvorlige hændelser skal ledelsen kontaktes straks."
            ),
            color=Color.orange()
        )
        embed.set_footer(text="SOSDAH - ZodiacRP | Hændelsesrapport")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig linket til hændelsesrapporten som privat besked ✅", ephemeral=True)

    # 🚨 Actioncard
    @ui.button(label="🚨 Actioncard", style=discord.ButtonStyle.primary)
    async def actioncard(self, interaction: Interaction, button: ui.Button):
        embed = Embed(
            title="🚨 Actioncard – Beslaglæggelsesdepot Del Perro",
            description=(
                "Her finder du det aktuelle **Actioncard** for beslaglæggelsesdepotet i Del Perro (8004).\n\n"
                "🔗 [Klik her for at åbne Actioncard (PDF)](https://YOUR-LINK-HERE)\n\n"
                "📍 **Indhold i vejledningen:**\n"
                "• Nix-pille-område og adgangsregler.\n"
                "• Almindelig opbevaring og placering.\n"
                "• Beslaglæggelses-procedure og udfyldelse af seddel.\n"
                "• Frigivelse af køretøjer godkendt af mekaniker.\n"
                "• Sikkerhed og adgang – husk at alle porte skal låses.\n\n"
                "_Dette er en politilokalitet – adgang kun i tjenstligt øjemed._"
            ),
            color=Color.red()
        )
        embed.set_footer(text="SOSDAH – ZodiacRP | Actioncard Del Perro")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig Actioncard-vejledningen som privat besked ✅", ephemeral=True)

    # 🚗 Sikker i trafikken
    @ui.button(label="🚗 Sikker i trafikken", style=discord.ButtonStyle.primary)
    async def sikkertrafik(self, interaction: Interaction, button: ui.Button):
        embed = Embed(
            title="🚗 Sikker i trafikken",
            description=(
                "Din sikkerhed som redder er altid det vigtigste.\n\n"
                "Denne vejledning beskriver, hvordan du arbejder sikkert på vejene, "
                "hvordan du placerer dig korrekt, og hvorfor det er vigtigt aldrig at holde i modkørende retning.\n\n"
                "🔗 [Klik her for at åbne hele vejledningen (PDF)](file:///C:/Users/jonma/Downloads/SOS%20Dansk%20Autohj%C3%A6lp%20A_S%20-%20Zodiac%20(3).pdf)\n\n"
                "📍 **Kort opsummering:**\n"
                "• Tænd altid lysbro, havariblink og arbejdslys.\n"
                "• Stå aldrig med ryggen mod trafikken.\n"
                "• Kald på TMA, hvis du arbejder uden for nødsporet.\n"
                "• Hold aldrig i modkørende retning.\n"
                "• Kontakt disponenten ved usikkerhed."
            ),
            color=Color.purple()
        )
        embed.set_footer(text="SOSDAH - ZodiacRP | Sikker i trafikken")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig vejledningen som privat besked ✅", ephemeral=True)

    # 💰 Prisliste
    @ui.button(label="💰 Prisliste", style=discord.ButtonStyle.secondary)
    async def prisliste(self, interaction: Interaction, button: ui.Button):
        await self.send_embed_dm(
            interaction,
            "💰 Prisliste",
            "Den aktuelle prisliste for ydelser, assistancer og andre opgaver.",
            Color.gold()
        )

        # 👕 Tøjguide
    @ui.button(label="👕 Tøjguide", style=discord.ButtonStyle.secondary)
    async def tojguide(self, interaction: Interaction, button: ui.Button):
        embed = Embed(
            title="👕 Tøjguide — SOS Dansk Autohjælp",
            description=(
                "Her finder du den officielle **tøjguide** for SOS Dansk Autohjælp.\n\n"
                "🔗 [Klik her for at åbne Tøjguiden (PDF)](file:///C:/Users/jonma/Downloads/T%C3%B8jguide%20til%20SOS%20Dansk%20Autohj%C3%A6lp%20(1).pdf)\n\n"
                "📋 **Indhold:**\n"
                "• Autoredder-uniform\n"
                "• Kranfører-uniform\n"
                "• Ledelses-uniform\n\n"
                "_Guiden sikrer, at alle reddere er korrekt og professionelt klædt på til opgaven._"
            ),
            color=Color.dark_blue()
        )
        embed.set_footer(text="SOSDAH - ZodiacRP | Tøjguide")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig tøjguiden som privat besked ✅", ephemeral=True)


    # 🚒 Flådestyring
    @ui.button(label="🚒 Flådestyring", style=discord.ButtonStyle.secondary)
    async def flaade(self, interaction: Interaction, button: ui.Button):
        embed = Embed(
            title="🚒 Flådestyring",
            description=(
                "Her kan du tilgå **SOS Dansk Autohjælps flådestyring**.\n\n"
                "Du kan se live-status på køretøjer, tilgængelighed og øvrige oplysninger direkte i dokumentet.\n\n"
                "🔗 [Klik her for at åbne Flådestyring (Google Sheets)](https://docs.google.com/spreadsheets/d/13Wi28C0wqG6sD6_mK5bw-SVp-ngKBRH7l-lehe5t3t4/edit?usp=sharing)\n\n"
                "📍 **Bemærk:**\n"
                "• Dokumentet kræver muligvis login til Google.\n"
                "• Redigering er kun tilladt for autoriserede brugere."
            ),
            color=Color.teal()
        )
        embed.set_footer(text="SOSDAH - ZodiacRP | Flådestyring")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig linket til flådestyring som privat besked ✅", ephemeral=True)

    # 👔 Ledelsen
    @ui.button(label="👔 Ledelsen", style=discord.ButtonStyle.success)
    async def ledelsen(self, interaction: Interaction, button: ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Denne funktion kan kun bruges i en server.", ephemeral=True)
            return

        roles_to_show = ["Distriktleder", "Uddannelses Leder", "Stationsleder"]
        embed = Embed(title="👔 Ledelsen — SOS Dansk Autohjælp", color=Color.dark_gray())
        embed.set_footer(text="SOSDAH - ZodiacRP | Ledelsen")

        members_shown = False
        for role_name in roles_to_show:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                members = [m for m in role.members]
                if members:
                    for member in members:
                        embed.add_field(
                            name=f"{member.display_name} — {role_name}",
                            value=f"[Profilbillede]({member.avatar.url if member.avatar else member.default_avatar.url})",
                            inline=False
                        )
                        members_shown = True

        if not members_shown:
            embed.description = "Ingen medlemmer fundet i Ledelsen."

        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig listen over ledelsen som privat besked ✅", ephemeral=True)


    # 🎓 Mentor
    @ui.button(label="🎓 Mentor", style=discord.ButtonStyle.success)
    async def mentor(self, interaction: Interaction, button: ui.Button):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Denne funktion kan kun bruges i en server.", ephemeral=True)
            return

        role = discord.utils.get(guild.roles, name="Mentor")
        embed = Embed(title="🎓 Mentor Team — SOS Dansk Autohjælp", color=Color.dark_green())
        embed.set_footer(text="SOSDAH - ZodiacRP | Mentorordning")

        if role and role.members:
            for member in role.members:
                embed.add_field(
                    name=f"{member.display_name} — Mentor",
                    value=f"[Profilbillede]({member.avatar.url if member.avatar else member.default_avatar.url})",
                    inline=False
                )
        else:
            embed.description = "Ingen mentorer fundet på serveren."

        await interaction.user.send(embed=embed)
        await interaction.response.send_message("Jeg har sendt dig listen over mentorer som privat besked ✅", ephemeral=True)



@tree.command(name="info", description="Vis informationsbaren med knapper")
async def info_cmd(interaction: discord.Interaction):
    embed = Embed(
        title="📘 Information & Dokumenter",
        description="Tryk på en af knapperne herunder for at få informationen som privat besked.\n\n🧭 *Planday | SOSDAH - ZodiacRP*",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=InfoButtons())














