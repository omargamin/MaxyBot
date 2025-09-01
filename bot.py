# -*- coding: utf-8 -*-

# --- Standard Library Imports ---
from __future__ import annotations
import os
import sys
import json
import asyncio
import re
import logging
import base64
import copy
import signal
from pathlib import Path
from datetime import datetime, UTC
from typing import Dict, Optional, List, Any, Literal

# --- Third-Party Imports ---
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiofiles
import aiohttp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- .env Setup ---
load_dotenv()

# --- Constants ---
# قم بتغيير هذه القيم لتناسب بوتك
OWNER_IDS = {1279500219154956419}  # استخدم مجموعة (set) للأداء الأفضل
DEV_GUILD_ID = 1400861301357678613
STATUS_CHANNEL_ID = 1410018649778950294
DEFAULT_PREFIX = "m!"

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('MaxyBot')

# --- Encryption Functions ---
ENCRYPTION_KEY: Optional[bytes] = None

def setup_encryption_key():
    """
    Initializes the encryption key. Tries to load from .env,
    validates it, or generates a new one if necessary.
    """
    global ENCRYPTION_KEY
    key_str = os.getenv("ENCRYPTION_KEY")
    
    if key_str:
        try:
            decoded_key = base64.urlsafe_b64decode(key_str)
            if len(decoded_key) == 32:
                ENCRYPTION_KEY = decoded_key
                logger.info("Successfully loaded ENCRYPTION_KEY from .env file.")
                return
            else:
                logger.warning("ENCRYPTION_KEY in .env is not 32 bytes. A new key will be generated.")
        except (ValueError, TypeError):
            logger.warning("ENCRYPTION_KEY in .env is invalid. A new key will be generated.")

    logger.info("Generating a new ENCRYPTION_KEY and saving it to .env file...")
    new_key = os.urandom(32)  # 256-bit key
    key_str_to_save = base64.urlsafe_b64encode(new_key).decode('utf-8')
    
    try:
        with open(".env", "a", encoding="utf-8") as f:
            if f.tell() != 0:
                f.write("\n")
            f.write(f"ENCRYPTION_KEY={key_str_to_save}\n")
        
        ENCRYPTION_KEY = new_key
        logger.info("Successfully generated and saved a new ENCRYPTION_KEY.")
    except Exception as e:
        logger.critical(f"FATAL: Could not write ENCRYPTION_KEY to .env file: {e}")
        sys.exit("Cannot run without a persistent encryption key.")

setup_encryption_key()

def encrypt_secret(secret: str) -> str:
    """Encrypts a string using AES-GCM."""
    if not ENCRYPTION_KEY:
        raise ValueError("Encryption key is not set.")
    aesgcm = AESGCM(ENCRYPTION_KEY)
    nonce = os.urandom(12)
    encrypted_data = aesgcm.encrypt(nonce, secret.encode('utf-8'), None)
    return base64.urlsafe_b64encode(nonce + encrypted_data).decode('utf-8')

def decrypt_secret(enc_secret: str) -> str:
    """Decrypts a string using AES-GCM."""
    if not ENCRYPTION_KEY:
        raise ValueError("Encryption key is not set.")
    padding = "=" * (4 - len(enc_secret) % 4)
    data = base64.urlsafe_b64decode(enc_secret + padding)
    nonce, ciphertext = data[:12], data[12:]
    aesgcm = AESGCM(ENCRYPTION_KEY)
    return aesgcm.decrypt(nonce, ciphertext, None).decode('utf-8')

# --- Default Guild Configuration ---
def get_default_config() -> Dict[str, Any]:
    """Returns a deep copy of the default configuration for a guild."""
    return {
        "prefix": DEFAULT_PREFIX,
        "welcome": {"enabled": False, "channel_id": None, "message": "Welcome {user.mention} to {guild.name}!", "embed": {"enabled": True, "title": "New Member!", "description": "We're glad to have you."}},
        "goodbye": {"enabled": False, "channel_id": None, "message": "Goodbye {user.name}!", "embed": {"enabled": True, "title": "Member Left", "description": "We'll miss them."}},
        "logging": {"enabled": False, "channel_id": None, "events": {"message_delete": True, "message_edit": True, "member_join": True, "member_leave": True, "member_update": True, "role_update": True, "channel_update": True, "voice_update": True}},
        "moderation": {"mute_role_id": None, "mod_log_channel_id": None, "allowed_roles": []},
        "automod": {"enabled": True, "anti_link": False, "anti_invite": False, "anti_spam": False, "bad_words_enabled": False, "bad_words_list": []},
        "leveling": {"enabled": True, "levelup_message": "🎉 Congrats {user.mention}, you reached **Level {level}**!", "xp_per_message_min": 15, "xp_per_message_max": 25, "xp_cooldown_seconds": 60},
        "economy": {"enabled": True, "start_balance": 100, "currency_symbol": "🪙", "currency_name": "Maxy Coin"},
        "tickets": {"enabled": False, "category_id": None, "support_role_id": None, "transcript_channel_id": None, "panel_channel_id": None},
        "autorole": {"enabled": False, "human_role_id": None, "bot_role_id": None},
        "starboard": {"enabled": False, "channel_id": None, "star_count": 5},
        "autoresponder": {"enabled": True},
        "disabled_commands": []
    }

# --- UI Views ---
class ShutdownConfirmView(discord.ui.View):
    """A view to confirm the bot shutdown command."""
    def __init__(self, author_id: int):
        super().__init__(timeout=30.0)
        self.value: Optional[bool] = None
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Shutdown", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()

class SyncConfirmView(discord.ui.View):
    """A view to confirm global command synchronization or clearing."""
    def __init__(self, author_id: int, *, is_clearing: bool = False):
        super().__init__(timeout=30.0)
        self.value: Optional[bool] = None
        self.author_id = author_id
        
        # Dynamically change button label and style
        confirm_label = "Confirm Clear" if is_clearing else "Confirm Sync"
        confirm_style = discord.ButtonStyle.danger if is_clearing else discord.ButtonStyle.primary
        self.confirm_button.label = confirm_label
        self.confirm_button.style = confirm_style

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
            return False
        return True

    # This is a placeholder that will be defined below
    # We define it here to add it to the view
    confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.primary)
    
    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        self.stop()
        
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        self.stop()

# --- Main Bot Class ---
class MaxyBot(commands.Bot):
    """
    The main bot class for MaxyBot, handles event listeners,
    command processing, and state management.
    """
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=self.get_prefix_wrapper,
            intents=intents,
            case_insensitive=True,
            owner_ids=OWNER_IDS,
            help_command=None
        )
        self.start_time = datetime.now(UTC)
        self.config_cache: Dict[int, Dict[str, Any]] = {}
        self.snipe_data: Dict[int, Dict[str, Any]] = {}
        self.editsnipe_data: Dict[int, Dict[str, Any]] = {}
        self.xp_cooldowns: Dict[int, Dict[int, datetime]] = {}
        self.logger = logger
        self.http_session: aiohttp.ClientSession
        
        # --- Path Setup ---
        self.root_path = Path.cwd()
        self.data_path = self.root_path / "data"
        self.data_path.mkdir(exist_ok=True)
        self.config_path = self.data_path / "config.json"
        
        # --- Database ---
        from utils.database import DatabaseManager  # Local import to avoid circular dependency issues
        self.db = DatabaseManager(self.data_path / "maxy.db")

    async def setup_hook(self):
        """Initializes async resources, loads extensions (cogs), and syncs commands."""
        self.logger.info("Running setup_hook...")
        
        self.http_session = aiohttp.ClientSession()
        await self.load_config()
        await self._load_all_cogs()
        
        # Removed automatic dev sync from here to give owner full control via command
        self.logger.info("setup_hook completed successfully. Use the 'sync' command to manage slash commands.")

    async def _load_all_cogs(self):
        """Loads all cogs from the 'cogs' directory."""
        self.logger.info("--- Loading Cogs ---")
        cog_dir = self.root_path / 'cogs'
        loaded_cogs, failed_cogs = [], []

        for filename in sorted(os.listdir(cog_dir)):
            if filename.endswith('.py') and not filename.startswith('_'):
                cog_name = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(cog_name)
                    loaded_cogs.append(cog_name)
                except Exception as e:
                    failed_cogs.append(cog_name)
                    self.logger.error(f"❌ Failed to load Cog: {cog_name} | Error: {e}", exc_info=True)
        
        self.logger.info(f"✅ Loaded {len(loaded_cogs)} cogs.")
        if failed_cogs:
            self.logger.warning(f"❌ Failed to load {len(failed_cogs)} cogs: {failed_cogs}")
        self.logger.info("--------------------")

    async def close(self):
        """Gracefully closes all bot resources."""
        self.logger.info("Closing bot resources...")
        if self.auto_save_config.is_running():
            self.auto_save_config.cancel()
        await self.save_config()
        await self.http_session.close()
        await self.db.close()
        await super().close()
        self.logger.info("Bot has been shut down.")

    async def load_config(self):
        """Loads the main configuration file."""
        try:
            async with aiofiles.open(self.config_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                # Store only guild settings, assuming root is {"guild_settings": {...}}
                self.config_cache_from_file = json.loads(content).get("guild_settings", {})
        except (FileNotFoundError, json.JSONDecodeError):
            self.logger.warning("config.json not found or invalid. Starting with an empty config.")
            self.config_cache_from_file = {}

    async def save_config(self):
        """Saves the current configuration to a file atomically."""
        try:
            full_config = {"guild_settings": self.config_cache_from_file}
            temp_path = self.config_path.with_suffix(f"{self.config_path.suffix}.tmp")
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(full_config, indent=4, ensure_ascii=False))
            os.replace(temp_path, self.config_path)
        except Exception as e:
            self.logger.error(f"Failed to save config.json: {e}")

    def get_guild_config(self, guild_id: int) -> dict:
        """
        Retrieves the config for a guild, merging defaults with saved settings.
        Uses a cache for performance.
        """
        if guild_id in self.config_cache:
            return self.config_cache[guild_id]

        guild_id_str = str(guild_id)
        # Start with a fresh copy of the defaults
        final_config = copy.deepcopy(get_default_config())
        saved_settings = self.config_cache_from_file.get(guild_id_str, {})

        def _recursive_update(d, u):
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = _recursive_update(d.get(k, {}), v)
                else:
                    d[k] = v
            return d

        final_config = _recursive_update(final_config, saved_settings)
        self.config_cache[guild_id] = final_config
        return final_config

    async def get_prefix_wrapper(self, bot, message: discord.Message):
        """Dynamically gets the command prefix for a guild."""
        if not message.guild:
            return commands.when_mentioned_or(DEFAULT_PREFIX)(bot, message)
        conf = self.get_guild_config(message.guild.id)
        prefix = conf.get("prefix", DEFAULT_PREFIX)
        return commands.when_mentioned_or(prefix)(bot, message)

    async def on_ready(self):
        """Called when the bot is fully connected and ready."""
        self.logger.info("=" * 40)
        self.logger.info(f"Bot Logged In as: {self.user.name} | {self.user.id}")
        self.logger.info(f"Discord.py Version: {discord.__version__}")
        self.logger.info(f"Serving {len(self.guilds)} guilds.")
        self.logger.info("Maxy Bot is online and operational.")
        self.logger.info("=" * 40)
        
        await self.send_status_message(
            title="✅ System Status: Online",
            description="Maxy Bot is now online and fully operational.",
            color=discord.Color.green()
        )
        
        guild_count = len(self.guilds)
        activity = discord.Activity(type=discord.ActivityType.playing, name=f"/help | serving {guild_count} guilds!")
        await self.change_presence(activity=activity, status=discord.Status.do_not_disturb)

        
        self.auto_save_config.start()

    async def on_message(self, message: discord.Message):
        """Processes messages for commands, automod, and other features."""
        if message.author.bot or not message.guild:
            return

        if (autoresponder_cog := self.get_cog('AutoResponder')):
            if await autoresponder_cog.handle_responses(message):
                return
        
        # Await command processing first to prevent automod on valid commands
        await self.process_commands(message)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """Global prefix command error handler."""
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have the required permissions to use this command.", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("🚫 I don't have the necessary permissions to perform this action.", ephemeral=True)
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ This command is on cooldown. Please try again in {error.retry_after:.2f} seconds.", ephemeral=True)
        else:
            self.logger.error(f"Unhandled prefix command error in '{ctx.command}':", exc_info=error)
            await ctx.send("An unexpected error occurred. The developers have been notified.", ephemeral=True)
    
    async def on_tree_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """Global slash command error handler."""
        # This is the slash-command equivalent of on_command_error
        original_error = getattr(error, 'original', error)
        
        if isinstance(original_error, discord.app_commands.CommandOnCooldown):
             await interaction.response.send_message(f"⏳ This command is on cooldown. Try again in {original_error.retry_after:.2f}s.", ephemeral=True)
        elif isinstance(original_error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You lack the permissions to use this command.", ephemeral=True)
        elif isinstance(original_error, discord.app_commands.BotMissingPermissions):
            await interaction.response.send_message("🚫 I don't have the required permissions to do that.", ephemeral=True)
        else:
            self.logger.error(f"Unhandled slash command error for '{interaction.command.name if interaction.command else 'Unknown'}':", exc_info=original_error)
            # Use followup.send for interactions that might have been deferred
            if interaction.response.is_done():
                await interaction.followup.send("An unexpected error occurred. This has been reported.", ephemeral=True)
            else:
                await interaction.response.send_message("An unexpected error occurred. This has been reported.", ephemeral=True)

    # --- Utility Methods ---
    async def send_status_message(self, title: str, description: str, color: discord.Color):
        """Sends a standardized status message to the designated channel."""
        if not self.is_ready() or not self.user:
            return
        
        try:
            channel = self.get_channel(STATUS_CHANNEL_ID) or await self.fetch_channel(STATUS_CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(UTC))
                embed.set_footer(text=f"Bot ID: {self.user.id}")
                await channel.send(embed=embed)
        except (discord.NotFound, discord.Forbidden) as e:
            self.logger.warning(f"Could not send status message to channel {STATUS_CHANNEL_ID}: {e}")
        except Exception as e:
            self.logger.error(f"An unexpected error occurred while sending status message: {e}", exc_info=True)
    
    # --- Background Tasks ---
    @tasks.loop(minutes=5)
    async def auto_save_config(self):
        """Periodically saves the bot's configuration."""
        await self.save_config()
        self.logger.info("Configuration auto-saved successfully.")

    @auto_save_config.before_loop
    async def before_auto_save(self):
        await self.wait_until_ready()

# --- Owner-Only Cog (could be moved to a file in /cogs) ---
class OwnerCog(commands.Cog, name="Owner"):
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    @commands.command(name="shutdown", hidden=True)
    @commands.is_owner()
    async def shutdown(self, ctx: commands.Context):
        """Shuts down the bot gracefully."""
        view = ShutdownConfirmView(ctx.author.id)
        msg = await ctx.send("Are you sure you want to shut down the bot?", view=view)

        await view.wait()
        if view.value is True:
            await msg.edit(content="⏳ Shutting down...", view=None)
            self.bot.logger.info(f"Shutdown command issued by {ctx.author}. Beginning shutdown.")
            await self.bot.close()
        elif view.value is False:
            await msg.edit(content="✅ Shutdown cancelled.", view=None)
        else:
            await msg.edit(content="⚠️ Shutdown confirmation timed out.", view=None)
    
    @commands.command(name="sync", hidden=True)
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, scope: Optional[Literal["current_guild", "clear"]] = None):
        """
        Synchronizes slash commands with Discord safely.
        
        Scopes:
        - (none): Syncs all commands globally. Requires confirmation.
        - current_guild: Syncs commands to the current server immediately.
        - clear: Clears all global commands. Requires confirmation.
        """
        
        if scope == "current_guild":
            if not ctx.guild:
                await ctx.send("❌ This command can only be used in a server.")
                return
            
            msg = await ctx.send(f"⏳ Syncing commands to **{ctx.guild.name}**...")
            try:
                synced = await self.bot.tree.sync(guild=ctx.guild)
                await msg.edit(content=f"✅ Synced **{len(synced)}** commands to this guild.")
                self.bot.logger.info(f"Commands synced to guild {ctx.guild.id} by {ctx.author}.")
            except Exception as e:
                await msg.edit(content=f"❌ An error occurred: `{e}`")
                self.bot.logger.error(f"Failed to sync to guild {ctx.guild.id}: {e}", exc_info=True)
            return

        # Logic for global sync and clear, which require confirmation
        is_clearing = scope == "clear"
        action_description = "clear all global commands" if is_clearing else "sync all commands globally"
        confirm_message = f"**Warning:** Are you sure you want to {action_description}?\nThis is a global action and may take up to an hour to update everywhere."

        view = SyncConfirmView(ctx.author.id, is_clearing=is_clearing)
        msg = await ctx.send(confirm_message, view=view)

        await view.wait()

        if view.value is True:
            action_in_progress = "Clearing" if is_clearing else "Syncing"
            await msg.edit(content=f"⏳ {action_in_progress} global commands...", view=None)
            
            try:
                if is_clearing:
                    self.bot.tree.clear_commands(guild=None)
                    await self.bot.tree.sync()
                    await msg.edit(content="🗑️ Successfully cleared all global commands.")
                    self.bot.logger.info(f"Global commands cleared by {ctx.author}.")
                else: # Global Sync
                    synced = await self.bot.tree.sync()
                    await msg.edit(content=f"✅ Synced **{len(synced)}** commands globally.")
                    self.bot.logger.info(f"Global commands synced by {ctx.author}.")
            except Exception as e:
                await msg.edit(content=f"❌ An error occurred during the global operation: `{e}`")
                self.bot.logger.error(f"Failed to {scope or 'sync'} global commands: {e}", exc_info=True)

        elif view.value is False:
            await msg.edit(content="✅ Operation cancelled.", view=None)
        else: # Timeout
            await msg.edit(content="⚠️ Confirmation timed out. Operation cancelled.", view=None)

    @commands.group(name="cog", hidden=True, invoke_without_command=True)
    @commands.is_owner()
    async def cog(self, ctx: commands.Context):
        """Base command for cog management."""
        await ctx.send("Invalid cog command. Use `load`, `unload`, or `reload`.")

    @cog.command(name="load")
    async def load_cog(self, ctx: commands.Context, *, cog_name: str):
        """Loads a cog."""
        try:
            await self.bot.load_extension(f"cogs.{cog_name}")
            await ctx.send(f"✅ Successfully loaded cog: `{cog_name}`")
        except Exception as e:
            await ctx.send(f"❌ Failed to load cog `{cog_name}`: `{e}`")

    @cog.command(name="unload")
    async def unload_cog(self, ctx: commands.Context, *, cog_name: str):
        """Unloads a cog."""
        try:
            await self.bot.unload_extension(f"cogs.{cog_name}")
            await ctx.send(f"✅ Successfully unloaded cog: `{cog_name}`")
        except Exception as e:
            await ctx.send(f"❌ Failed to unload cog `{cog_name}`: `{e}`")
            
    @cog.command(name="reload")
    async def reload_cog(self, ctx: commands.Context, *, cog_name: str):
        """Reloads a cog."""
        try:
            await self.bot.reload_extension(f"cogs.{cog_name}")
            await ctx.send(f"🔄 Successfully reloaded cog: `{cog_name}`")
        except Exception as e:
            await ctx.send(f"❌ Failed to reload cog `{cog_name}`: `{e}`")

# --- Bot Execution ---
async def main():
    """The main entry point for running the bot."""
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        logger.critical("FATAL: DISCORD_BOT_TOKEN is not set in the .env file!")
        return

    bot = MaxyBot()
    
    async def shutdown_handler(sig: signal.Signals):
        logger.warning(f"Received shutdown signal ({sig.name}). Initiating graceful shutdown...")
        await bot.send_status_message(
            "⚠️ System Status: Shutting Down",
            f"Bot is shutting down due to signal {sig.name}.",
            discord.Color.orange()
        )
        await bot.close()


    try:
        await bot.add_cog(OwnerCog(bot))
        await bot.db.init()
        await bot.start(TOKEN)
    except discord.errors.LoginFailure:
        logger.critical("Login Error: The DISCORD_BOT_TOKEN is invalid.")
    except Exception as e:
        logger.critical(f"An unexpected error occurred while running the bot: {e}", exc_info=True)
    finally:
        if not bot.is_closed():
            await bot.send_status_message(
                "❌ System Status: Final Shutdown",
                "The bot is performing a final shutdown.",
                discord.Color.red()
            )
            await bot.close()
        logger.info("Bot process has terminated.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Shutdown requested via Ctrl+C. Bot is shutting down gracefully.")