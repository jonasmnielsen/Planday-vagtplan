# -*- coding: utf-8 -*-

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




@vagtplan_cmd.error
async def vagtplan_cmd_error(interaction: discord.Interaction, error):
if isinstance(error, app_commands.MissingRole):
await interaction.response.send_message("Kun **Disponent** kan bruge denne kommando.", ephemeral=True)
else:
await interaction.response.send_message(f"Fejl: {error}", ephemeral=True)


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

