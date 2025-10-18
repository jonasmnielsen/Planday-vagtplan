# -*- coding: utf-8 -*-
# Planday | Vagtplan – Discord-bot til SOSDAH - ZodiacRP
# Dansk tekst, daglig post kl. 12:00, starttid 19:30, Disponent-rollekrav,
# Google Sheets-logning og manuel besked via /vagtplan.

import os
import json
import datetime as dt
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

import gspread
from google.oauth2.service_account import Credentials


# -------------------- Konfiguration --------------------
CONFIG = json.load(open("config.json", "r", encoding="utf-8"))
TZ = ZoneInfo(CONFIG.get("timezone", "Europe/Copenhagen"))
START_H = CONFIG.get("start_time_h", 19)
START_M = CONFIG.get("start_time_m", 30)
DAILY_H = CONFIG.get("daily_post_hour", 12)
DAILY_M = CONFIG.get("daily_post_minute", 0)
ROLE_DISP = CONFIG.get("role_disponent", "Disponent")
CHANNEL_NAME = CONFIG.get("channel_name", "「📰」vagtplan")
SHEET_ID = CONFIG.get("sheet_id")  # SKAL udfyldes


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


# -------------------- Google Sheets klient --------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
gc = gspread.authorize(CREDS)


def ensure_sheets():
    if not SHEET_ID:
        raise RuntimeError("sheet_id mangler i config.json")
    sh = gc.open_by_key(SHEET_ID)
    # Registreringer
    try:
        reg = sh.worksheet("Registreringer")
    except gspread.WorksheetNotFound:
        reg = sh.add_worksheet(title="Registreringer", rows=1000, cols=10)
        reg.append_row(["Dato", "Brugernavn", "Handling", "Tidspunkt", "Disponent?"], value_input_option="USER_ENTERED")
    # Oversigt
    try:
        over = sh.worksheet("Oversigt")
    except gspread.WorksheetNotFound:
        over = sh.add_worksheet(title="Oversigt", rows=1000, cols=10)
        over.update("A1", "Navn")
        over.update("B1", "Deltaget (%)")
        over.update("C1", "Kommet senere (%)")
        over.update("D1", "Fravær (%)")
        over.update("E1", "Disponentdage")
        over.update("F1", "Total registreringer")
        over.update("A2", "=SORT(UNIQUE(FILTER(Registreringer!B:B, Registreringer!B:B<>\"\")))")
        over.update("F2", "=ARRAYFORMULA(IF(A2:A=\"\",,COUNTIF(Registreringer!B:B,A2:A)))")
        over.update("B2", "=ARRAYFORMULA(IF(A2:A=\"\",,IF(F2:F=0,0,ROUND(100*COUNTIFS(Registreringer!B:B,A2:A,Registreringer!C:C,\"Deltager\")/F2:F,1))))")
        over.update("C2", "=ARRAYFORMULA(IF(A2:A=\"\",,IF(F2:F=0,0,ROUND(100*COUNTIFS(Registreringer!B:B,A2:A,Registreringer!C:C,\"Deltager senere\")/F2:F,1))))")
        over.update("D2", "=ARRAYFORMULA(IF(A2:A=\"\",,IF(F2:F=0,0,ROUND(100*COUNTIFS(Registreringer!B:B,A2:A,Registreringer!C:C,\"Fraværende\")/F2:F,1))))")
        over.update("E2", "=ARRAYFORMULA(IF(A2:A=\"\",,COUNTIFS(Registreringer!B:B,A2:A,Registreringer!E:E,\"Ja\")))")
    return sh


def log_action(sh, dato_str: str, username: str, handling: str, tidspunkt: str, disp: bool):
    reg = sh.worksheet("Registreringer")
    reg.append_row([dato_str, username, handling, tidspunkt, "Ja" if disp else "Nej"], value_input_option="USER_ENTERED")


# -------------------- Hjælpere --------------------
async def find_channel_by_name(guild: discord.Guild, name: str) -> discord.TextChannel | None:
    for ch in guild.text_channels:
        if ch.name == name:
            return ch
    key = name.replace(" ", "").lower()
    for ch in guild.text_channels:
        if ch.name.replace(" ", "").lower().startswith(key):
            return ch
    return None


def build_embed(besked: str | None = None) -> discord.Embed:
    today = dt.datetime.now(TZ).date()
    e = discord.Embed(
        title=f"Dagens vagtplan for {dansk_dato(today).capitalize()}",
        description="Server: SOSDAH - ZodiacRP",
        colour=0x2b90d9,
    )
    start_str = f"{dansk_dato(today)} kl. {START_H:02d}:{START_M:02d}"
    e.add_field(name="Starttid", value=start_str, inline=False)
    e.add_field(name="Disponering", value="Ikke sat", inline=False)
    e.add_field(name="Besked", value=besked if besked else "Ingen besked sat", inline=False)
    e.set_footer(text="Planday | Vagtplan")
    return e


# -------------------- UI (knapper + modal) --------------------
class VagtplanView(discord.ui.View):
    def __init__(self, disponent_role_name: str, *, timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.disponent_role_name = disponent_role_name

    def _now(self):
        return dt.datetime.now(TZ)

    def _ts(self):
        return self._now().strftime("%H:%M")

    def _has_disponent_role(self, member: discord.Member) -> bool:
        return isinstance(member, discord.Member) and any(r.name == self.disponent_role_name for r in member.roles)

    async def _update_disponering(self, interaction: discord.Interaction):
        msg = interaction.message
        embed = msg.embeds[0]
        idx = next((i for i, f in enumerate(embed.fields) if f.name.lower().strip() == "disponering"), None)
        if idx is None:
            embed.add_field(name="Disponering", value="Ikke sat", inline=False)
            idx = len(embed.fields) - 1
        current = embed.fields[idx].value or "Ikke sat"
        name = getattr(interaction.user, 'display_name', interaction.user.name)
        if current.strip().lower() == "ikke sat":
            new_val = name
        else:
            new_val = current if name in current else f"{current}, {name}"
        embed.set_field_at(idx, name="Disponering", value=new_val, inline=False)
        await msg.edit(embed=embed, view=self)

    async def _handle(self, interaction: discord.Interaction, handling: str, requires_disponent: bool = False):
        if requires_disponent and not self._has_disponent_role(interaction.user):
            await interaction.response.send_message("Kun brugere med rollen **Disponent** kan bruge denne knap.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        sh = ensure_sheets()
        dato_str = self._now().strftime("%d-%m-%Y")
        ts = self._ts()
        username = getattr(interaction.user, 'display_name', interaction.user.name)
        log_action(sh, dato_str, username, handling, ts, requires_disponent)
        if requires_disponent:
            await self._update_disponering(interaction)
        await interaction.followup.send(f"Registreret **{handling}** for {interaction.user.mention} kl. {ts}", ephemeral=True)

    @discord.ui.button(label="Deltager", style=discord.ButtonStyle.success, emoji="✅")
    async def btn_deltager(self, interaction: discord.Interaction, _):
        await self._handle(interaction, "Deltager")

    @discord.ui.button(label="Deltager senere", style=discord.ButtonStyle.primary, emoji="🕓")
    async def btn_senere(self, interaction: discord.Interaction, _):
        await self._handle(interaction, "Deltager senere")

    @discord.ui.button(label="Fraværende", style=discord.ButtonStyle.danger, emoji="❌")
    async def btn_fravaer(self, interaction: discord.Interaction, _):
        await self._handle(interaction, "Fraværende")

    @discord.ui.button(label="disponent", style=discord.ButtonStyle.secondary, emoji="🧭")
    async def btn_disponent(self, interaction: discord.Interaction, _):
        await self._handle(interaction, "Disponent", requires_disponent=True)


class BeskedModal(discord.ui.Modal, title="Tilføj besked til vagtplan"):
    besked = discord.ui.TextInput(label="Besked", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, cb):
        super().__init__()
        self._cb = cb

    async def on_submit(self, interaction: discord.Interaction):
        await self._cb(interaction, str(self.besked))


# -------------------- Kommandoer --------------------
@tree.command(name="vagtplan", description="Send dagens vagtplan i kanal med mulighed for besked")
@app_commands.checks.has_role(ROLE_DISP)
async def vagtplan_cmd(interaction: discord.Interaction):
    async def after_modal(inter: discord.Interaction, besked_txt: str | None):
        guild = inter.guild
        if guild is None:
            await inter.response.send_message("Kan kun bruges i en server.", ephemeral=True)
            return

        ch = await find_channel_by_name(guild, CHANNEL_NAME)
        if ch is None:
            await inter.response.send_message(f"Kanalen '{CHANNEL_NAME}' blev ikke fundet.", ephemeral=True)
            return

        embed = build_embed(besked_txt)
        view = VagtplanView(ROLE_DISP)
        await ch.send(embed=embed, view=view)
        await inter.response.send_message("Vagtplan sendt.", ephemeral=True)

    await interaction.response.send_modal(BeskedModal(after_modal))


# -------------------- Daglig auto-post kl. 12:00 --------------------
@tasks.loop(time=dt.time(hour=DAILY_H, minute=DAILY_M, tzinfo=TZ))
async def daily_post():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        ch = await find_channel_by_name(guild, CHANNEL_NAME)
        if ch:
            try:
                await ch.send(embed=build_embed(), view=VagtplanView(ROLE_DISP))
            except Exception as e:
                print(f"Kunne ikke sende daglig vagtplan i {guild.name}: {e}")


# -------------------- Lifecycle --------------------
@bot.event
async def on_ready():
    print(f"Logget ind som {bot.user}")
    try:
        ensure_sheets()
        print("Google Sheets klar.")
    except Exception as e:
        print("Fejl ved Sheets-init:", e)
    try:
        await tree.sync()
        print("Slash-kommandoer synkroniseret.")
    except Exception as e:
        print("Fejl ved sync:", e)
    if not daily_post.is_running():
        daily_post.start()


# -------------------- Start --------------------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Sæt DISCORD_TOKEN som miljøvariabel i Railway → Variables.")
    bot.run(token)

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


