from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Union
import io
import discord
from discord import app_commands
from discord.ext import commands, tasks
import datetime
from datetime import datetime as dt, UTC
import random
import aiohttp
import os
import re
import humanize
import psutil
import time
import asyncio
import json
import math
import difflib
import yt_dlp
from PIL import Image, ImageDraw, ImageFont, ImageOps
import google.generativeai as genai

if TYPE_CHECKING:
    from ..bot import MaxyBot

from .utils import cog_command_error

class Giveaways(commands.Cog, name="Giveaways"):
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    class GiveawayJoinView(discord.ui.View):
        def __init__(self, bot: 'MaxyBot', message_id: int):
            super().__init__(timeout=None)
            self.bot = bot
            self.message_id = message_id
            self.add_item(discord.ui.Button(label="Join", style=discord.ButtonStyle.success, custom_id=f"join_giveaway_{message_id}", emoji="🎉"))

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.data['custom_id'] != f"join_giveaway_{self.message_id}":
                return False

            await interaction.response.defer(ephemeral=True)
            is_entrant = await self.bot.db.fetchone("SELECT 1 FROM giveaway_entrants WHERE message_id = ? AND user_id = ?", (self.message_id, interaction.user.id))
            if is_entrant:
                await interaction.followup.send("You have already entered this giveaway!", ephemeral=True)
                return False

            await self.bot.db.execute("INSERT INTO giveaway_entrants (message_id, user_id) VALUES (?, ?)", (self.message_id, interaction.user.id))
            await interaction.followup.send("You have successfully entered the giveaway!", ephemeral=True)
            return False

    @app_commands.command(name="g-start", description="[Admin] Starts a new giveaway.")
    @app_commands.describe(duration="Duration (e.g., 10m, 1h, 2d).", winners="The number of winners.", prize="What the prize is.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def g_start(self, interaction: discord.Interaction, duration: str, winners: app_commands.Range[int, 1, 20], prize: str):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        seconds = 0
        match = re.findall(r"(\d+)([smhdw])", duration.lower())
        if not match:
            return await interaction.response.send_message("Invalid duration format.", ephemeral=True)

        for value, unit in match:
            seconds += int(value) * units[unit]

        end_time = dt.now(UTC) + datetime.timedelta(seconds=seconds)
        end_timestamp = int(end_time.timestamp())

        embed = discord.Embed(title=f"🎉 **GIVEAWAY: {prize}** 🎉", description=f"Click the button to enter!\nEnds: <t:{end_timestamp}:R> (<t:{end_timestamp}:F>)\nHosted by: {interaction.user.mention}", color=discord.Color.gold())
        embed.set_footer(text=f"{winners} winner(s) | Ends at")
        embed.timestamp = end_time

        await interaction.response.send_message("Giveaway created!", ephemeral=True)
        message = await interaction.channel.send(embed=embed)

        final_view = self.GiveawayJoinView(self.bot, message.id)
        self.bot.add_view(final_view, message_id=message.id)

        await self.bot.db.execute("INSERT INTO giveaways (message_id, guild_id, channel_id, prize, end_timestamp, winner_count) VALUES (?, ?, ?, ?, ?, ?)", (message.id, interaction.guild.id, interaction.channel.id, prize, end_time.timestamp(), winners))

    @tasks.loop(seconds=15)
    async def check_giveaways(self):
        giveaways = await self.bot.db.fetchall("SELECT * FROM giveaways WHERE is_ended = 0 AND end_timestamp < ?", (dt.now(UTC).timestamp(),))
        for g in giveaways:
            channel = self.bot.get_channel(g['channel_id'])
            if not channel:
                await self.bot.db.execute("UPDATE giveaways SET is_ended = 1 WHERE message_id = ?", (g['message_id'],))
                continue

            try:
                message = await channel.fetch_message(g['message_id'])
            except discord.NotFound:
                await self.bot.db.execute("UPDATE giveaways SET is_ended = 1 WHERE message_id = ?", (g['message_id'],))
                continue

            entrants = await self.bot.db.fetchall("SELECT user_id FROM giveaway_entrants WHERE message_id = ?", (g['message_id'],))
            entrant_ids = [e['user_id'] for e in entrants]
            winner_count = min(g['winner_count'], len(entrant_ids))
            winners = random.sample(entrant_ids, k=winner_count) if entrant_ids else []
            winner_mentions = [f"<@{w_id}>" for w_id in winners]

            new_embed = message.embeds[0].to_dict()
            new_embed['title'] = f"🎉 **GIVEAWAY ENDED: {g['prize']}** 🎉"
            new_embed['description'] = f"Winners: {', '.join(winner_mentions) if winners else 'No one!'}\nHosted by: {new_embed['description'].split('Hosted by: ')[1]}"
            new_embed['color'] = discord.Color.dark_grey().value

            await message.edit(embed=discord.Embed.from_dict(new_embed), view=None)

            if winners:
                await message.reply(f"Congratulations {', '.join(winner_mentions)}! You won the **{g['prize']}**!")
            else:
                await message.reply(f"The giveaway for **{g['prize']}** has ended, but there were no entrants.")

            await self.bot.db.execute("UPDATE giveaways SET is_ended = 1 WHERE message_id = ?", (g['message_id'],))

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()

async def setup(bot: MaxyBot):
    await bot.add_cog(Giveaways(bot))