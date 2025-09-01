# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import aiofiles

TRANSCRIPTS_DIR = "transcripts"

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not os.path.exists(TRANSCRIPTS_DIR):
            os.makedirs(TRANSCRIPTS_DIR)

    # -----------------------------
    # Views
    # -----------------------------
    class TicketPanelView(discord.ui.View):
        def __init__(self, staff_role_id: int, category_id: int, panel_title: str):
            super().__init__(timeout=None)
            self.add_item(
                discord.ui.Button(
                    label=panel_title,
                    style=discord.ButtonStyle.primary,
                    emoji="🎟️",
                    custom_id=f"ticket_create_{staff_role_id}_{category_id}"
                )
            )

    class TicketActionView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(discord.ui.Button(label="Claim", style=discord.ButtonStyle.success, emoji="🙋", custom_id="persistent_ticket_claim"))
            self.add_item(discord.ui.Button(label="Transcript", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="persistent_ticket_transcript"))
            self.add_item(discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="persistent_ticket_close"))
            self.add_item(discord.ui.Button(label="@Admins", style=discord.ButtonStyle.secondary, emoji="👑", custom_id="persistent_ticket_mention_admins"))

    # -----------------------------
    # Listeners
    # -----------------------------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data or not (custom_id := interaction.data.get("custom_id")):
            return

        if custom_id.startswith("ticket_create_"):
            await self.create_ticket(interaction, custom_id)
        elif custom_id == "persistent_ticket_claim":
            await self.claim_ticket(interaction)
        elif custom_id == "persistent_ticket_transcript":
            await self.transcript_ticket(interaction)
        elif custom_id == "persistent_ticket_close":
            await self.close_ticket(interaction)
        elif custom_id == "persistent_ticket_mention_admins":
            await self.mention_admins(interaction)

    # -----------------------------
    # Ticket Actions
    # -----------------------------
    async def create_ticket(self, interaction: discord.Interaction, custom_id: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        _, staff_id_str, cat_id_str = custom_id.split('_')
        guild = interaction.guild
        
        existing_ticket = discord.utils.find(
            lambda c: c.topic and f"ticket_user_{interaction.user.id}" in c.topic,
            guild.text_channels
        )
        if existing_ticket:
            return await interaction.followup.send(f"You already have a ticket open: {existing_ticket.mention}", ephemeral=True)

        staff_role = guild.get_role(int(staff_id_str))
        category = guild.get_channel(int(cat_id_str))
        if not staff_role or not isinstance(category, discord.CategoryChannel):
            return await interaction.followup.send("Ticket system is misconfigured. Please contact an admin.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
            staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket for {interaction.user.name} | User ID: ticket_user_{interaction.user.id}"
        )
        await interaction.followup.send(f"Ticket created successfully! {channel.mention}", ephemeral=True)
        
        embed = discord.Embed(
            title="Support Ticket",
            description=f"Welcome {interaction.user.mention}! A staff member will be with you shortly. Please describe your issue.",
            color=discord.Color.blurple()
        )
        await channel.send(content=f"{interaction.user.mention} {staff_role.mention}", embed=embed, view=self.TicketActionView())

    async def claim_ticket(self, interaction: discord.Interaction):
        if "ticket-" not in interaction.channel.name: 
            return
        view = self.TicketActionView()
        view.children[0].disabled = True
        await interaction.message.edit(view=view)
        await interaction.response.send_message(f"🙋 Ticket claimed by {interaction.user.mention}.", ephemeral=False)

    async def transcript_ticket(self, interaction: discord.Interaction):
        if "ticket-" not in interaction.channel.name: 
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        transcript_filename = f"{TRANSCRIPTS_DIR}/{interaction.channel.id}.html"
        async with aiofiles.open(transcript_filename, 'w', encoding='utf-8') as f:
            await f.write(f"<html><body><h1>Transcript for #{interaction.channel.name}</h1>")
            async for msg in interaction.channel.history(limit=None, oldest_first=True):
                await f.write(f"<p><b>{msg.author.name}</b> ({msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}): {msg.clean_content}</p>")
            await f.write("</body></html>")
        
        await interaction.followup.send(f"✅ Transcript saved as {transcript_filename}", ephemeral=True)
        os.remove(transcript_filename)

    async def close_ticket(self, interaction: discord.Interaction):
        if "ticket-" not in interaction.channel.name: 
            return
        await interaction.response.send_message("🔒 Closing this ticket in 5 seconds...")
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

    async def mention_admins(self, interaction: discord.Interaction):
        """Mentions everyone with administrator permission in the server."""
        if "ticket-" not in interaction.channel.name: 
            return
        guild = interaction.guild
        admins = [member.mention for member in guild.members if member.guild_permissions.administrator]
        if not admins:
            await interaction.response.send_message("No admins found in this server.", ephemeral=True)
        else:
            mentions = " ".join(admins)
            await interaction.response.send_message(f"👑 Attention Admins: {mentions}", allowed_mentions=discord.AllowedMentions(users=True))

    # -----------------------------
    # Slash Command
    # -----------------------------
    @app_commands.command(name="ticket-setup", description="🛠️ Creates the ticket system panel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticketsetup(self, interaction: discord.Interaction, channel: discord.TextChannel, staff_role: discord.Role, category: discord.CategoryChannel, panel_title: str, panel_description: str):
        embed = discord.Embed(title=panel_title, description=panel_description, color=discord.Color.dark_blue())
        view = self.TicketPanelView(staff_role.id, category.id, panel_title)
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Ticket panel created in {channel.mention}.", ephemeral=True)

# -----------------------------
# Cog Setup
# -----------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
