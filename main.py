import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from flask import Flask
import asyncio
import os

token = os.getenv("DISCORDMUSIC_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive"

@app.route("/ping")
def ping():
    return "OK"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web, daemon=True).start()

# ---------------- GLOBAL STATE ---------------- #

volume_level = 0.5
song_queue = []
current_song = None

repeat_mode = False
current_position = 0

player_message = None

# ---------------- YTDLP + FFMPEG ---------------- #

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10",
    "options": "-vn"
}

# ---------------- EMBED ---------------- #

def make_embed(song):

    status = "▶ Playing"
    if song.get("vc_state") == "paused":
        status = "⏸ Paused"

    next_song = song_queue[0]["title"] if song_queue else "None"

    embed = discord.Embed(
        title="🎵 Music Player",
        description=f"{status}\n**{song['title']}**",
        color=discord.Color.blurple()
    )

    embed.add_field(name="Volume", value=f"{int(volume_level*100)}%", inline=True)
    embed.add_field(name="Next", value=next_song, inline=True)
    embed.add_field(name="Repeat", value=str(repeat_mode), inline=True)

    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])

    return embed

# ---------------- AFTER SONG HANDLER ---------------- #

async def handle_after_song(interaction):

    global repeat_mode

    if repeat_mode and current_song:
        song_queue.insert(0, current_song)

    await play_next(interaction)

# ---------------- UI ---------------- #

class PlayModal(discord.ui.Modal, title="Play Song"):

    query = discord.ui.TextInput(label="Song Name")

    async def on_submit(self, interaction: discord.Interaction):
        await play_song(interaction, str(self.query))


class MusicControls(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    async def update(self, interaction):

        global player_message

        if player_message and current_song:
            await player_message.edit(
                embed=make_embed(current_song),
                view=self
            )

    # ---------------- PLAY ---------------- #

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.success)
    async def play_btn(self, interaction, button):
        await interaction.response.send_modal(PlayModal())

    # ---------------- PAUSE / RESUME ---------------- #

    @discord.ui.button(emoji="⏯", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction, button):

        global current_song

        vc = interaction.guild.voice_client

        if not vc:
            return await interaction.response.send_message("Not connected", ephemeral=True)

        if vc.is_playing():
            vc.pause()
            current_song["vc_state"] = "paused"

        elif vc.is_paused():
            vc.resume()
            current_song["vc_state"] = "playing"

        await self.update(interaction)
        await interaction.response.defer()

    # ---------------- REPEAT ---------------- #

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary)
    async def repeat_btn(self, interaction, button):

        global repeat_mode
        repeat_mode = not repeat_mode

        await self.update(interaction)

        await interaction.response.send_message(
            f"Repeat: {repeat_mode}",
            ephemeral=True
        )

    # ---------------- QUEUE ---------------- #

    @discord.ui.button(emoji="📜", style=discord.ButtonStyle.secondary)
    async def queue_btn(self, interaction, button):

        if not song_queue:
            return await interaction.response.send_message("Queue empty", ephemeral=True)

        text = "\n".join(f"{i+1}. {s['title']}" for i, s in enumerate(song_queue))

        embed = discord.Embed(title="Queue", description=text)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------- NEXT ---------------- #

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, button):

        text = song_queue[0]["title"] if song_queue else "None"

        await interaction.response.send_message(text, ephemeral=True)

    # ---------------- VOLUME ---------------- #

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary)
    async def vol_down(self, interaction, button):

        global volume_level

        volume_level = max(0.0, volume_level - 0.1)

        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = volume_level

        await self.update(interaction)
        await interaction.response.defer()

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary)
    async def vol_up(self, interaction, button):

        global volume_level

        volume_level = min(2.0, volume_level + 0.1)

        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = volume_level

        await self.update(interaction)
        await interaction.response.defer()

    # ---------------- STOP ---------------- #
@discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger)
async def stop(self, interaction, button):

    global current_song, player_message

    song_queue.clear()
    current_song = None

    vc = interaction.guild.voice_client

    if vc:
        vc.stop()
        await vc.disconnect()

    player_message = None

    await interaction.response.send_message("Stopped & disconnected", ephemeral=True)
# ---------------- PLAY SONG ---------------- #

async def play_song(interaction, query):

    global current_song

    vc = interaction.guild.voice_client

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)

        if "entries" in info:
            info = info["entries"][0]

    song = {
        "title": info["title"],
        "url": info["url"],
        "thumbnail": info.get("thumbnail"),
        "vc_state": "playing"
    }

    song_queue.append(song)

    if not vc.is_playing():
        await play_next(interaction)

    await interaction.response.send_message(f"Queued: {song['title']}")

# ---------------- QUEUE PLAYER ---------------- #

async def play_next(interaction):

    global current_song, player_message

    if not song_queue:
        current_song = None
        return

    current_song = song_queue.pop(0)

    vc = interaction.guild.voice_client

    source = discord.FFmpegPCMAudio(
        current_song["url"],
        **FFMPEG_OPTIONS
    )

    source = discord.PCMVolumeTransformer(source, volume=volume_level)

    def after(error):

        asyncio.run_coroutine_threadsafe(
            handle_after_song(interaction),
            bot.loop
        )

    vc.play(source, after=after)

    view = MusicControls()
    embed = make_embed(current_song)

    if player_message:
        await player_message.edit(embed=embed, view=view)
    else:
        player_message = await interaction.channel.send(embed=embed, view=view)

async def refresh_player():
    global player_message, current_song

    if player_message and current_song:
        await player_message.edit(
            embed=make_embed(current_song),
            view=MusicControls()
        )

# ---------------- SLASH COMMANDS ---------------- #

@bot.tree.command(
    name="play",
    description="Play a song"
)
@app_commands.describe(
    query="Song name"
)
async def play(
    interaction: discord.Interaction,
    query: str
):

    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send(
            "Join a VC first"
        )
        return

    channel = interaction.user.voice.channel

    vc = interaction.guild.voice_client

    if not vc:
        vc = await channel.connect()

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:

        info = ydl.extract_info(
            query,
            download=False
        )

        if "entries" in info:
            info = info["entries"][0]

        formats = info.get("formats", [])

        audio_url = None

        for f in formats:

            if f.get("acodec") != "none":

                audio_url = f.get("url")
                break

        if not audio_url:

            await interaction.followup.send(
                "Could not find audio source"
            )

            return

        song = {
            "title": info["title"],
            "url": audio_url,
            "thumbnail": info.get("thumbnail"),
            "requester": interaction.user
        }

    song_queue.append(song)

    await interaction.followup.send(
        f"Queued: **{song['title']}**"
    )

    if not vc.is_playing():
        await play_next(interaction)


@bot.tree.command(
    name="pause",
    description="Pause music"
)
async def pause(interaction: discord.Interaction):

    vc = interaction.guild.voice_client

    if vc and vc.is_playing():

        vc.pause()

        await interaction.response.send_message(
            "Paused"
        )


@bot.tree.command(
    name="resume",
    description="Resume music"
)
async def resume(interaction: discord.Interaction):

    vc = interaction.guild.voice_client

    if vc and vc.is_paused():

        vc.resume()

        await interaction.response.send_message(
            "Resumed"
        )


@bot.tree.command(
    name="skip",
    description="Skip current song"
)
async def skip(interaction: discord.Interaction):

    vc = interaction.guild.voice_client

    if vc:

        vc.stop()

        await interaction.response.send_message(
            "Skipped"
        )


@bot.tree.command(
    name="stop",
    description="Stop music"
)
async def stop(interaction: discord.Interaction):

    vc = interaction.guild.voice_client

    if vc:

        song_queue.clear()

        vc.stop()

        await interaction.response.send_message(
            "Stopped"
        )


@bot.tree.command(
    name="queue",
    description="Show queue"
)
async def queue(interaction: discord.Interaction):

    if len(song_queue) == 0:

        await interaction.response.send_message(
            "Queue is empty"
        )

        return

    message = ""

    for i, song in enumerate(song_queue, start=1):

        message += f"{i}. {song['title']}\n"

    embed = discord.Embed(
        title="📜 Queue",
        description=message,
        color=discord.Color.green()
    )

    await interaction.response.send_message(
        embed=embed
    )


@bot.tree.command(
    name="nowplaying",
    description="Current song"
)
async def nowplaying(interaction: discord.Interaction):

    if not current_song:

        await interaction.response.send_message(
            "Nothing playing"
        )

        return

    embed = make_embed(current_song)

    await interaction.response.send_message(
        embed=embed,
        view=MusicControls()
    )


@bot.tree.command(
    name="volume",
    description="Set volume"
)
@app_commands.describe(
    amount="0 to 200"
)
async def volume(
    interaction: discord.Interaction,
    amount: int
):

    global volume_level

    vc = interaction.guild.voice_client

    if not vc or not vc.source:

        await interaction.response.send_message(
            "Nothing playing"
        )

        return

    amount = max(0, min(amount, 200))

    volume_level = amount / 100

    vc.source.volume = volume_level

    await interaction.response.send_message(
        f"Volume set to {amount}%"
    )


@bot.tree.command(
    name="leave",
    description="Leave VC"
)
async def leave(interaction: discord.Interaction):

    vc = interaction.guild.voice_client

    if vc:

        await vc.disconnect()

        await interaction.response.send_message(
            "Disconnected"
        )

# ---------------- START ---------------- #

@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Bot ready")

bot.run(token)
