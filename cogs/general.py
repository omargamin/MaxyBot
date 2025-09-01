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

class General(commands.Cog, name="General"):
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.http_session = bot.http_session

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="ping", description="Checks the bot's latency and response time.")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(title="🏓 Pong!", description=f"**API Latency:** `{latency}ms`", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stats", description="Displays detailed statistics about the bot.")
    async def stats(self, interaction: discord.Interaction):
        process = psutil.Process(os.getpid())
        mem_usage = process.memory_info().rss
        uptime_delta = dt.now(UTC) - self.bot.start_time
        uptime_str = humanize.naturaldelta(uptime_delta)
        embed = discord.Embed(title=f"{self.bot.user.name} Statistics", color=discord.Color.blurple())
        embed.set_thumbnail(url=self.bot.user.avatar.url)
        embed.add_field(name="📊 Servers", value=f"`{len(self.bot.guilds)}`", inline=True)
        embed.add_field(name="👥 Users", value=f"`{len(self.bot.users)}`", inline=True)
        embed.add_field(name="💻 CPU Usage", value=f"`{psutil.cpu_percent()}%`", inline=True)
        embed.add_field(name="🧠 Memory", value=f"`{humanize.naturalsize(mem_usage)}`", inline=True)
        embed.add_field(name="⬆️ Uptime", value=f"`{uptime_str}`", inline=True)
        embed.add_field(name="🏓 Ping", value=f"`{round(self.bot.latency * 1000)}ms`", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Shows detailed information about a user.")
    @app_commands.describe(user="The user to get info about. Defaults to you.")
    async def userinfo(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        embed = discord.Embed(title=f"User Information: {target.display_name}", color=target.color)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Full Name", value=f"`{target}`", inline=True)
        embed.add_field(name="User ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Nickname", value=f"`{target.nick}`" if target.nick else "None", inline=True)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(target.created_at, style='R'), inline=True)
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(target.joined_at, style='R'), inline=True)
        roles = [role.mention for role in reversed(target.roles) if role.name != "@everyone"]
        role_str = ", ".join(roles) if roles else "None"
        embed.add_field(name=f"Roles [{len(roles)}]", value=role_str if len(role_str) < 1024 else f"{len(roles)} roles", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Shows detailed information about the current server.")
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=f"Server Info: {guild.name}", color=discord.Color.blue())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Created On", value=discord.utils.format_dt(guild.created_at, style='D'), inline=True)
        embed.add_field(name="Members", value=f"**Total:** {guild.member_count}\n**Humans:** {len([m for m in guild.members if not m.bot])}\n**Bots:** {len([m for m in guild.members if m.bot])}", inline=True)
        embed.add_field(name="Channels", value=f"**Text:** {len(guild.text_channels)}\n**Voice:** {len(guild.voice_channels)}", inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Displays a user's avatar in high resolution.")
    @app_commands.describe(user="The user whose avatar to show.")
    async def avatar(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        embed = discord.Embed(title=f"{target.display_name}'s Avatar", color=target.color)
        embed.set_image(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="snipe", description="Shows the most recently deleted message in the channel.")
    async def snipe(self, interaction: discord.Interaction):
        snipe_data = self.bot.snipe_data.get(interaction.channel.id)
        if not snipe_data:
            return await interaction.response.send_message("There's nothing to snipe!", ephemeral=True)
        embed = discord.Embed(description=snipe_data['content'], color=snipe_data['author'].color, timestamp=snipe_data['timestamp'])
        embed.set_author(name=snipe_data['author'].display_name, icon_url=snipe_data['author'].avatar.url)
        if snipe_data['attachments']:
            embed.set_image(url=snipe_data['attachments'][0])
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="editsnipe", description="Shows the original content of the most recently edited message.")
    async def editsnipe(self, interaction: discord.Interaction):
        snipe_data = self.bot.editsnipe_data.get(interaction.channel.id)
        if not snipe_data:
            return await interaction.response.send_message("There's no edited message to snipe!", ephemeral=True)
        embed = discord.Embed(color=snipe_data['author'].color, timestamp=snipe_data['timestamp'])
        embed.set_author(name=snipe_data['author'].display_name, icon_url=snipe_data['author'].avatar.url)
        embed.add_field(name="Before", value=snipe_data['before_content'], inline=False)
        embed.add_field(name="After", value=snipe_data['after_content'], inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="Shows a list of all available commands.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Maxy Bot Help", description="Here is a list of all command categories. All commands are slash commands (/).", color=discord.Color.blurple())
        for cog_name, cog in self.bot.cogs.items():
            if cog_name.lower() == "admin" and not await self.bot.is_owner(interaction.user):
                continue

            commands_list = []
            for cmd in cog.get_app_commands():
                if isinstance(cmd, app_commands.Command):
                    commands_list.append(f"`/{cmd.name}`")

            if commands_list:
                embed.add_field(name=f"**{cog_name}**", value=' '.join(commands_list), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="invite", description="Get the bot's invite link.")
    async def invite(self, interaction: discord.Interaction):
        invite_url = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(permissions=8), scopes=("bot", "applications.commands"))
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Invite Me!", url=invite_url))
        await interaction.response.send_message("Click the button below to invite me to your server!", view=view, ephemeral=True)

async def setup(bot: MaxyBot):
    await bot.add_cog(General(bot))