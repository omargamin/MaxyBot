import os
from pathlib import Path
import json
from textwrap import dedent
import sys
import requests
import logging
import time
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- PROJECT STRUCTURE AND FILE CONTENT DEFINITIONS ---

PROJECT_STRUCTURE = {
    "dashboard": {
        "views": {
            "partials": [
                "header.ejs",
                "footer.ejs",
                "sidebar.ejs",
                "user_dropdown.ejs"
            ],
            "pages": [
                "index.ejs",
                "dashboard.ejs",
                "guild_settings.ejs"
            ]
        },
        "public": {
            "css": ["style.css"],
            "js": ["main.js"],
            "img": []
        },
        "routes": [],
    },
    "data": {
        "transcripts": [".keep"],
        "backups": [".keep"]
    },
    "cogs": [],
    "utils": ["__init__.py"],
    "assets": {
        "fonts": [],
        "images": {
            "profile_backgrounds": [],
            "templates": []
        },
        "json": []
    }
}

BOT_PY_CONTENT = r"""
from __future__ import annotations
import os
import sys
import json
import asyncio
import random
import re
from pathlib import Path
from datetime import datetime, timedelta, UTC
from typing import Dict, Optional, List, Union, Any
import logging

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiofiles
import aiohttp

from utils.database import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('MaxyBot')

load_dotenv()

def get_default_config() -> Dict[str, Any]:
    return {
        "prefix": "m!",
        "welcome": {
            "enabled": False, "channel_id": None, "message": "Welcome {user.mention} to {guild.name}!",
            "embed": {"enabled": True, "title": "New Member!", "description": "We're glad to have you."}
        },
        "goodbye": {
            "enabled": False, "channel_id": None, "message": "Goodbye {user.name}!",
            "embed": {"enabled": True, "title": "Member Left", "description": "We'll miss them."}
        },
        "logging": {
            "enabled": False, "channel_id": None,
            "events": {
                "message_delete": True, "message_edit": True, "member_join": True,
                "member_leave": True, "member_update": True, "role_update": True,
                "channel_update": True, "voice_update": True
            }
        },
        "moderation": {
            "mute_role_id": None, "mod_log_channel_id": None,
            "allowed_roles": []
        },
        "automod": {
            "enabled": True, "anti_link": False, "anti_invite": False,
            "anti_spam": False, "bad_words_enabled": False, "bad_words_list": []
        },
        "leveling": {
            "enabled": True,
            "levelup_message": "🎉 Congrats {user.mention}, you reached **Level {level}**!",
            "xp_per_message_min": 15,
            "xp_per_message_max": 25,
            "xp_cooldown_seconds": 60
        },
        "economy": {"enabled": True, "start_balance": 100, "currency_symbol": "🪙", "currency_name": "Maxy Coin"},
        "tickets": {"enabled": False, "category_id": None, "support_role_id": None, "transcript_channel_id": None, "panel_channel_id": None},
        "autorole": {"enabled": False, "human_role_id": None, "bot_role_id": None},
        "starboard": {"enabled": False, "channel_id": None, "star_count": 5},
        "autoresponder": {"enabled": True},
        "disabled_commands": []
    }

class MaxyBot(commands.Bot):
    def __init__(self):
        self.config_cache_from_file: dict = {}
self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=self.get_prefix_wrapper,
            intents=intents,
            case_insensitive=True,
            help_command=None
        )
        self.persistent_views_added = False
        self.start_time = datetime.now(UTC)
        self.config_cache: Dict[int, Dict[str, Any]] = {}
        self.snipe_data: Dict[int, Dict[str, Any]] = {}
        self.editsnipe_data: Dict[int, Dict[str, Any]] = {}
        self.xp_cooldowns: Dict[int, Dict[int, datetime]] = {}
        self.logger = logger
        self.http_session = aiohttp.ClientSession()
        self.root_path = Path.cwd()
        self.data_path = self.root_path / "data"
        self.config_path = self.data_path / "config.json"
        self.db = DatabaseManager(self.data_path / "maxy.db")

    async def setup_hook(self):
        logger.info("Running setup_hook...")
        await self.load_config()

        cog_dir = self.root_path / 'cogs'
        for filename in os.listdir(cog_dir):
            if filename.endswith('.py') and not filename.startswith('__') and filename != 'utils.py':
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    logger.info(f"Loaded cog: {filename}")
                except Exception as e:
                    logger.error(f"Failed to load cog {filename}: {e}", exc_info=True)

        if not self.persistent_views_added:
            ticket_cog = self.get_cog("Tickets")
            if ticket_cog:
                self.add_view(ticket_cog.TicketPanelView(self))
                self.add_view(ticket_cog.CloseTicketView(self))

            giveaway_cog = self.get_cog("Giveaways")
            if giveaway_cog:
                active_giveaways = await self.db.fetchall("SELECT message_id FROM giveaways WHERE is_ended = 0")
                for g in active_giveaways:
                    self.add_view(giveaway_cog.GiveawayJoinView(self, g['message_id']), message_id=g['message_id'])

            self.persistent_views_added = True

        await self.sync_commands()

    async def sync_commands(self):
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            try:
                guild_obj = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
                logger.info(f"Commands synced to development guild {guild_id}")
            except (ValueError, TypeError):
                logger.error("Invalid GUILD_ID in .env file. It must be an integer. Syncing globally.")
                await self.tree.sync()
        else:
            await self.tree.sync()
            logger.info("Commands synced globally.")

    async def close(self):
        await self.http_session.close()
        await self.db.close()
        await super().close()

    async def load_config(self):
        try:
            async with aiofiles.open(self.config_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                self.config_data = json.loads(content).get("guild_settings", {})
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("config.json not found or invalid. Starting with empty config.")
            self.config_data = {}

    async def save_config(self):
        try:
            full_config = {"guild_settings": self.config_data}
            temp_path = self.config_path.with_suffix(f"{self.config_path.suffix}.tmp")
            async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(full_config, indent=4))
            os.replace(temp_path, self.config_path)
        except Exception as e:
            logger.error(f"Failed to save config.json: {e}")

    def get_guild_config(self, guild_id: int) -> dict:
        if guild_id in self.config_cache:
            return self.config_cache[guild_id]

        guild_id_str = str(guild_id)
        guild_config = json.loads(json.dumps(get_default_config()))
        saved_settings = self.config_data.get(guild_id_str, {})

        def update_dict(d, u):
            for k, v in u.items():
                if isinstance(v, dict):
                    d[k] = update_dict(d.get(k, {}), v)
                else:
                    d[k] = v
            return d

        final_config = update_dict(guild_config, saved_settings)
        self.config_cache[guild_id] = final_config
        return final_config

    async def get_prefix_wrapper(self, bot, message):
        if not message.guild:
            return commands.when_mentioned_or("m!")(bot, message)

        conf = self.get_guild_config(message.guild.id)
        prefix = conf.get("prefix", "m!")
        return commands.when_mentioned_or(prefix)(bot, message)

    async def on_ready(self):
        logger.info("=" * 40)
        logger.info(f"Bot Logged In as: {self.user.name} | {self.user.id}")
        logger.info(f"Discord.py Version: {discord.__version__}")
        logger.info(f"Serving {len(self.guilds)} guilds.")
        logger.info(f"Maxy Bot is online and operational.")
        logger.info("Dashboard should be running on http://localhost:3000")
        logger.info("=" * 40)
        await self.change_presence(activity=discord.Game(name="/help | maxybot.com"))
        self.auto_save_config.start()

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        conf = self.get_guild_config(message.guild.id)

        afk_cog = self.get_cog('Utilities')
        if afk_cog:
            await afk_cog.handle_afk_return(message)
            await afk_cog.handle_afk_mention(message)

        autoresponder_cog = self.get_cog('AutoResponder')
        if autoresponder_cog:
            if await autoresponder_cog.handle_responses(message):
                return

        automod_conf = conf.get('automod', {})
        if automod_conf.get('enabled'):
            if not message.author.guild_permissions.manage_messages:
                if automod_conf.get('anti_invite'):
                    if re.search(r"(discord\.gg/|discord\.com/invite/)", message.content):
                        await message.delete()
                        await message.channel.send(f"🚫 {message.author.mention}, server invites are not allowed here!", delete_after=10)
                        return
                if automod_conf.get('anti_link'):
                    if re.search(r"https?://", message.content):
                        await message.delete()
                        await message.channel.send(f"🚫 {message.author.mention}, links are not allowed here!", delete_after=10)
                        return

        leveling_conf = conf.get('leveling', {})
        if leveling_conf.get('enabled'):
            now = datetime.now(UTC)
            guild_cooldowns = self.xp_cooldowns.setdefault(message.guild.id, {})
            user_last_xp = guild_cooldowns.get(message.author.id)
            cooldown_seconds = leveling_conf.get('xp_cooldown_seconds', 60)

            if user_last_xp and now < user_last_xp + timedelta(seconds=cooldown_seconds):
                pass
            else:
                guild_cooldowns[message.author.id] = now
                xp_to_add = random.randint(leveling_conf.get('xp_per_message_min', 15), leveling_conf.get('xp_per_message_max', 25))
                leveling_cog = self.get_cog('Leveling')
                if leveling_cog:
                    await leveling_cog.add_xp(message.guild.id, message.author.id, xp_to_add, message.channel, message.author)

        await self.process_commands(message)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have the required permissions to use this command.")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send("🚫 I don't have the necessary permissions to perform this action.")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ This command is on cooldown. Please try again in {error.retry_after:.2f} seconds.")
        else:
            logger.error(f"Unhandled prefix command error in '{ctx.command}':", exc_info=error)
            await ctx.send("An unexpected error occurred. The developers have been notified.")

    async def on_member_join(self, member: discord.Member):
        conf = self.get_guild_config(member.guild.id)

        autorole_conf = conf.get('autorole', {})
        if autorole_conf.get('enabled'):
            role_id = autorole_conf.get('bot_role_id') if member.bot else autorole_conf.get('human_role_id')
            if role_id:
                try:
                    role = member.guild.get_role(int(role_id))
                    if role:
                        await member.add_roles(role, reason="Autorole on join")
                except (ValueError, TypeError, discord.Forbidden, discord.HTTPException) as e:
                    logger.error(f"Failed to assign autorole in guild {member.guild.id}: {e}")

        welcome_conf = conf.get('welcome', {})
        if not welcome_conf.get('enabled'):
            return

        channel_id = welcome_conf.get('channel_id')
        try:
            channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        except (ValueError, TypeError):
            channel = None

        if not channel:
            return

        message = welcome_conf.get('message', "Welcome {user.mention}!").format(user=member, guild=member.guild)
        await channel.send(message)

    async def on_member_remove(self, member: discord.Member):
        conf = self.get_guild_config(member.guild.id)
        goodbye_conf = conf.get('goodbye', {})
        if not goodbye_conf.get('enabled'):
            return
        channel_id = goodbye_conf.get('channel_id')
        try:
            channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        except (ValueError, TypeError):
            channel = None
        if not channel:
            return
        message = goodbye_conf.get('message', "Goodbye {user.name}.").format(user=member, guild=member.guild)
        await channel.send(message)

    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        self.snipe_data[message.channel.id] = {
            'content': message.content,
            'author': message.author,
            'timestamp': datetime.now(UTC),
            'attachments': [att.url for att in message.attachments]
        }
        logging_cog = self.get_cog("Logging")
        if logging_cog:
            await logging_cog.log_message_delete(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or before.content == after.content:
            return
        self.editsnipe_data[before.channel.id] = {
            'before_content': before.content,
            'after_content': after.content,
            'author': before.author,
            'timestamp': datetime.now(UTC)
        }
        logging_cog = self.get_cog("Logging")
        if logging_cog:
            await logging_cog.log_message_edit(before, after)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) != '⭐':
            return
        if not payload.guild_id:
            return

        conf = self.get_guild_config(payload.guild_id)
        star_conf = conf.get('starboard', {})
        if not star_conf.get('enabled'):
            return

        star_channel_id = star_conf.get('channel_id')
        star_count = star_conf.get('star_count', 5)

        if not star_channel_id:
            return

        star_channel = self.get_channel(int(star_channel_id))
        if not star_channel:
            return

        try:
            channel = self.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        if message.author.id == payload.user_id:
            return

        for reaction in message.reactions:
            if str(reaction.emoji) == '⭐' and reaction.count >= star_count:
                star_entry = await self.db.fetchone("SELECT starboard_message_id FROM starboard WHERE original_message_id = ?", (message.id,))
                if star_entry:
                    try:
                        star_message = await star_channel.fetch_message(star_entry['starboard_message_id'])
                        await star_message.edit(content=f"⭐ **{reaction.count}** | {channel.mention}")
                    except discord.NotFound:
                        await self.db.execute("DELETE FROM starboard WHERE original_message_id = ?", (message.id,))
                    return

                embed = discord.Embed(
                    description=message.content,
                    color=discord.Color.gold(),
                    timestamp=message.created_at
                )
                embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                embed.add_field(name="Original", value=f"[Jump to Message]({message.jump_url})", inline=False)
                if message.attachments:
                    embed.set_image(url=message.attachments[0].url)

                star_message = await star_channel.send(f"⭐ **{reaction.count}** | {channel.mention}", embed=embed)
                await self.db.execute("INSERT INTO starboard (original_message_id, starboard_message_id, guild_id) VALUES (?, ?, ?)", (message.id, star_message.id, payload.guild_id))
                break

    async def on_member_update(self, before, after):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_member_update(before, after)

    async def on_guild_role_create(self, role):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_role_create(role)

    async def on_guild_role_delete(self, role):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_role_delete(role)

    async def on_guild_role_update(self, before, after):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_role_update(before, after)

    async def on_guild_channel_create(self, channel):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_channel_create(channel)

    async def on_guild_channel_delete(self, channel):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_channel_delete(channel)

    async def on_guild_channel_update(self, before, after):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_channel_update(before, after)

    async def on_voice_state_update(self, member, before, after):
        logging_cog = self.get_cog("Logging")
        if logging_cog: await logging_cog.log_voice_state_update(member, before, after)

    @tasks.loop(minutes=5)
    async def auto_save_config(self):
        await self.save_config()

    @auto_save_config.before_loop
    async def before_auto_save(self):
        await self.wait_until_ready()

async def main():
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        sys.exit("FATAL: DISCORD_BOT_TOKEN is not set in the .env file!")
    bot = MaxyBot()
    await bot.db.init()
    try:
        await bot.start(TOKEN)
    except discord.errors.LoginFailure:
        logger.critical("Login Error: The DISCORD_BOT_TOKEN is invalid.")
    except Exception as e:
        logger.critical(f"An unexpected error occurred while running the bot: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested.")
"""

DATABASE_PY_CONTENT = r"""
import aiosqlite
from pathlib import Path
import logging

logger = logging.getLogger('DatabaseManager')

class DatabaseManager:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db = None

    async def _get_db(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
        return self._db

    async def init(self):
        db = await self._get_db()
        await db.execute('''
            CREATE TABLE IF NOT EXISTS economy (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                wallet INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS leveling (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                warn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS afk (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_inventory (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                item_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                is_active INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id, item_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS giveaways (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                prize TEXT NOT NULL,
                end_timestamp REAL NOT NULL,
                winner_count INTEGER NOT NULL,
                is_ended INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS giveaway_entrants (
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (message_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT DEFAULT 'open'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS auto_responses (
                response_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                trigger TEXT NOT NULL,
                response TEXT NOT NULL,
                creator_id INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS starboard (
                original_message_id INTEGER PRIMARY KEY,
                starboard_message_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS level_rewards (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, level)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                remind_content TEXT NOT NULL,
                remind_timestamp REAL NOT NULL
            )
        ''')
        await db.commit()
        logger.info("Database initialized and tables created.")

    async def execute(self, query: str, params=()):
        db = await self._get_db()
        await db.execute(query, params)
        await db.commit()

    async def fetchone(self, query: str, params=()):
        db = await self._get_db()
        async with db.execute(query, params) as cursor:
            return await cursor.fetchone()

    async def fetchall(self, query: str, params=()):
        db = await self._get_db()
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database connection closed.")
"""

SERVER_JS_CONTENT = r"""
require('dotenv').config();
const express = require('express');
const session = require('express-session');
const DiscordOauth2 = require('discord-oauth2');
const path = require('path');
const fs = require('fs').promises;
const crypto = require('crypto');

const app = express();
const oauth = new DiscordOauth2({
    clientId: process.env.DISCORD_CLIENT_ID,
    clientSecret: process.env.DISCORD_CLIENT_SECRET,
    redirectUri: process.env.OAUTH_REDIRECT_URI,
});

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views/pages'));

app.use(express.static(path.join(__dirname, 'public')));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use(session({
    secret: process.env.SESSION_SECRET || crypto.randomBytes(20).toString('hex'),
    resave: false,
    saveUninitialized: false,
    cookie: { maxAge: 604800000 }
}));

const dataPath = path.join(__dirname, '..', 'data', 'config.json');

const getNested = (obj, path, defaultValue = undefined) => {
    const value = path.split('.').reduce((acc, part) => acc && acc[part], obj);
    return value !== undefined ? value : defaultValue;
};

const setNested = (obj, path, value) => {
    const keys = path.split('.');
    let current = obj;
    for (let i = 0; i < keys.length - 1; i++) {
        const key = keys[i];
        if (typeof current[key] !== 'object' || current[key] === null) {
            current[key] = {};
        }
        current = current[key];
    }
    current[keys[keys.length - 1]] = value;
    return obj;
};

async function readConfig() {
    try {
        const data = await fs.readFile(dataPath, 'utf8');
        return JSON.parse(data);
    } catch (err) {
        console.error("Error reading config.json:", err);
        return { guild_settings: {} };
    }
}

async function writeConfig(config) {
    try {
        const tempPath = dataPath + '.tmp';
        await fs.writeFile(tempPath, JSON.stringify(config, null, 4), 'utf8');
        await fs.rename(tempPath, dataPath);
    } catch (err) {
        console.error("Error writing to config.json:", err);
    }
}

const authRequired = (req, res, next) => {
    if (!req.session.user) return res.redirect('/login');
    next();
};

app.get('/', (req, res) => {
    res.render('index', { user: req.session.user });
});

app.get('/login', (req, res) => {
    const url = oauth.generateAuthUrl({
        scope: ['identify', 'guilds'],
        state: crypto.randomBytes(16).toString('hex'),
    });
    res.redirect(url);
});

app.get('/logout', (req, res) => {
    req.session.destroy();
    res.redirect('/');
});

app.get('/callback', async (req, res) => {
    if (!req.query.code) return res.redirect('/login');
    try {
        const tokenResponse = await oauth.tokenRequest({
            code: req.query.code,
            scope: 'identify guilds',
            grantType: 'authorization_code',
        });

        const [user, guilds] = await Promise.all([
            oauth.getUser(tokenResponse.access_token),
            oauth.getUserGuilds(tokenResponse.access_token)
        ]);

        req.session.user = user;
        req.session.guilds = guilds;
        res.redirect('/dashboard');

    } catch (err) {
        console.error("OAuth Callback Error:", err);
        res.status(500).send("An error occurred during authentication.");
    }
});

app.get('/dashboard', authRequired, (req, res) => {
    const adminGuilds = req.session.guilds.filter(g => (g.permissions & 0x8) === 0x8);
    res.render('dashboard', {
        user: req.session.user,
        guilds: adminGuilds
    });
});

app.get('/dashboard/:guildId', authRequired, async (req, res) => {
    const guild = req.session.guilds.find(g => g.id === req.params.guildId);
    if (!guild || !(guild.permissions & 0x8)) {
        return res.status(403).send("You don't have permission to manage this server.");
    }
    const config = await readConfig();
    const guildConfig = config.guild_settings[guild.id] || {};

    res.render('guild_settings', {
        user: req.session.user,
        guild,
        config: guildConfig,
        getNested
    });
});

app.post('/api/settings/:guildId', authRequired, async (req, res) => {
    const guild = req.session.guilds.find(g => g.id === req.params.guildId);
    if (!guild || !(guild.permissions & 0x8)) {
        return res.status(403).json({ message: "Unauthorized" });
    }

    const config = await readConfig();
    if (!config.guild_settings) {
        config.guild_settings = {};
    }
    if (!config.guild_settings[guild.id]) {
        config.guild_settings[guild.id] = {};
    }
    const guildSettings = config.guild_settings[guild.id];

    const checkboxes = [
        'welcome.enabled', 'goodbye.enabled', 'logging.enabled', 'automod.enabled', 'automod.anti_link', 
        'automod.anti_invite', 'leveling.enabled', 'economy.enabled', 'tickets.enabled', 
        'autorole.enabled', 'starboard.enabled', 'autoresponder.enabled'
    ];

    checkboxes.forEach(key => {
        setNested(guildSettings, key, false);
    });

    for (const key in req.body) {
        let value = req.body[key];
        if (value === 'on') {
            value = true;
        }

        if (!isNaN(value) && typeof value === 'string' && value.trim() !== '') {
             const numericKeys = [
                 'leveling.xp_per_message_min', 'leveling.xp_per_message_max',
                 'leveling.xp_cooldown_seconds', 'starboard.star_count', 'economy.start_balance'
             ];
             if(numericKeys.includes(key)) {
                 value = Number(value);
             }
        }
        setNested(guildSettings, key, value);
    }

    config.guild_settings[guild.id] = guildSettings;
    await writeConfig(config);
    res.json({ status: 'success', message: 'Settings saved successfully!' });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Maxy Bot Dashboard is running on http://localhost:${PORT}`);
});
"""

HEADER_EJS = r"""
<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Maxy Bot Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="/css/style.css">
</head>
<body>
    <div class="d-flex" id="wrapper">
"""

FOOTER_EJS = r"""
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="/js/main.js"></script>
</body>
</html>
"""

SIDEBAR_EJS = r"""
<div class="sidebar-wrapper" id="sidebar-wrapper">
    <div class="sidebar-heading">
        <i class="bi bi-robot me-2"></i>Maxy Bot
    </div>
    <div class="list-group list-group-flush my-3">
        <a href="/dashboard" class="list-group-item list-group-item-action <%= (typeof guild === 'undefined') ? 'active' : '' %>">
            <i class="bi bi-house-door-fill me-2"></i> Servers
        </a>
        <% if (typeof guild !== 'undefined') { %>
            <hr class="mx-3 my-2" style="border-color: var(--border-color);">
            <h6 class="sidebar-subheading px-3 mt-2 mb-1 text-muted text-uppercase">MANAGE: <%= guild.name %></h6>
            <a href="/dashboard/<%= guild.id %>" class="list-group-item list-group-item-action active">
                <i class="bi bi-gear-fill me-2"></i> General Settings
            </a>
        <% } %>
    </div>
</div>
"""

USER_DROPDOWN_EJS = r"""
<div class="dropdown">
    <a href="#" class="d-flex align-items-center text-white text-decoration-none dropdown-toggle" id="userDropdown" data-bs-toggle="dropdown" aria-expanded="false">
        <img src="https://cdn.discordapp.com/avatars/<%= user.id %>/<%= user.avatar %>.png?size=64" alt="" width="32" height="32" class="rounded-circle me-2">
        <strong><%= user.username %></strong>
    </a>
    <ul class="dropdown-menu dropdown-menu-dark text-small shadow dropdown-menu-end" aria-labelledby="userDropdown">
        <li><a class="dropdown-item" href="/logout"><i class="bi bi-box-arrow-right me-2"></i>Sign out</a></li>
    </ul>
</div>
"""

INDEX_EJS = r"""
<%- include('../partials/header') %>
<div class="page-content-wrapper" id="page-content-wrapper-full">
    <div class="container-fluid vh-100 d-flex justify-content-center align-items-center">
        <div class="text-center">
            <h1 class="display-3 fw-bold" style="color: var(--primary-color);">Maxy Bot</h1>
            <p class="lead my-4">The ultimate, all-in-one Discord bot. <br> Fully customizable, powerful, and ready to enhance your server.</p>
            <% if (user) { %>
                <a href="/dashboard" class="btn btn-primary btn-lg"><i class="bi bi-speedometer2 me-2"></i> Go to Dashboard</a>
                <a href="/logout" class="btn btn-outline-danger btn-lg ms-2"><i class="bi bi-box-arrow-right me-2"></i> Logout</a>
            <% } else { %>
                <a href="/login" class="btn btn-primary btn-lg"><i class="bi bi-discord me-2"></i> Login with Discord</a>
            <% } %>
        </div>
    </div>
</div>
<%- include('../partials/footer') %>
"""

DASHBOARD_EJS = r"""
<%- include('../partials/header') %>
<%- include('../partials/sidebar') %>

<div id="page-content-wrapper">
    <nav class="navbar navbar-expand-lg">
        <div class="container-fluid">
            <h2 class="navbar-brand m-0">Select a Server</h2>
            <%- include('../partials/user_dropdown') %>
        </div>
    </nav>

    <div class="row row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-lg-4 row-cols-xl-5 g-4">
        <% guilds.forEach(g => { %>
            <div class="col">
                <a href="/dashboard/<%= g.id %>" class="text-decoration-none">
                    <div class="card guild-card h-100">
                         <img src="https://cdn.discordapp.com/icons/<%= g.id %>/<%= g.icon %>.png?size=128" alt="Server Icon" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png';">
                        <div class="card-body">
                            <h5 class="card-title mt-2"><%= g.name %></h5>
                        </div>
                    </div>
                </a>
            </div>
        <% }); %>
    </div>
</div>

<%- include('../partials/footer') %>
"""

GUILD_SETTINGS_EJS = r"""
<%- include('../partials/header') %>
<%- include('../partials/sidebar', { guild: guild }) %>

<div id="page-content-wrapper">
    <nav class="navbar navbar-expand-lg">
        <div class="container-fluid">
            <h2 class="navbar-brand m-0 d-flex align-items-center">
                <img src="https://cdn.discordapp.com/icons/<%= guild.id %>/<%= guild.icon %>.png?size=64" class="rounded-circle me-3" width="45" height="45" alt="Guild Icon" onerror="this.src='https://cdn.discordapp.com/embed/avatars/0.png';">
                <span><%= guild.name %></span>
            </h2>
            <%- include('../partials/user_dropdown') %>
        </div>
    </nav>
    
    <div class="toast-container position-fixed top-0 end-0 p-3" style="z-index: 1100">
        <div id="notificationToast" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header">
                <strong class="me-auto">Notification</strong>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body"></div>
        </div>
    </div>

    <form class="ajax-form" action="/api/settings/<%= guild.id %>" method="POST">
        <nav>
            <div class="nav nav-tabs" id="nav-tab" role="tablist">
                <button class="nav-link active" id="nav-general-tab" data-bs-toggle="tab" data-bs-target="#nav-general" type="button" role="tab">General</button>
                <button class="nav-link" id="nav-modules-tab" data-bs-toggle="tab" data-bs-target="#nav-modules" type="button" role="tab">Modules</button>
                <button class="nav-link" id="nav-welcome-tab" data-bs-toggle="tab" data-bs-target="#nav-welcome" type="button" role="tab">Welcome & Goodbye</button>
                <button class="nav-link" id="nav-leveling-tab" data-bs-toggle="tab" data-bs-target="#nav-leveling" type="button" role="tab">Leveling</button>
                <button class="nav-link" id="nav-moderation-tab" data-bs-toggle="tab" data-bs-target="#nav-moderation" type="button" role="tab">Moderation</button>
                <button class="nav-link" id="nav-starboard-tab" data-bs-toggle="tab" data-bs-target="#nav-starboard" type="button" role="tab">Starboard</button>
            </div>
        </nav>
        <div class="tab-content" id="nav-tabContent">
            <div class="tab-pane fade show active" id="nav-general" role="tabpanel">
                <h4 class="mb-3">Core Settings</h4>
                <div class="mb-3">
                    <label for="prefix" class="form-label">Command Prefix</label>
                    <input type="text" class="form-control" name="prefix" value="<%= getNested(config, 'prefix', 'm!') %>">
                    <div class="form-text">Note: Slash commands (/) are primary. This prefix is for legacy message commands.</div>
                </div>
            </div>
            <div class="tab-pane fade" id="nav-modules" role="tabpanel">
                <h4 class="mb-4">Module Toggles</h4>
                <div class="row">
                    <% const modules = ['economy', 'tickets', 'autorole', 'logging', 'autoresponder', 'leveling', 'welcome', 'goodbye', 'starboard']; %>
                    <% modules.forEach(module => { %>
                    <div class="col-md-6 col-lg-4 mb-3">
                        <div class="form-check form-switch fs-5">
                            <input class="form-check-input" type="checkbox" role="switch" name="<%= module %>.enabled" <%= getNested(config, module + '.enabled') ? 'checked' : '' %>>
                            <label class="form-check-label text-capitalize"><%= module.replace('autoresponder', 'Auto Responder') %> System</label>
                        </div>
                    </div>
                    <% }); %>
                </div>
            </div>
            <div class="tab-pane fade" id="nav-welcome" role="tabpanel">
                <div class="row">
                    <div class="col-md-6">
                        <h4 class="mb-3">Welcome System</h4>
                        <div class="mb-3">
                            <label class="form-label">Welcome Channel ID</label>
                            <input type="text" class="form-control" name="welcome.channel_id" placeholder="Enter Channel ID" value="<%= getNested(config, 'welcome.channel_id', '') %>">
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Welcome Message</label>
                            <textarea class="form-control" name="welcome.message" rows="3"><%= getNested(config, 'welcome.message', '') %></textarea>
                            <div class="form-text">{user.mention}, {user.name}, {guild.name}</div>
                        </div>
                    </div>
                    <div class="col-md-6">
                        <h4 class="mb-3">Goodbye System</h4>
                        <div class="mb-3">
                            <label class="form-label">Goodbye Channel ID</label>
                            <input type="text" class="form-control" name="goodbye.channel_id" placeholder="Enter Channel ID" value="<%= getNested(config, 'goodbye.channel_id', '') %>">
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Goodbye Message</label>
                            <textarea class="form-control" name="goodbye.message" rows="3"><%= getNested(config, 'goodbye.message', '') %></textarea>
                            <div class="form-text">{user.name}, {guild.name}</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="tab-pane fade" id="nav-leveling" role="tabpanel">
                <h4 class="mb-3">Leveling System</h4>
                <div class="mb-4">
                    <label class="form-label">Level-Up Message</label>
                    <textarea class="form-control" name="leveling.levelup_message" rows="2"><%= getNested(config, 'leveling.levelup_message', '') %></textarea>
                    <div class="form-text">{user.mention}, {level}</div>
                </div>
                <div class="row">
                    <div class="col-md-4 mb-3">
                        <label class="form-label">Min XP / Msg</label>
                        <input type="number" class="form-control" name="leveling.xp_per_message_min" value="<%= getNested(config, 'leveling.xp_per_message_min', 15) %>">
                    </div>
                    <div class="col-md-4 mb-3">
                        <label class="form-label">Max XP / Msg</label>
                        <input type="number" class="form-control" name="leveling.xp_per_message_max" value="<%= getNested(config, 'leveling.xp_per_message_max', 25) %>">
                    </div>
                    <div class="col-md-4 mb-3">
                        <label class="form-label">XP Cooldown (sec)</label>
                        <input type="number" class="form-control" name="leveling.xp_cooldown_seconds" value="<%= getNested(config, 'leveling.xp_cooldown_seconds', 60) %>">
                    </div>
                </div>
            </div>
            <div class="tab-pane fade" id="nav-moderation" role="tabpanel">
                <h4 class="mb-3">Moderation & Logging</h4>
                <div class="row mb-4">
                    <div class="col-md-6 mb-3"><label class="form-label">Mod Log Channel ID</label><input type="text" class="form-control" name="moderation.mod_log_channel_id" value="<%= getNested(config, 'moderation.mod_log_channel_id', '') %>"></div>
                    <div class="col-md-6 mb-3"><label class="form-label">Server Log Channel ID</label><input type="text" class="form-control" name="logging.channel_id" value="<%= getNested(config, 'logging.channel_id', '') %>"></div>
                </div>
                <h5 class="mb-3">AutoMod</h5>
                <div class="d-flex flex-wrap gap-4">
                    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="automod.anti_link" <%= getNested(config, 'automod.anti_link') ? 'checked' : '' %>><label class="form-check-label">Anti-Link</label></div>
                    <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="automod.anti_invite" <%= getNested(config, 'automod.anti_invite') ? 'checked' : '' %>><label class="form-check-label">Anti-Invite</label></div>
                </div>
            </div>
            <div class="tab-pane fade" id="nav-starboard" role="tabpanel">
                 <h4 class="mb-3">Starboard</h4>
                 <div class="row">
                      <div class="col-md-6 mb-3"><label class="form-label">Starboard Channel ID</label><input type="text" class="form-control" name="starboard.channel_id" value="<%= getNested(config, 'starboard.channel_id', '') %>"></div>
                      <div class="col-md-6 mb-3"><label class="form-label">Required Stars ⭐</label><input type="number" class="form-control" name="starboard.star_count" value="<%= getNested(config, 'starboard.star_count', 5) %>" min="1"></div>
                 </div>
            </div>
        </div>

        <div class="text-center mt-4">
            <button type="submit" class="btn btn-primary btn-lg px-5">Save All Settings</button>
        </div>
    </form>
</div>

<%- include('../partials/footer') %>
"""

STYLE_CSS_CONTENT = r"""
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');

:root {
    --primary-color: #7289da;
    --primary-hover: #677bc4;
    --background-color: #1e1e2e;
    --surface-color: #282a36;
    --text-color: #f8f8f2;
    --muted-text-color: #a9a9b3;
    --border-color: #44475a;
    --success-color: #50fa7b;
    --danger-color: #ff5555;
    --warning-color: #f1fa8c;
    --font-family: 'Poppins', sans-serif;
    --border-radius: 8px;
    --box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
}

[data-bs-theme="dark"] {
    --bs-primary: var(--primary-color);
    --bs-primary-rgb: 114, 137, 218;
    --bs-body-bg: var(--background-color);
    --bs-body-color: var(--text-color);
    --bs-border-color: var(--border-color);
    --bs-secondary-bg: var(--surface-color);
}

body {
    font-family: var(--font-family);
    background-color: var(--background-color);
    color: var(--text-color);
    line-height: 1.6;
}

#wrapper {
    display: flex;
    min-height: 100vh;
}

#sidebar-wrapper {
    width: 260px;
    background-color: var(--surface-color);
    border-right: 1px solid var(--border-color);
    transition: margin-left 0.3s ease;
    box-shadow: var(--box-shadow);
}

.sidebar-heading {
    padding: 1.5rem;
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--primary-color);
    border-bottom: 1px solid var(--border-color);
    text-align: center;
}

.sidebar-heading .bi {
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0% { transform: scale(1); }
    50% { transform: scale(1.1); }
    100% { transform: scale(1); }
}

.list-group-item {
    background-color: transparent;
    border: none;
    color: var(--muted-text-color);
    font-weight: 500;
    padding: 1rem 1.5rem;
    margin: 0.25rem 1rem;
    border-radius: var(--border-radius);
    transition: all 0.2s ease-in-out;
}

.list-group-item:hover {
    background-color: rgba(var(--bs-primary-rgb), 0.1);
    color: var(--primary-color);
    transform: translateX(5px);
}

.list-group-item.active {
    background: linear-gradient(90deg, var(--primary-color) 0%, var(--primary-hover) 100%);
    color: white;
    box-shadow: 0 4px 8px rgba(var(--bs-primary-rgb), 0.3);
}

#page-content-wrapper {
    flex-grow: 1;
    padding: 1.5rem;
}

#page-content-wrapper-full {
    flex-grow: 1;
}

.navbar {
    background-color: var(--surface-color);
    border-radius: var(--border-radius);
    padding: 1rem 1.5rem;
    margin-bottom: 1.5rem;
    box-shadow: var(--box-shadow);
}

.card {
    background-color: var(--surface-color);
    border: 1px solid var(--border-color);
    border-radius: var(--border-radius);
    box-shadow: var(--box-shadow);
    transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}

.card:hover {
    transform: translateY(-5px);
    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
    border-color: var(--primary-color);
}

.card-header {
    background-color: #313341;
    font-weight: 600;
    padding: 1rem 1.5rem;
    border-bottom: 1px solid var(--border-color);
}

.guild-card {
    text-align: center;
    padding: 1rem;
}

.guild-card img {
    width: 100px;
    height: 100px;
    border-radius: 50%;
    border: 3px solid var(--primary-color);
    margin-bottom: 1rem;
    object-fit: cover;
}

.form-control, .form-select {
    background-color: var(--background-color);
    border: 1px solid var(--border-color);
    color: var(--text-color);
    border-radius: var(--border-radius);
    padding: 0.75rem 1rem;
}

.form-control:focus, .form-select:focus {
    background-color: var(--background-color);
    border-color: var(--primary-color);
    box-shadow: 0 0 0 0.25rem rgba(var(--bs-primary-rgb), 0.25);
    color: var(--text-color);
}

.form-check-input {
    width: 2.5em;
    height: 1.25em;
    background-color: var(--border-color);
    border-color: var(--border-color);
}
.form-check-input:checked {
    background-color: var(--success-color);
    border-color: var(--success-color);
}

.btn-primary {
    background: linear-gradient(90deg, var(--primary-color) 0%, var(--primary-hover) 100%);
    border: none;
    font-weight: 600;
    padding: 0.75rem 1.5rem;
    border-radius: var(--border-radius);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.btn-primary:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(var(--bs-primary-rgb), 0.4);
}

.nav-tabs {
    border-bottom: 1px solid var(--border-color);
}
.nav-tabs .nav-link {
    background: none;
    border: none;
    color: var(--muted-text-color);
    font-weight: 500;
    padding: 0.75rem 1.25rem;
    border-bottom: 3px solid transparent;
}
.nav-tabs .nav-link.active, .nav-tabs .nav-item.show .nav-link {
    color: var(--primary-color);
    background-color: transparent;
    border-bottom: 3px solid var(--primary-color);
}
.tab-content {
    background-color: var(--surface-color);
    padding: 2rem;
    border: 1px solid var(--border-color);
    border-top: none;
    border-radius: 0 0 var(--border-radius) var(--border-radius);
}
"""

MAIN_JS_CONTENT = r"""
document.addEventListener('DOMContentLoaded', function() {
    const ajaxForms = document.querySelectorAll('form.ajax-form');

    ajaxForms.forEach(form => {
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submitButton = form.querySelector('button[type="submit"]');
            const originalButtonText = submitButton.innerHTML;
            submitButton.disabled = true;
            submitButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Saving...`;

            const formData = new FormData(form);
            const data = {};
            for (let [key, value] of formData.entries()) {
                if (key.includes('.')) {
                    const keys = key.split('.');
                    let temp = data;
                    for (let i = 0; i < keys.length - 1; i++) {
                        if (!temp[keys[i]]) {
                            temp[keys[i]] = {};
                        }
                        temp = temp[keys[i]];
                    }
                    temp[keys[keys.length - 1]] = value;
                } else {
                    data[key] = value;
                }
            }

            form.querySelectorAll('input[type=checkbox]').forEach(checkbox => {
                const name = checkbox.name;
                if (!checkbox.checked) {
                    const keys = name.split('.');
                    let current = data;
                    for (let i = 0; i < keys.length - 1; i++) {
                        if (!current[keys[i]]) current[keys[i]] = {};
                        current = current[keys[i]];
                    }
                    if (!(keys[keys.length - 1] in current)) {
                        current[keys[keys.length - 1]] = false;
                    }
                }
            });

            try {
                const response = await fetch(form.action, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(Object.fromEntries(new FormData(form)))
                });

                const result = await response.json();
                if (response.ok && result.status === 'success') {
                    showToast(result.message, 'success');
                } else {
                    showToast(result.message || 'An unknown error occurred.', 'error');
                }
            } catch (error) {
                showToast('A network error occurred. Please try again.', 'error');
                console.error('Form submission error:', error);
            } finally {
                submitButton.disabled = false;
                submitButton.innerHTML = originalButtonText;
            }
        });
    });

    function showToast(message, type = 'success') {
        const toastEl = document.getElementById('notificationToast');
        if (!toastEl) return;

        const toastBody = toastEl.querySelector('.toast-body');
        const toastHeader = toastEl.querySelector('.toast-header');
        
        toastBody.textContent = message;
        
        toastHeader.classList.remove('bg-success', 'bg-danger', 'text-white');
        const btnClose = toastHeader.querySelector('.btn-close');
        btnClose.classList.remove('btn-close-white');

        if (type === 'success') {
            toastHeader.classList.add('bg-success', 'text-white');
            btnClose.classList.add('btn-close-white');
        } else if (type === 'error') {
            toastHeader.classList.add('bg-danger', 'text-white');
            btnClose.classList.add('btn-close-white');
        }

        const toast = new bootstrap.Toast(toastEl);
        toast.show();
    }
});
"""

ENV_SAMPLE_CONTENT = r"""
# == REQUIRED ==
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
DISCORD_CLIENT_ID=YOUR_CLIENT_ID_HERE
DISCORD_CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE

# == DASHBOARD CONFIG ==
OAUTH_REDIRECT_URI=http://localhost:3000/callback
SESSION_SECRET=GENERATE_A_RANDOM_SECRET_KEY_HERE

# == OPTIONAL & RECOMMENDED ==
# For instant slash command syncing to a test server instead of waiting ~1 hour for global sync
GUILD_ID=YOUR_TEST_SERVER_ID_FOR_INSTANT_SYNC

# == API KEYS FOR OPTIONAL FEATURES ==
GEMINI_API_KEY=
OPENWEATHER_API_KEY=
TENOR_API_KEY=
"""

REQUIREMENTS_TXT_CONTENT = r"""
discord.py==2.3.2
python-dotenv==1.0.1
Pillow==10.4.0
aiohttp==3.9.5
PyNaCl==1.5.0
yt-dlp
humanize==4.9.0
aiofiles==23.2.1
psutil==5.9.8
google-generativeai==0.5.4
matplotlib==3.8.4
aiosqlite==0.19.0
requests==2.32.3
"""

PACKAGE_JSON_CONTENT = r"""
{
  "name": "maxybot-dashboard",
  "version": "6.1.0",
  "description": "The web dashboard for Maxy Bot.",
  "main": "server.js",
  "scripts": {
    "start": "node server.js",
    "dev": "nodemon server.js"
  },
  "author": "Maxy Bot Team",
  "license": "ISC",
  "dependencies": {
    "discord-oauth2": "^2.11.0",
    "dotenv": "^16.4.5",
    "ejs": "^3.1.10",
    "express": "^4.19.2",
    "express-session": "^1.18.0"
  },
  "devDependencies": {
    "nodemon": "^3.1.0"
  }
}
"""

SHOP_ITEMS_JSON_CONTENT = r"""
{
    "profile_backgrounds": [
        {
            "id": "default_bg",
            "name": "Default Gradient",
            "price": 0,
            "path": "default.png",
            "description": "The sleek, default gradient background."
        },
        {
            "id": "galaxy_bg",
            "name": "Cosmic Galaxy",
            "price": 2500,
            "path": "galaxy.png",
            "description": "A stunning view of a distant galaxy."
        },
        {
            "id": "sunset_bg",
            "name": "Pixel Sunset",
            "price": 1500,
            "path": "sunset.png",
            "description": "A beautiful retro pixel art sunset."
        },
        {
            "id": "forest_bg",
            "name": "Enchanted Forest",
            "price": 1800,
            "path": "forest.png",
            "description": "A mystical forest with glowing mushrooms."
        }
    ]
}
"""

COG_UTILS_PY_CONTENT = r"""
from __future__ import annotations
from typing import TYPE_CHECKING
import discord
from discord import app_commands
import logging

if TYPE_CHECKING:
    from ..bot import MaxyBot

async def cog_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    bot: MaxyBot = interaction.client
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"⏳ This command is on cooldown. Please try again in {error.retry_after:.2f} seconds.", ephemeral=True)
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have the required permissions to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.BotMissingPermissions):
        await interaction.response.send_message("🚫 I don't have the necessary permissions to perform this action.", ephemeral=True)
    else:
        # Use followup if the initial response has already been sent
        send_method = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
        
        try:
            await send_method("An unexpected error occurred. The developers have been notified.", ephemeral=True)
        except discord.errors.InteractionResponded:
            # This can happen in a race condition, so we try the other method.
            if send_method == interaction.response.send_message:
                await interaction.followup.send("An unexpected error occurred. The developers have been notified.", ephemeral=True)

        command_name = interaction.command.name if interaction.command else 'unknown'
        bot.logger.error(f"Error in command '{command_name}':", exc_info=error)
"""

# Corrected COG_TEMPLATE, it now expects the full class body in `implemented_code`
COG_TEMPLATE = r"""
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

class {cog_name}(commands.Cog, name="{cog_name}"):
{implemented_code}

async def setup(bot: MaxyBot):
    bot.add_cog({cog_name}(bot))
"""

# --- UNIFIED COG DEFINITIONS ---
# All cogs are now defined here in a consistent structure to fix all previous errors.

COGS_TO_CREATE = {
    "General": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.http_session = bot.http_session

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="ping", description="Checks the bot's latency and response time.")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(title="🏓 Pong!", description=f"**API Latency:** `{latency}ms`", color=discord.Colour.green())
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
"""),
    },
    "Fun": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.http_session = bot.http_session

    async def get_tenor_gif(self, query: str) -> Optional[str]:
        api_key = os.getenv("TENOR_API_KEY")
        if not api_key:
            return None
        url = f"https://tenor.googleapis.com/v2/search?q={query}&key={api_key}&limit=20&media_filter=minimal"
        async with self.http_session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                if data['results']:
                    return random.choice(data['results'])['media_formats']['gif']['url']
        return None

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="The question you want to ask.")
    async def eight_ball(self, interaction: discord.Interaction, question: str):
        responses = [
            "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes – definitely.", "You may rely on it.",
            "As I see it, yes.", "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
            "Reply hazy, try again.", "Ask again later.", "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
            "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."
        ]
        embed = discord.Embed(title="🎱 The Magic 8-Ball Says...", color=discord.Color.blue())
        embed.add_field(name="Your Question", value=question, inline=False)
        embed.add_field(name="Answer", value=random.choice(responses), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="coinflip", description="Flips a coin.")
    async def coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Heads", "Tails"])
        embed = discord.Embed(title="🪙 Coin Flip", description=f"The coin landed on... **{result}**!", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="meme", description="Gets a random meme from Reddit.")
    async def meme(self, interaction: discord.Interaction):
        await interaction.response.defer()
        subreddits = ["memes", "dankmemes", "wholesomememes", "me_irl"]
        try:
            async with self.http_session.get(f"https://www.reddit.com/r/{random.choice(subreddits)}/random.json") as resp:
                if resp.status != 200:
                    return await interaction.followup.send("Could not fetch a meme, Reddit might be down.")
                data = await resp.json()
                post = data[0]['data']['children'][0]['data']
                embed = discord.Embed(title=post['title'], url=f"https://reddit.com{post['permalink']}", color=discord.Colour.orange())
                embed.set_image(url=post['url'])
                embed.set_footer(text=f"👍 {post['ups']} | 💬 {post['num_comments']} | r/{post['subreddit']}")
                await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {e}")

    @app_commands.command(name="gif", description="Searches for a GIF on Tenor.")
    @app_commands.describe(query="What to search for.")
    async def gif(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        gif_url = await self.get_tenor_gif(query)
        if gif_url:
            await interaction.followup.send(gif_url)
        else:
            await interaction.followup.send(f"Could not find a GIF for '{query}'. The Tenor API key might be missing or invalid.")

    @app_commands.command(name="slap", description="Slap someone with a GIF.")
    @app_commands.describe(user="The user to slap.")
    async def slap(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()
        gif_url = await self.get_tenor_gif("anime slap")
        embed = discord.Embed(description=f"{interaction.user.mention} slaps {user.mention}!", color=discord.Colour.red())
        if gif_url:
            embed.set_image(url=gif_url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="hug", description="Hug someone with a GIF.")
    @app_commands.describe(user="The user to hug.")
    async def hug(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer()
        gif_url = await self.get_tenor_gif("anime hug")
        embed = discord.Embed(description=f"{interaction.user.mention} hugs {user.mention}!", color=discord.Color.pink())
        if gif_url:
            embed.set_image(url=gif_url)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="ship", description="Calculates the love compatibility between two users.")
    @app_commands.describe(user1="The first user.", user2="The second user.")
    async def ship(self, interaction: discord.Interaction, user1: discord.Member, user2: Optional[discord.Member] = None):
        if user2 is None:
            user2 = interaction.user
        percentage = random.randint(0, 100)
        if percentage < 20: msg = "Not a great match... 💔"
        elif percentage < 50: msg = "Could be worse! 🤔"
        elif percentage < 80: msg = "There's definitely potential! 🥰"
        else: msg = "It's a match made in heaven! 💖"
        embed = discord.Embed(title="Love Calculator", description=f"**{user1.display_name}** + **{user2.display_name}** = **{percentage}%**\n\n{msg}", color=discord.Color.pink())
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="howgay", description="Calculates how gay a user is.")
    @app_commands.describe(user="The user to rate.")
    async def howgay(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        percentage = random.randint(0, 100)
        embed = discord.Embed(title="Gay-o-Meter", description=f"**{target.display_name}** is **{percentage}%** gay! 🏳️‍🌈", color=discord.Color.random())
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="rps", description="Play Rock, Paper, Scissors with the bot.")
    @app_commands.describe(choice="Your choice.")
    @app_commands.choices(choice=[
        app_commands.Choice(name="Rock", value="rock"),
        app_commands.Choice(name="Paper", value="paper"),
        app_commands.Choice(name="Scissors", value="scissors"),
    ])
    async def rps(self, interaction: discord.Interaction, choice: app_commands.Choice[str]):
        bot_choice = random.choice(["rock", "paper", "scissors"])
        user_choice = choice.value
        
        result = "It's a tie!"
        if (user_choice == "rock" and bot_choice == "scissors") or \
           (user_choice == "paper" and bot_choice == "rock") or \
           (user_choice == "scissors" and bot_choice == "paper"):
            result = "You win!"
        elif (bot_choice == "rock" and user_choice == "scissors") or \
             (bot_choice == "paper" and user_choice == "rock") or \
             (bot_choice == "scissors" and user_choice == "paper"):
            result = "You lose!"
            
        await interaction.response.send_message(f"You chose **{user_choice}**. I chose **{bot_choice}**. **{result}**")
"""),
    },
    "Utilities": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.http_session = bot.http_session
        self.check_reminders.start()
        
    def cog_unload(self):
        self.check_reminders.cancel()
        
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="poll", description="Creates a simple poll with reactions.")
    @app_commands.describe(question="The poll question.", options="Up to 10 options, separated by '|'.")
    async def poll(self, interaction: discord.Interaction, question: str, options: str):
        option_list = options.split('|')
        if len(option_list) < 2 or len(option_list) > 10:
            return await interaction.response.send_message("You must provide between 2 and 10 options, separated by '|'.", ephemeral=True)
        reactions = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        description = []
        for i, option in enumerate(option_list):
            description.append(f"{reactions[i]} {option.strip()}")
        embed = discord.Embed(title=question, description="\n".join(description), color=discord.Color.blurple())
        embed.set_footer(text=f"Poll started by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        for i in range(len(option_list)):
            await message.add_reaction(reactions[i])

    @app_commands.command(name="weather", description="Gets the current weather for a location.")
    @app_commands.describe(location="The city or zip code.")
    async def weather(self, interaction: discord.Interaction, location: str):
        api_key = os.getenv("OPENWEATHER_API_KEY")
        if not api_key:
            return await interaction.response.send_message("The `OPENWEATHER_API_KEY` is not set in the `.env` file.", ephemeral=True)
        await interaction.response.defer()
        url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={api_key}&units=metric"
        async with self.http_session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                embed = discord.Embed(title=f"Weather in {data['name']}, {data['sys']['country']}", color=0x7289DA)
                embed.add_field(name="🌡️ Temperature", value=f"{data['main']['temp']}°C", inline=True)
                embed.add_field(name="💨 Wind", value=f"{data['wind']['speed']} m/s", inline=True)
                embed.add_field(name="💧 Humidity", value=f"{data['main']['humidity']}%", inline=True)
                embed.add_field(name="☁️ Condition", value=data['weather'][0]['description'].title(), inline=True)
                embed.set_thumbnail(url=f"http://openweathermap.org/img/wn/{data['weather'][0]['icon']}@2x.png")
                await interaction.followup.send(embed=embed)
            elif resp.status == 404:
                await interaction.followup.send(f"Could not find weather for '{location}'.")
            else:
                await interaction.followup.send("Could not retrieve weather data.")

    @app_commands.command(name="afk", description="Sets your status to AFK (Away From Keyboard).")
    @app_commands.describe(reason="The reason for being AFK.")
    async def afk(self, interaction: discord.Interaction, reason: Optional[str] = "No reason provided."):
        await self.bot.db.execute("REPLACE INTO afk (guild_id, user_id, reason, timestamp) VALUES (?, ?, ?, ?)", (interaction.guild.id, interaction.user.id, reason, dt.now(UTC)))
        await interaction.response.send_message(f"You are now AFK. Reason: `{reason}`", ephemeral=True)
        try:
            current_nick = interaction.user.display_name
            if not current_nick.startswith("[AFK]"):
                    await interaction.user.edit(nick=f"[AFK] {current_nick}")
        except discord.Forbidden:
            pass

    async def handle_afk_return(self, message: discord.Message):
        afk_data = await self.bot.db.fetchone("SELECT timestamp FROM afk WHERE guild_id = ? AND user_id = ?", (message.guild.id, message.author.id))
        if afk_data:
            await self.bot.db.execute("DELETE FROM afk WHERE guild_id = ? AND user_id = ?", (message.guild.id, message.author.id))
            await message.channel.send(f"Welcome back, {message.author.mention}! I've removed your AFK status.", delete_after=10)
            try:
                if message.author.display_name.startswith("[AFK]"):
                    new_nick = message.author.display_name[6:]
                    await message.author.edit(nick=new_nick)
            except discord.Forbidden:
                pass

    async def handle_afk_mention(self, message: discord.Message):
        if not message.mentions: return
        for user in message.mentions:
            afk_data = await self.bot.db.fetchone("SELECT reason, timestamp FROM afk WHERE guild_id = ? AND user_id = ?", (message.guild.id, user.id))
            if afk_data:
                timestamp = dt.fromisoformat(afk_data['timestamp'])
                afk_time = humanize.naturaltime(dt.now(UTC) - timestamp)
                await message.channel.send(f"**{user.display_name}** is AFK: `{afk_data['reason']}` ({afk_time})")

    @app_commands.command(name="remindme", description="Sets a reminder for the future.")
    @app_commands.describe(duration="When to remind (e.g., 10m, 1h, 2d).", reminder="What to be reminded of.")
    async def remindme(self, interaction: discord.Interaction, duration: str, reminder: str):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        seconds = 0
        match = re.findall(r"(\d+)([smhdw])", duration.lower())
        if not match:
            return await interaction.response.send_message("Invalid duration format.", ephemeral=True)
        
        for value, unit in match:
            seconds += int(value) * units[unit]
        
        remind_time = dt.now(UTC) + datetime.timedelta(seconds=seconds)
        remind_timestamp = remind_time.timestamp()
        
        await self.bot.db.execute("INSERT INTO reminders (user_id, channel_id, remind_content, remind_timestamp) VALUES (?, ?, ?, ?)", (interaction.user.id, interaction.channel.id, reminder, remind_timestamp))
        await interaction.response.send_message(f"✅ Got it! I'll remind you about `{reminder}` on {discord.utils.format_dt(remind_time, 'F')}.", ephemeral=True)

    @tasks.loop(seconds=30)
    async def check_reminders(self):
        now = dt.now(UTC).timestamp()
        reminders = await self.bot.db.fetchall("SELECT * FROM reminders WHERE remind_timestamp <= ?", (now,))
        for r in reminders:
            try:
                user = await self.bot.fetch_user(r['user_id'])
                channel = await self.bot.fetch_channel(r['channel_id'])
                await channel.send(f"Hey {user.mention}, you asked me to remind you about: `{r['remind_content']}`")
            except (discord.NotFound, discord.Forbidden) as e:
                self.bot.logger.warning(f"Could not send reminder {r['reminder_id']}: {e}")
            finally:
                await self.bot.db.execute("DELETE FROM reminders WHERE reminder_id = ?", (r['reminder_id'],))
    
    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()
"""),
    },
"Tickets": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    class TicketPanelView(discord.ui.View):
        def __init__(self, bot: 'MaxyBot'):
            super().__init__(timeout=None)
            self.bot = bot

        @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket_button", emoji="🎟️")
        async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.defer(ephemeral=True)
            conf = self.bot.get_guild_config(interaction.guild.id)
            ticket_conf = conf.get("tickets", {})
            category_id = ticket_conf.get("category_id")
            support_role_id = ticket_conf.get("support_role_id")

            if not category_id or not support_role_id:
                return await interaction.followup.send("The ticket system is not fully configured. An admin needs to set the category and support role.", ephemeral=True)
            
            try:
                category = interaction.guild.get_channel(int(category_id))
                support_role = interaction.guild.get_role(int(support_role_id))
            except (ValueError, TypeError):
                return await interaction.followup.send("Ticket system configuration is invalid (Category or Role ID is incorrect).", ephemeral=True)

            if not category or not support_role:
                return await interaction.followup.send("Ticket system configuration is invalid (category or role not found).", ephemeral=True)

            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
                support_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            channel = await category.create_text_channel(name=f"ticket-{interaction.user.name}", topic=f"Ticket for {interaction.user.id}", overwrites=overwrites)
            await self.bot.db.execute("INSERT INTO tickets (channel_id, guild_id, user_id) VALUES (?, ?, ?)", (channel.id, interaction.guild.id, interaction.user.id))
            await interaction.followup.send(f"✅ Your ticket has been created: {channel.mention}", ephemeral=True)
            embed = discord.Embed(title="Support Ticket", description="Thank you for creating a ticket. A staff member will be with you shortly. To close this ticket, click the button below.", color=discord.Colour.green())
            await channel.send(f"Welcome {interaction.user.mention}! {support_role.mention}", embed=embed, view=self.bot.get_cog("Tickets").CloseTicketView(self.bot))

    class CloseTicketView(discord.ui.View):
        def __init__(self, bot: 'MaxyBot'):
            super().__init__(timeout=None)
            self.bot = bot

        @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket_button", emoji="🔒")
        async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_message("Closing this ticket in 5 seconds...", ephemeral=True)
            await asyncio.sleep(5)

            conf = self.bot.get_guild_config(interaction.guild.id)
            transcript_channel_id = conf.get("tickets", {}).get("transcript_channel_id")
            if transcript_channel_id:
                try:
                    transcript_channel = interaction.guild.get_channel(int(transcript_channel_id))
                    if transcript_channel:
                        messages = [message async for message in interaction.channel.history(limit=None, oldest_first=True)]
                        transcript = "\n".join([f"[{m.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {m.author}: {m.content}" for m in messages])
                        transcript_file = discord.File(io.StringIO(transcript), filename=f"transcript-{interaction.channel.name}.txt")
                        await transcript_channel.send(f"Transcript for ticket `{interaction.channel.name}` closed by {interaction.user.mention}.", file=transcript_file)
                except (ValueError, TypeError):
                    self.bot.logger.warning(f"Invalid transcript channel ID for guild {interaction.guild.id}")

            await self.bot.db.execute("DELETE FROM tickets WHERE channel_id = ?", (interaction.channel.id,))
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

    @app_commands.command(name="ticket-setup", description="[Admin] Sets up the ticket system message and panel.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_setup(self, interaction: discord.Interaction):
        conf = self.bot.get_guild_config(interaction.guild.id)
        ticket_conf = conf.get("tickets", {})
        if not ticket_conf.get('enabled') or not ticket_conf.get('category_id') or not ticket_conf.get('support_role_id'):
            return await interaction.response.send_message("Enable the ticket module and set a category/support role in the dashboard first!", ephemeral=True)

        embed = discord.Embed(title="Create a Support Ticket", description="Click the button below to create a new ticket and get help from our staff team.", color=discord.Color.blurple())
        view = self.TicketPanelView(self.bot)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("Ticket panel created successfully!", ephemeral=True)
"""),
    },
    "Giveaways": {
        "implemented_code": dedent(r"""
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
"""),
    },
    "Configuration": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="set-prefix", description="[Admin] Sets the legacy command prefix for the bot.")
    @app_commands.describe(prefix="The new prefix to use.")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_prefix(self, interaction: discord.Interaction, prefix: str):
        if len(prefix) > 5:
            return await interaction.response.send_message("Prefix cannot be longer than 5 characters.", ephemeral=True)
        conf = self.bot.get_guild_config(interaction.guild.id)
        conf['prefix'] = prefix
        self.bot.config_data[str(interaction.guild.id)] = conf
        await self.bot.save_config()
        self.bot.config_cache.pop(interaction.guild.id, None)
        await interaction.response.send_message(f"✅ My prefix has been updated to `{prefix}`.", ephemeral=True)

    @app_commands.command(name="setup-welcome", description="[Admin] Configures the welcome message system.")
    @app_commands.describe(channel="The channel for welcome messages.", message="The welcome message. Use {user.mention}, {user.name}, {guild.name}.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        conf = self.bot.get_guild_config(interaction.guild.id)
        conf['welcome']['channel_id'] = channel.id
        conf['welcome']['message'] = message
        conf['welcome']['enabled'] = True
        self.bot.config_data[str(interaction.guild.id)] = conf
        await self.bot.save_config()
        self.bot.config_cache.pop(interaction.guild.id, None)
        await interaction.response.send_message(f"✅ Welcome messages will now be sent to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="setup-logs", description="[Admin] Configures the server logging system.")
    @app_commands.describe(channel="The channel for logging server events.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_logs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        conf = self.bot.get_guild_config(interaction.guild.id)
        conf['logging']['channel_id'] = channel.id
        conf['logging']['enabled'] = True
        self.bot.config_data[str(interaction.guild.id)] = conf
        await self.bot.save_config()
        self.bot.config_cache.pop(interaction.guild.id, None)
        await interaction.response.send_message(f"✅ Server events will now be logged in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="autorole-human", description="[Admin] Sets a role to be automatically given to new human members.")
    @app_commands.describe(role="The role to assign.")
    @app_commands.checks.has_permissions(administrator=True)
    async def autorole_human(self, interaction: discord.Interaction, role: discord.Role):
        conf = self.bot.get_guild_config(interaction.guild.id)
        if role.is_default() or role.is_bot_managed() or role.is_premium_subscriber() or role.is_integration():
            return await interaction.response.send_message("You cannot assign this type of role.", ephemeral=True)

        conf['autorole']['human_role_id'] = role.id
        conf['autorole']['enabled'] = True
        message = f"✅ New human members will now automatically receive the {role.mention} role."
        
        self.bot.config_data[str(interaction.guild.id)] = conf
        await self.bot.save_config()
        self.bot.config_cache.pop(interaction.guild.id, None)
        await interaction.response.send_message(message, ephemeral=True)
"""),
    },
    "Admin": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        return await self.bot.is_owner(interaction.user)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("❌ This command can only be used by the bot owner.", ephemeral=True)
        else:
            await cog_command_error(interaction, error)

    @app_commands.command(name="shutdown", description="[Owner Only] Shuts down the bot.")
    async def shutdown(self, interaction: discord.Interaction):
        await interaction.response.send_message("Shutting down...", ephemeral=True)
        await self.bot.close()

    @app_commands.command(name="reload", description="[Owner Only] Reloads a cog.")
    @app_commands.describe(cog="The name of the cog to reload (e.g., 'general').")
    async def reload(self, interaction: discord.Interaction, cog: str):
        try:
            await self.bot.reload_extension(f"cogs.{cog.lower()}")
            await interaction.response.send_message(f"✅ Successfully reloaded the `{cog}` cog.", ephemeral=True)
        except commands.ExtensionNotLoaded:
            await interaction.response.send_message(f"⚠️ The cog `{cog}` is not loaded.", ephemeral=True)
        except commands.ExtensionNotFound:
            await interaction.response.send_message(f"❌ The cog `{cog}` was not found.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔥 An error occurred while reloading `{cog}`:\n```py\n{e}\n```", ephemeral=True)

    @app_commands.command(name="sync", description="[Owner Only] Syncs slash commands.")
    @app_commands.describe(guild_id="Optional guild ID to sync to. Leave blank for global.")
    async def sync(self, interaction: discord.Interaction, guild_id: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        if guild_id:
            try:
                guild = discord.Object(id=int(guild_id))
                self.bot.tree.copy_global_to(guild=guild)
                await self.bot.tree.sync(guild=guild)
                await interaction.followup.send(f"Commands synced to guild `{guild_id}`.")
            except (ValueError, TypeError):
                await interaction.followup.send("Invalid guild ID.")
        else:
            await self.bot.tree.sync()
            await interaction.followup.send("Commands synced globally.")
"""),
    },
    "AI": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-1.5-flash')
        else:
            self.model = None
            self.bot.logger.warning("GEMINI_API_KEY not found. AI cog will be disabled.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="chat", description="Chat with the Maxy AI (Gemini).")
    @app_commands.describe(prompt="What do you want to talk about?")
    async def chat(self, interaction: discord.Interaction, *, prompt: str):
        if not self.model:
            return await interaction.response.send_message(
                "The AI service is not configured. The bot owner must set `GEMINI_API_KEY` in the `.env` file.",
                ephemeral=True
            )
        await interaction.response.defer()
        try:
            response = await self.model.generate_content_async(prompt)
            embed = discord.Embed(
                title="🤖 AI Chat",
                color=discord.Color.blurple()
            )
            embed.add_field(name="You Asked", value=f"```{discord.utils.escape_markdown(prompt[:1020])}```", inline=False)

            response_text = response.text
            if len(response_text) > 1024:
                embed.add_field(name="AI's Response", value=f"{response_text[:1020]}...", inline=False)
            else:
                embed.add_field(name="AI's Response", value=response_text, inline=False)
            embed.set_footer(text=f"Powered by Google Gemini | Requested by {interaction.user.display_name}")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.bot.logger.error(f"Gemini API error: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while communicating with the AI. This could be due to an invalid API key or a service issue.",
                ephemeral=True
            )

    @app_commands.command(name="imagine", description="Generate an image from a text prompt using AI.")
    @app_commands.describe(prompt="Describe the image you want to create.")
    async def imagine(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.send_message("🚧 This command requires an image generation API and is currently under construction.", ephemeral=True)
"""),
    },
    "Leveling": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    def get_xp_for_level(self, level: int) -> int:
        if level <= 0: return 100
        return 5 * (level ** 2) + 50 * level + 100

    async def add_xp(self, guild_id: int, user_id: int, xp_to_add: int, channel: discord.TextChannel, user: discord.Member):
        current_data = await self.bot.db.fetchone("SELECT xp, level FROM leveling WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))

        if not current_data:
            await self.bot.db.execute("INSERT INTO leveling (guild_id, user_id, xp, level) VALUES (?, ?, ?, ?)", (guild_id, user_id, xp_to_add, 0))
            current_xp = xp_to_add
            current_level = 0
        else:
            current_xp = current_data['xp'] + xp_to_add
            current_level = current_data['level']
            await self.bot.db.execute("UPDATE leveling SET xp = ? WHERE guild_id = ? AND user_id = ?", (current_xp, guild_id, user_id))

        xp_needed_for_next_level = self.get_xp_for_level(current_level)

        if current_xp >= xp_needed_for_next_level:
            new_level = current_level + 1
            remaining_xp = current_xp - xp_needed_for_next_level
            await self.bot.db.execute("UPDATE leveling SET level = ?, xp = ? WHERE guild_id = ? AND user_id = ?", (new_level, remaining_xp, guild_id, user_id))

            conf = self.bot.get_guild_config(guild_id)
            levelup_msg = conf['leveling'].get('levelup_message', "🎉 Congrats {user.mention}, you reached **Level {level}**!")
            try:
                await channel.send(levelup_msg.format(user=user, level=new_level), allowed_mentions=discord.AllowedMentions(users=True))
            except discord.Forbidden:
                pass

            reward = await self.bot.db.fetchone("SELECT role_id FROM level_rewards WHERE guild_id = ? AND level = ?", (guild_id, new_level))
            if reward:
                try:
                    role = user.guild.get_role(reward['role_id'])
                    if role:
                        await user.add_roles(role, reason=f"Level {new_level} reward")
                        await channel.send(f"🌟 As a reward, you've received the **{role.name}** role!")
                except Exception as e:
                    self.bot.logger.error(f"Failed to grant level reward role in guild {guild_id}: {e}")

    @app_commands.command(name="rank", description="Check your or another user's current rank and level.")
    @app_commands.describe(user="The user whose rank to check. Defaults to you.")
    async def rank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        if target.bot:
            return await interaction.response.send_message("Bots don't have ranks!", ephemeral=True)
        await interaction.response.defer()
        data = await self.bot.db.fetchone("SELECT xp, level FROM leveling WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, target.id))

        level = data['level'] if data else 0
        xp = data['xp'] if data else 0
        xp_for_next_level = self.get_xp_for_level(level)

        leaderboard = await self.bot.db.fetchall("SELECT user_id FROM leveling WHERE guild_id = ? ORDER BY level DESC, xp DESC", (interaction.guild.id,))
        rank = 0
        for i, entry in enumerate(leaderboard):
            if entry['user_id'] == target.id:
                rank = i + 1
                break

        embed = discord.Embed(title=f"Rank for {target.display_name}", color=target.color)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Level", value=f"`{level}`", inline=True)
        embed.add_field(name="Rank", value=f"`#{rank}`" if rank > 0 else "`Unranked`", inline=True)
        embed.add_field(name="Progress", value=f"`{xp} / {xp_for_next_level} XP`", inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="leaderboard-levels", description="Shows the server's leveling leaderboard.")
    async def leaderboard_levels(self, interaction: discord.Interaction):
        await interaction.response.defer()
        query = "SELECT user_id, level, xp FROM leveling WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT 10"
        results = await self.bot.db.fetchall(query, (interaction.guild.id,))

        if not results:
            return await interaction.followup.send("There is no one on the leaderboard yet!")

        embed = discord.Embed(title=f"🏆 Level Leaderboard for {interaction.guild.name}", color=discord.Color.gold())
        description = []
        for i, row in enumerate(results):
            user = interaction.guild.get_member(row['user_id'])
            username = user.display_name if user else f"User ID: {row['user_id']}"
            description.append(f"**{i+1}.** {username} - **Level {row['level']}** ({row['xp']} XP)")
        embed.description = "\n".join(description)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="level-reward-add", description="[Admin] Set a role to be given at a certain level.")
    @app_commands.describe(level="The level to grant the role at.", role="The role to grant.")
    @app_commands.checks.has_permissions(administrator=True)
    async def add_level_reward(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 200], role: discord.Role):
        if role.is_default() or role.is_bot_managed():
            return await interaction.response.send_message("You cannot use this role as a reward.", ephemeral=True)
        await self.bot.db.execute("REPLACE INTO level_rewards (guild_id, level, role_id) VALUES (?, ?, ?)", (interaction.guild.id, level, role.id))
        await interaction.response.send_message(f"✅ Role {role.mention} will now be awarded at **Level {level}**.", ephemeral=True)

    @app_commands.command(name="level-reward-remove", description="[Admin] Remove a role reward for a specific level.")
    @app_commands.describe(level="The level of the reward to remove.")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_level_reward(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 200]):
        await self.bot.db.execute("DELETE FROM level_rewards WHERE guild_id = ? AND level = ?", (interaction.guild.id, level))
        await interaction.response.send_message(f"🗑️ Any role reward for **Level {level}** has been removed.", ephemeral=True)
"""),
    },
    "Logging": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    async def get_log_channel(self, guild_id: int) -> Optional[discord.TextChannel]:
        conf = self.bot.get_guild_config(guild_id)
        log_conf = conf.get('logging', {})
        if not log_conf.get('enabled'):
            return None

        channel_id = log_conf.get('channel_id')
        if not channel_id:
            return None
        try:
            channel = self.bot.get_channel(int(channel_id))
            if isinstance(channel, discord.TextChannel):
                return channel
        except (ValueError, TypeError):
            return None
        return None

    async def log_message_delete(self, message: discord.Message):
        if not message.guild: return
        log_channel = await self.get_log_channel(message.guild.id)
        if not log_channel: return

        embed = discord.Embed(title="Message Deleted", description=f"Message sent by {message.author.mention} in {message.channel.mention} was deleted.", color=discord.Colour.red(), timestamp=dt.now(UTC))
        content = message.content if message.content else "No message content (might be an embed or image)."
        embed.add_field(name="Content", value=f"```{content[:1020]}```", inline=False)
        embed.set_footer(text=f"Author ID: {message.author.id} | Message ID: {message.id}")
        await log_channel.send(embed=embed)

    async def log_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild: return
        log_channel = await self.get_log_channel(before.guild.id)
        if not log_channel: return

        embed = discord.Embed(title="Message Edited", description=f"Message by {before.author.mention} in {before.channel.mention} was edited. [Jump to Message]({after.jump_url})", color=discord.Colour.orange(), timestamp=dt.now(UTC))
        before_content = before.content if before.content else "N/A"
        after_content = after.content if after.content else "N/A"
        embed.add_field(name="Before", value=f"```{before_content[:1020]}```", inline=False)
        embed.add_field(name="After", value=f"```{after_content[:1020]}```", inline=False)
        embed.set_footer(text=f"Author ID: {before.author.id} | Message ID: {before.id}")
        await log_channel.send(embed=embed)

    async def log_member_update(self, before: discord.Member, after: discord.Member):
        log_channel = await self.get_log_channel(before.guild.id)
        if not log_channel: return

        if before.nick != after.nick:
            embed = discord.Embed(color=discord.Color.blue(), timestamp=dt.now(UTC))
            embed.set_author(name=f"{after} ({after.id})", icon_url=after.display_avatar.url)
            embed.title = "Nickname Changed"
            embed.add_field(name="Before", value=f"`{before.nick}`", inline=True)
            embed.add_field(name="After", value=f"`{after.nick}`", inline=True)
            await log_channel.send(embed=embed)

        if before.roles != after.roles:
            embed = discord.Embed(color=discord.Color.blue(), timestamp=dt.now(UTC))
            embed.set_author(name=f"{after} ({after.id})", icon_url=after.display_avatar.url)
            embed.title = "Roles Updated"
            added_roles = [r.mention for r in after.roles if r not in before.roles]
            removed_roles = [r.mention for r in before.roles if r not in after.roles]

            if added_roles:
                embed.add_field(name="Added Roles", value=", ".join(added_roles), inline=False)
            if removed_roles:
                embed.add_field(name="Removed Roles", value=", ".join(removed_roles), inline=False)

            if added_roles or removed_roles:
                await log_channel.send(embed=embed)

    async def log_role_create(self, role: discord.Role):
        log_channel = await self.get_log_channel(role.guild.id)
        if not log_channel: return
        embed = discord.Embed(title="Role Created", description=f"Role {role.mention} (`{role.name}`) was created.", color=discord.Colour.green(), timestamp=dt.now(UTC))
        await log_channel.send(embed=embed)

    async def log_role_delete(self, role: discord.Role):
        log_channel = await self.get_log_channel(role.guild.id)
        if not log_channel: return
        embed = discord.Embed(title="Role Deleted", description=f"Role `{role.name}` was deleted.", color=discord.Colour.red(), timestamp=dt.now(UTC))
        await log_channel.send(embed=embed)

    async def log_role_update(self, before: discord.Role, after: discord.Role):
        log_channel = await self.get_log_channel(before.guild.id)
        if not log_channel: return
        if before.name == after.name and before.color == after.color and before.permissions == after.permissions: return

        description = f"Role {after.mention} was updated."
        embed = discord.Embed(title="Role Updated", description=description, color=discord.Color.blue(), timestamp=dt.now(UTC))
        if before.name != after.name:
            embed.add_field(name="Name Change", value=f"`{before.name}` -> `{after.name}`", inline=False)
        if before.color != after.color:
            embed.add_field(name="Color Change", value=f"`{before.color}` -> `{after.color}`", inline=False)
        if before.permissions != after.permissions:
            embed.add_field(name="Permissions Changed", value="Use audit log for details.", inline=False)
        await log_channel.send(embed=embed)

    async def log_channel_create(self, channel: discord.abc.GuildChannel):
        log_channel = await self.get_log_channel(channel.guild.id)
        if not log_channel: return
        embed = discord.Embed(title="Channel Created", description=f"Channel {channel.mention} (`{channel.name}`) was created.", color=discord.Colour.green(), timestamp=dt.now(UTC))
        await log_channel.send(embed=embed)

    async def log_channel_delete(self, channel: discord.abc.GuildChannel):
        log_channel = await self.get_log_channel(channel.guild.id)
        if not log_channel: return
        embed = discord.Embed(title="Channel Deleted", description=f"Channel `{channel.name}` was deleted.", color=discord.Colour.red(), timestamp=dt.now(UTC))
        await log_channel.send(embed=embed)

    async def log_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        log_channel = await self.get_log_channel(before.guild.id)
        if not log_channel: return
        embed = discord.Embed(title="Channel Updated", description=f"Channel {after.mention} was updated.", color=discord.Color.blue(), timestamp=dt.now(UTC))
        if before.name != after.name:
            embed.add_field(name="Name Change", value=f"`{before.name}` -> `{after.name}`", inline=False)
        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel) and before.topic != after.topic:
            embed.add_field(name="Topic Change", value="Topic was updated.", inline=False)
        if len(embed.fields) > 0:
            await log_channel.send(embed=embed)

    async def log_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        log_channel = await self.get_log_channel(member.guild.id)
        if not log_channel: return
        embed = discord.Embed(timestamp=dt.now(UTC))
        embed.set_author(name=member, icon_url=member.display_avatar.url)
        if not before.channel and after.channel:
            embed.title = "Member Joined Voice"
            embed.description = f"{member.mention} joined voice channel {after.channel.mention}"
            embed.color = discord.Colour.green()
            await log_channel.send(embed=embed)
        elif before.channel and not after.channel:
            embed.title = "Member Left Voice"
            embed.description = f"{member.mention} left voice channel {before.channel.mention}"
            embed.color = discord.Colour.red()
            await log_channel.send(embed=embed)
        elif before.channel and after.channel and before.channel != after.channel:
            embed.title = "Member Moved Voice"
            embed.description = f"{member.mention} moved from {before.channel.mention} to {after.channel.mention}"
            embed.color = discord.Color.blue()
            await log_channel.send(embed=embed)
"""),
    },
    "AutoResponder": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_messages

    async def handle_responses(self, message: discord.Message) -> bool:
        conf = self.bot.get_guild_config(message.guild.id)
        if not conf.get('autoresponder', {}).get('enabled', False):
            return False
        
        trigger = message.content.lower()
        response_data = await self.bot.db.fetchone(
            "SELECT response FROM auto_responses WHERE guild_id = ? AND lower(trigger) = ?",
            (message.guild.id, trigger)
        )
        if response_data:
            response_text = response_data['response']
            response_text = response_text.replace('{user.mention}', message.author.mention)
            response_text = response_text.replace('{user.name}', message.author.name)
            response_text = response_text.replace('{guild.name}', message.guild.name)
            await message.channel.send(response_text)
            return True
        return False

    @app_commands.command(name="response-add", description="Adds an auto-response.")
    @app_commands.describe(trigger="The word or phrase that will trigger the response.", response="The message the bot should send.")
    async def add_response(self, interaction: discord.Interaction, trigger: str, response: str):
        trigger = trigger.lower()
        exists = await self.bot.db.fetchone("SELECT 1 FROM auto_responses WHERE guild_id = ? AND trigger = ?", (interaction.guild.id, trigger))
        if exists:
            return await interaction.response.send_message(f"An auto-response for the trigger `{trigger}` already exists.", ephemeral=True)
        
        await self.bot.db.execute(
            "INSERT INTO auto_responses (guild_id, trigger, response, creator_id) VALUES (?, ?, ?, ?)",
            (interaction.guild.id, trigger, response, interaction.user.id)
        )
        await interaction.response.send_message(f"✅ Auto-response for `{trigger}` has been added.", ephemeral=True)

    @app_commands.command(name="response-remove", description="Removes an auto-response.")
    @app_commands.describe(trigger="The trigger of the response to remove.")
    async def remove_response(self, interaction: discord.Interaction, trigger: str):
        trigger = trigger.lower()
        row = await self.bot.db.fetchone("SELECT response_id FROM auto_responses WHERE guild_id = ? AND trigger = ?", (interaction.guild.id, trigger))
        if not row:
            return await interaction.response.send_message(f"No auto-response found for the trigger `{trigger}`.", ephemeral=True)
        
        await self.bot.db.execute("DELETE FROM auto_responses WHERE response_id = ?", (row['response_id'],))
        await interaction.response.send_message(f"🗑️ Auto-response for `{trigger}` has been removed.", ephemeral=True)

    @app_commands.command(name="response-list", description="Lists all auto-responses for this server.")
    async def list_responses(self, interaction: discord.Interaction):
        responses = await self.bot.db.fetchall("SELECT trigger FROM auto_responses WHERE guild_id = ?", (interaction.guild.id,))
        if not responses:
            return await interaction.response.send_message("This server has no auto-responses configured.", ephemeral=True)
            
        description = ", ".join([f"`{r['trigger']}`" for r in responses])
        embed = discord.Embed(title=f"Auto-Responses for {interaction.guild.name}", description=description, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)
"""),
    },
    "Economy": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.http_session = bot.http_session
        self.shop_items = {}
        self.font_path = str(self.bot.root_path / "assets" / "fonts" / "font.ttf")
        self.bg_path = self.bot.root_path / "assets" / "images" / "profile_backgrounds"
        try:
            with open(self.bot.root_path / "assets/json/shop_items.json", 'r', encoding='utf-8') as f:
                self.shop_items = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.bot.logger.error("Could not load shop_items.json. Shop functionality will be limited.")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    async def get_balance(self, guild_id: int, user_id: int) -> dict:
        data = await self.bot.db.fetchone("SELECT wallet, bank FROM economy WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        if not data:
            conf = self.bot.get_guild_config(guild_id)
            start_balance = conf['economy'].get('start_balance', 100)
            await self.bot.db.execute("INSERT INTO economy (guild_id, user_id, wallet) VALUES (?, ?, ?)", (guild_id, user_id, start_balance))
            return {'wallet': start_balance, 'bank': 0}
        return {'wallet': data['wallet'], 'bank': data['bank']}

    async def update_balance(self, guild_id: int, user_id: int, wallet_change: int = 0, bank_change: int = 0):
        await self.bot.db.execute(f"INSERT OR IGNORE INTO economy (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id))
        await self.bot.db.execute(f"UPDATE economy SET wallet = wallet + ?, bank = bank + ? WHERE guild_id = ? AND user_id = ?", (wallet_change, bank_change, guild_id, user_id))

    def generate_profile_image(self, user_data: dict):
        username = user_data['username']
        level = user_data['level']
        rank = user_data['rank']
        wallet = user_data['wallet']
        xp = user_data['xp']
        xp_needed = user_data['xp_needed']
        currency_name = user_data['currency_name']
        bg_file = user_data['bg_file']

        bg_image = Image.open(self.bg_path / bg_file).convert("RGBA")
        avatar_image = Image.open(io.BytesIO(user_data['avatar_bytes'])).convert("RGBA").resize((184, 184))

        mask = Image.new('L', avatar_image.size, 0)
        draw_mask = ImageDraw.Draw(mask)
        draw_mask.ellipse((0, 0) + avatar_image.size, fill=255)

        img = Image.new("RGBA", (934, 282), (0,0,0,0))
        img.paste(bg_image, (0,0))
        img.paste(avatar_image, (63, 50), mask)

        draw = ImageDraw.Draw(img)
        font_big = ImageFont.truetype(self.font_path, 48)
        font_medium = ImageFont.truetype(self.font_path, 35)
        font_small = ImageFont.truetype(self.font_path, 25)

        draw.text((280, 55), username, (255, 255, 255), font=font_big, stroke_width=1, stroke_fill=(0,0,0))
        draw.text((285, 130), f"Level: {level}", (255, 255, 255), font=font_medium, stroke_width=1, stroke_fill=(0,0,0))
        draw.text((490, 130), f"Rank: #{rank}", (255, 255, 255), font=font_medium, stroke_width=1, stroke_fill=(0,0,0))
        draw.text((285, 180), f"{wallet:,} {currency_name}", (255, 255, 255), font=font_medium, stroke_width=1, stroke_fill=(0,0,0))

        xp_percent = (xp / xp_needed) if xp_needed > 0 else 0
        xp_bar_width = int(590 * xp_percent)
        draw.rectangle((290, 230, 880, 245), fill=(70, 70, 70))
        if xp_bar_width > 0:
            draw.rectangle((290, 230, 290 + xp_bar_width, 245), fill=(59, 171, 255))

        xp_text = f"{xp:,} / {xp_needed:,} XP"
        text_bbox = draw.textbbox((0, 0), xp_text, font=font_small)
        text_width = text_bbox[2] - text_bbox[0]
        draw.text((880 - text_width, 195), xp_text, (255, 255, 255), font=font_small, stroke_width=1, stroke_fill=(0,0,0))

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    @app_commands.command(name="profile", description="Displays your server profile.")
    @app_commands.describe(user="The user to view the profile of.")
    async def profile(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        if target.bot:
            return await interaction.response.send_message("Bots don't have profiles!", ephemeral=True)
        await interaction.response.defer()

        leveling_cog = self.bot.get_cog("Leveling")
        conf = self.bot.get_guild_config(interaction.guild.id)

        level_data = await self.bot.db.fetchone("SELECT level, xp FROM leveling WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, target.id))
        eco_data = await self.get_balance(interaction.guild.id, target.id)
        leaderboard = await self.bot.db.fetchall("SELECT user_id FROM leveling WHERE guild_id = ? ORDER BY level DESC, xp DESC", (interaction.guild.id,))

        rank = "N/A"
        for i, entry in enumerate(leaderboard):
            if entry['user_id'] == target.id:
                rank = i + 1
                break

        level = level_data['level'] if level_data else 0
        xp = level_data['xp'] if level_data else 0
        xp_needed = leveling_cog.get_xp_for_level(level) if leveling_cog else 100
        
        active_bg_data = await self.bot.db.fetchone("SELECT item_id FROM user_inventory WHERE guild_id = ? AND user_id = ? AND item_type = 'profile_background' AND is_active = 1", (interaction.guild.id, target.id))
        bg_id = active_bg_data['item_id'] if active_bg_data else 'default_bg'
        bg_item = next((item for item in self.shop_items.get("profile_backgrounds", []) if item['id'] == bg_id), None)
        bg_file = bg_item['path'] if bg_item else 'default.png'
        
        avatar_bytes = await target.display_avatar.with_format("png").read()

        user_data = {
            "username": target.display_name, "level": level, "rank": rank, "xp": xp, "xp_needed": xp_needed,
            "wallet": eco_data['wallet'], "currency_name": conf['economy']['currency_name'],
            "bg_file": bg_file, "avatar_bytes": avatar_bytes
        }

        loop = asyncio.get_event_loop()
        final_buffer = await loop.run_in_executor(None, self.generate_profile_image, user_data)
        
        file = discord.File(fp=final_buffer, filename=f"profile_{target.id}.png")
        await interaction.followup.send(file=file)

    @app_commands.command(name="balance", description="Check your or another user's balance.")
    @app_commands.describe(user="The user to check the balance of.")
    async def balance(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        if target.bot:
            return await interaction.response.send_message("Bots don't have money!", ephemeral=True)

        conf = self.bot.get_guild_config(interaction.guild.id)
        currency_symbol = conf['economy']['currency_symbol']
        bal = await self.get_balance(interaction.guild.id, target.id)

        embed = discord.Embed(title=f"{target.display_name}'s Balance", color=target.color)
        embed.add_field(name="Wallet", value=f"{currency_symbol} {bal['wallet']:,}", inline=True)
        embed.add_field(name="Bank", value=f"{currency_symbol} {bal['bank']:,}", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="Claim your daily coins.")
    @app_commands.checks.cooldown(1, 86400, key=lambda i: (i.guild_id, i.user.id))
    async def daily(self, interaction: discord.Interaction):
        amount = random.randint(200, 500)
        await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=amount)
        conf = self.bot.get_guild_config(interaction.guild.id)
        currency_symbol = conf['economy']['currency_symbol']
        await interaction.response.send_message(f"🎉 You claimed your daily bonus of **{currency_symbol} {amount}**!")

    @app_commands.command(name="work", description="Work to earn some coins.")
    @app_commands.checks.cooldown(1, 3600, key=lambda i: (i.guild_id, i.user.id))
    async def work(self, interaction: discord.Interaction):
        amount = random.randint(50, 250)
        await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=amount)
        conf = self.bot.get_guild_config(interaction.guild.id)
        currency_symbol = conf['economy']['currency_symbol']
        await interaction.response.send_message(f"💼 You worked hard and earned **{currency_symbol} {amount}**!")

    @app_commands.command(name="deposit", description="Deposit coins into your bank account.")
    @app_commands.describe(amount="The amount to deposit or 'all'.")
    async def deposit(self, interaction: discord.Interaction, amount: str):
        bal = await self.get_balance(interaction.guild.id, interaction.user.id)
        wallet_bal = bal['wallet']
        if amount.lower() == 'all':
            dep_amount = wallet_bal
        else:
            try:
                dep_amount = int(amount)
            except ValueError:
                return await interaction.response.send_message("Please enter a valid number or 'all'.", ephemeral=True)
        
        if dep_amount <= 0:
            return await interaction.response.send_message("You must deposit a positive amount.", ephemeral=True)
        if dep_amount > wallet_bal:
            return await interaction.response.send_message("You don't have that much money in your wallet.", ephemeral=True)
            
        await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=-dep_amount, bank_change=dep_amount)
        conf = self.bot.get_guild_config(interaction.guild.id)
        currency_symbol = conf['economy']['currency_symbol']
        await interaction.response.send_message(f"🏦 You have deposited **{currency_symbol} {dep_amount:,}** into your bank.")

    @app_commands.command(name="withdraw", description="Withdraw coins from your bank account.")
    @app_commands.describe(amount="The amount to withdraw or 'all'.")
    async def withdraw(self, interaction: discord.Interaction, amount: str):
        bal = await self.get_balance(interaction.guild.id, interaction.user.id)
        bank_bal = bal['bank']
        if amount.lower() == 'all':
            wit_amount = bank_bal
        else:
            try:
                wit_amount = int(amount)
            except ValueError:
                return await interaction.response.send_message("Please enter a valid number or 'all'.", ephemeral=True)

        if wit_amount <= 0:
            return await interaction.response.send_message("You must withdraw a positive amount.", ephemeral=True)
        if wit_amount > bank_bal:
            return await interaction.response.send_message("You don't have that much money in your bank.", ephemeral=True)
            
        await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=wit_amount, bank_change=-wit_amount)
        conf = self.bot.get_guild_config(interaction.guild.id)
        currency_symbol = conf['economy']['currency_symbol']
        await interaction.response.send_message(f"💸 You have withdrawn **{currency_symbol} {wit_amount:,}** from your bank.")

    @app_commands.command(name="leaderboard-eco", description="Shows the server's economy leaderboard.")
    async def leaderboard_eco(self, interaction: discord.Interaction):
        await interaction.response.defer()
        query = "SELECT user_id, wallet, bank FROM economy WHERE guild_id = ? ORDER BY (wallet + bank) DESC LIMIT 10"
        results = await self.bot.db.fetchall(query, (interaction.guild.id,))
        if not results:
            return await interaction.followup.send("There is no one on the leaderboard yet!")

        conf = self.bot.get_guild_config(interaction.guild.id)
        currency_symbol = conf['economy']['currency_symbol']
        embed = discord.Embed(title=f"🏆 Economy Leaderboard for {interaction.guild.name}", color=discord.Color.gold())
        description = []
        for i, row in enumerate(results):
            user = interaction.guild.get_member(row['user_id'])
            username = user.display_name if user else f"User ID: {row['user_id']}"
            total = row['wallet'] + row['bank']
            description.append(f"**{i+1}.** {username} - **{currency_symbol} {total:,}**")
        embed.description = "\n".join(description)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="shop", description="View items available for purchase.")
    async def shop(self, interaction: discord.Interaction):
        if not self.shop_items.get("profile_backgrounds"):
            return await interaction.response.send_message("The shop is currently empty.", ephemeral=True)
            
        embed = discord.Embed(title="🖼️ Profile Background Shop", color=discord.Color.blue())
        description = "Use `/buy <item_id>` to purchase an item.\n\n"
        for item in self.shop_items["profile_backgrounds"]:
            description += f"**{item['name']}** - `{item['id']}`\n"
            description += f"Price: **{item['price']:,}** {self.bot.get_guild_config(interaction.guild.id)['economy']['currency_symbol']}\n"
            description += f"*{item['description']}*\n\n"
        embed.description = description
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Buy an item from the shop.")
    @app_commands.describe(item_id="The ID of the item to buy.")
    async def buy(self, interaction: discord.Interaction, item_id: str):
        item_id = item_id.lower()
        item = next((i for i in self.shop_items.get("profile_backgrounds", []) if i['id'] == item_id), None)
        if not item:
            return await interaction.response.send_message("That item does not exist in the shop.", ephemeral=True)

        bal = await self.get_balance(interaction.guild.id, interaction.user.id)
        if bal['wallet'] < item['price']:
            return await interaction.response.send_message("You don't have enough money in your wallet to buy this.", ephemeral=True)

        owned = await self.bot.db.fetchone("SELECT 1 FROM user_inventory WHERE user_id = ? AND guild_id = ? AND item_id = ?", (interaction.user.id, interaction.guild.id, item_id))
        if owned:
            return await interaction.response.send_message("You already own this item!", ephemeral=True)
        
        await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=-item['price'])
        await self.bot.db.execute("INSERT INTO user_inventory (user_id, guild_id, item_id, item_type, quantity) VALUES (?, ?, ?, ?, ?)", (interaction.user.id, interaction.guild.id, item_id, 'profile_background', 1))
        await interaction.response.send_message(f"🛍️ You have successfully purchased the **{item['name']}** background!")

    @app_commands.command(name="inventory", description="View your purchased items.")
    async def inventory(self, interaction: discord.Interaction):
        items = await self.bot.db.fetchall("SELECT item_id, is_active FROM user_inventory WHERE user_id = ? AND guild_id = ? AND item_type = 'profile_background'", (interaction.user.id, interaction.guild.id))
        if not items:
            return await interaction.response.send_message("Your inventory is empty. Visit the `/shop` to buy something!", ephemeral=True)
            
        embed = discord.Embed(title=f"{interaction.user.display_name}'s Backgrounds", color=interaction.user.color)
        description = "Use `/set-background <item_id>` to change your active background.\n\n"
        item_dict = {i['item_id']: i['is_active'] for i in items}

        for shop_item in self.shop_items.get("profile_backgrounds", []):
            if shop_item['id'] in item_dict:
                active_str = " - **(Active)**" if item_dict[shop_item['id']] else ""
                description += f"• **{shop_item['name']}** (`{shop_item['id']}`){active_str}\n"

        embed.description = description
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="set-background", description="Set your active profile background.")
    @app_commands.describe(item_id="The ID of the background from your inventory.")
    async def set_background(self, interaction: discord.Interaction, item_id: str):
        item_id = item_id.lower()
        owned = await self.bot.db.fetchone("SELECT 1 FROM user_inventory WHERE user_id = ? AND guild_id = ? AND item_id = ?", (interaction.user.id, interaction.guild.id, item_id))
        if not owned:
            return await interaction.response.send_message("You do not own this background. Check your `/inventory`.", ephemeral=True)

        await self.bot.db.execute("UPDATE user_inventory SET is_active = 0 WHERE user_id = ? AND guild_id = ? AND item_type = 'profile_background'", (interaction.user.id, interaction.guild.id))
        await self.bot.db.execute("UPDATE user_inventory SET is_active = 1 WHERE user_id = ? AND guild_id = ? AND item_id = ?", (interaction.user.id, interaction.guild.id, item_id))

        item = next((i for i in self.shop_items.get("profile_backgrounds", []) if i['id'] == item_id), None)
        item_name = item['name'] if item else item_id
        await interaction.response.send_message(f"🖼️ Your profile background has been set to **{item_name}**!")
        
    @app_commands.command(name="slots", description="Play the slot machine.")
    @app_commands.checks.cooldown(1, 5, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(bet="The amount you want to bet.")
    async def slots(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1, 10000]):
        bal = await self.get_balance(interaction.guild.id, interaction.user.id)
        if bet > bal['wallet']:
            return await interaction.response.send_message("You don't have enough money in your wallet to place that bet.", ephemeral=True)

        await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=-bet)
        
        emojis = ["🍒", "🍇", "🍊", "🍋", "🔔", "💎", "🍀"]
        reels = [random.choice(emojis) for _ in range(3)]
        
        result_str = " | ".join(reels)
        
        winnings = 0
        if reels[0] == reels[1] == reels[2]:
            winnings = bet * 10
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            winnings = bet * 3

        embed = discord.Embed(title="🎰 Slot Machine 🎰", color=discord.Color.gold())
        embed.add_field(name="Result", value=f"**[ {result_str} ]**", inline=False)

        if winnings > 0:
            await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=winnings)
            embed.description = f"🎉 Congratulations! You won **{winnings:,}** coins!"
            embed.color = discord.Colour.green()
        else:
            embed.description = "Better luck next time!"
            embed.color = discord.Colour.red()

        new_bal = await self.get_balance(interaction.guild.id, interaction.user.id)
        embed.set_footer(text=f"Your new balance: {new_bal['wallet']:,}")
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="rob", description="Attempt to rob another user.")
    @app_commands.checks.cooldown(1, 3600, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(user="The user you want to rob.")
    async def rob(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.user.id:
            return await interaction.response.send_message("You can't rob yourself!", ephemeral=True)
            
        robber_bal = await self.get_balance(interaction.guild.id, interaction.user.id)
        if robber_bal['wallet'] < 250:
            return await interaction.response.send_message("You need at least 250 coins in your wallet to attempt a robbery.", ephemeral=True)

        target_bal = await self.get_balance(interaction.guild.id, user.id)
        if target_bal['wallet'] < 100:
            return await interaction.response.send_message(f"{user.display_name} is too poor to rob.", ephemeral=True)
            
        if random.randint(1, 3) == 1:
            amount_stolen = random.randint(1, int(target_bal['wallet'] * 0.5))
            await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=amount_stolen)
            await self.update_balance(interaction.guild.id, user.id, wallet_change=-amount_stolen)
            await interaction.response.send_message(f"💰 Success! You robbed **{amount_stolen:,}** coins from {user.mention}!")
        else:
            fine = random.randint(50, 250)
            await self.update_balance(interaction.guild.id, interaction.user.id, wallet_change=-fine)
            await interaction.response.send_message(f"👮‍♂️ You were caught! You paid a fine of **{fine:,}** coins.")
"""),
    },
    "Music": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
        self.ytdl = yt_dlp.YoutubeDL({
            'format': 'bestaudio/best', 'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
            'restrictfilenames': True, 'noplaylist': True, 'nocheckcertificate': True,
            'ignoreerrors': False, 'logtostderr': False, 'quiet': True, 'no_warnings': True,
            'default_search': 'auto', 'source_address': '0.0.0.0'
        })
        self.queues = {}
        self.current_song = {}
        self.loop_states = {}

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    def play_next(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        loop_state = self.loop_states.get(guild_id, 'none')
        
        if loop_state == 'song' and self.current_song.get(guild_id):
            song_to_play = self.current_song[guild_id]
        else:
            if guild_id not in self.queues or not self.queues[guild_id]:
                self.current_song.pop(guild_id, None)
                asyncio.run_coroutine_threadsafe(interaction.channel.send("Queue finished!"), self.bot.loop)
                return
            
            if loop_state == 'queue' and self.current_song.get(guild_id):
                self.queues[guild_id].append(self.current_song[guild_id])
                
            song_to_play = self.queues[guild_id].pop(0)

        self.current_song[guild_id] = song_to_play
        vc = interaction.guild.voice_client
        if vc:
            player = discord.FFmpegPCMAudio(song_to_play['url'], **self.FFMPEG_OPTIONS)
            vc.play(player, after=lambda e: self.play_next(interaction))

    @app_commands.command(name="play", description="Plays a song from YouTube or adds it to the queue.")
    @app_commands.describe(query="The song name or YouTube URL.")
    async def play(self, interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("You need to be in a voice channel to play music.", ephemeral=True)

        await interaction.response.defer()
        
        vc: discord.VoiceClient = interaction.guild.voice_client
        if not vc:
            vc = await interaction.user.voice.channel.connect()

        try:
            loop = self.bot.loop or asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: self.ytdl.extract_info(query, download=False))
            song = data['entries'][0] if 'entries' in data else data
            
            if interaction.guild.id not in self.queues:
                self.queues[interaction.guild.id] = []
            
            self.queues[interaction.guild.id].append(song)

            if not vc.is_playing():
                embed = discord.Embed(title="🎵 Now Playing", description=f"[{song['title']}]({song['webpage_url']})", color=discord.Colour.green())
                embed.set_thumbnail(url=song.get('thumbnail'))
                await interaction.followup.send(embed=embed)
                self.play_next(interaction)
            else:
                embed = discord.Embed(title="✅ Added to Queue", description=f"[{song['title']}]({song['webpage_url']})", color=discord.Color.blue())
                embed.set_thumbnail(url=song.get('thumbnail'))
                await interaction.followup.send(embed=embed)
        except Exception as e:
            self.bot.logger.error(f"Music play error: {e}", exc_info=True)
            await interaction.followup.send(f"An error occurred while trying to play the song. It might be age-restricted or private.")

    @app_commands.command(name="stop", description="Stops the music and disconnects the bot.")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        guild_id = interaction.guild.id
        if vc and vc.is_connected():
            if guild_id in self.queues: self.queues[guild_id].clear()
            self.current_song.pop(guild_id, None)
            self.loop_states.pop(guild_id, None)
            await vc.disconnect()
            await interaction.response.send_message("⏹️ Music stopped and disconnected.", ephemeral=True)
        else:
            await interaction.response.send_message("I'm not connected to a voice channel.", ephemeral=True)
            
    @app_commands.command(name="skip", description="Skips the current song.")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped the current song.", ephemeral=True)
        else:
            await interaction.response.send_message("I'm not playing anything right now.", ephemeral=True)
            
    @app_commands.command(name="pause", description="Pauses the current song.")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Paused the music.", ephemeral=True)
        else:
            await interaction.response.send_message("I'm not playing anything to pause.", ephemeral=True)
            
    @app_commands.command(name="resume", description="Resumes the paused song.")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Resumed the music.", ephemeral=True)
        else:
            await interaction.response.send_message("There's nothing to resume.", ephemeral=True)

    @app_commands.command(name="queue", description="Shows the current song queue.")
    async def queue(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if not guild_id in self.queues or not self.queues[guild_id]:
            return await interaction.response.send_message("The queue is empty.", ephemeral=True)
            
        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.blue())
        queue_list = ""
        for i, song in enumerate(self.queues[guild_id][:10]):
            queue_list += f"`{i+1}.` {song['title']}\n"
        embed.description = queue_list
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="nowplaying", description="Shows the currently playing song.")
    async def nowplaying(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        song = self.current_song.get(guild_id)
        if not song or not interaction.guild.voice_client.is_playing():
            return await interaction.response.send_message("Nothing is playing right now.", ephemeral=True)
        
        embed = discord.Embed(title="🎵 Now Playing", description=f"[{song['title']}]({song['webpage_url']})", color=discord.Colour.green())
        embed.set_thumbnail(url=song.get('thumbnail'))
        await interaction.response.send_message(embed=embed)
        
    @app_commands.command(name="shuffle", description="Shuffles the queue.")
    async def shuffle(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if guild_id in self.queues and len(self.queues[guild_id]) > 1:
            random.shuffle(self.queues[guild_id])
            await interaction.response.send_message("🔀 Queue has been shuffled.", ephemeral=True)
        else:
            await interaction.response.send_message("Not enough songs in the queue to shuffle.", ephemeral=True)
            
    @app_commands.command(name="loop", description="Sets the loop mode.")
    @app_commands.describe(mode="The loop mode: none, song, or queue.")
    @app_commands.choices(mode=[
        app_commands.Choice(name="None", value="none"),
        app_commands.Choice(name="Current Song", value="song"),
        app_commands.Choice(name="Entire Queue", value="queue")
    ])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        self.loop_states[interaction.guild.id] = mode.value
        await interaction.response.send_message(f"🔄 Loop mode set to **{mode.name}**.", ephemeral=True)
"""),
    },
    "Moderation": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    @app_commands.command(name="purge", description="Deletes a specified number of messages.")
    @app_commands.describe(amount="The number of messages to delete (1-100).", user="Optional: The user whose messages to delete.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100], user: Optional[discord.Member] = None):
        await interaction.response.defer(ephemeral=True)
        check = (lambda m: m.author == user) if user else None
        deleted = await interaction.channel.purge(limit=amount, check=check)
        user_str = f" from {user.mention}" if user else ""
        await interaction.followup.send(f"✅ Successfully deleted {len(deleted)} messages{user_str}.", ephemeral=True)

    @app_commands.command(name="kick", description="Kicks a member from the server.")
    @app_commands.describe(member="The member to kick.", reason="The reason for the kick.")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided."):
        if member.id == interaction.user.id:
            return await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
        if member.top_role >= interaction.user.top_role and interaction.guild.owner.id != interaction.user.id:
            return await interaction.response.send_message("You cannot kick a member with a higher or equal role.", ephemeral=True)
        
        try:
            await member.kick(reason=f"{reason} (Kicked by {interaction.user})")
            await interaction.response.send_message(f"👢 Kicked {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to kick this member.", ephemeral=True)

    @app_commands.command(name="ban", description="Bans a member from the server.")
    @app_commands.describe(member="The member to ban.", reason="The reason for the ban.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided."):
        if member.id == interaction.user.id:
            return await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
        if member.top_role >= interaction.user.top_role and interaction.guild.owner.id != interaction.user.id:
            return await interaction.response.send_message("You cannot ban a member with a higher or equal role.", ephemeral=True)

        try:
            await member.ban(reason=f"{reason} (Banned by {interaction.user})")
            await interaction.response.send_message(f"🔨 Banned {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to ban this member.", ephemeral=True)

    @app_commands.command(name="timeout", description="Mutes a member for a specified duration (e.g., 10m, 1h, 2d).")
    @app_commands.describe(member="The member to mute.", duration="Duration (e.g. 5m, 1h, 2d, 1w). Max 28 days.", reason="The reason for the mute.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: Optional[str] = "No reason provided."):
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        seconds = 0
        match = re.findall(r"(\d+)([smhdw])", duration.lower())
        if not match:
            return await interaction.response.send_message("Invalid duration format. Use `10s`, `5m`, `2h`, `3d`, `1w`.", ephemeral=True)
        
        for value, unit in match:
            seconds += int(value) * units[unit]
        
        if seconds <= 0 or seconds > 2419200:
            return await interaction.response.send_message("Invalid duration. Duration must be between 1 second and 28 days.", ephemeral=True)
            
        delta = datetime.timedelta(seconds=seconds)
        try:
            await member.timeout(delta, reason=f"{reason} (Timed out by {interaction.user})")
            await interaction.response.send_message(f"🤫 Timed out {member.mention} for {humanize.naturaldelta(delta)}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to timeout this member.", ephemeral=True)
            
    @app_commands.command(name="untimeout", description="Removes a timeout from a member.")
    @app_commands.describe(member="The member to untimeout.", reason="The reason for removing the timeout.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason provided."):
        try:
            await member.timeout(None, reason=f"{reason} (Timeout removed by {interaction.user})")
            await interaction.response.send_message(f"😊 Removed timeout for {member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to manage this member's timeout.", ephemeral=True)

    @app_commands.command(name="unban", description="Unbans a user from the server.")
    @app_commands.describe(user_id="The ID of the user to unban.", reason="The reason for the unban.")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: Optional[str] = "No reason provided."):
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=f"{reason} (Unbanned by {interaction.user})")
            await interaction.response.send_message(f"✅ Unbanned {user.mention}.", ephemeral=True)
        except (ValueError, TypeError):
            await interaction.response.send_message("Invalid User ID provided.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("This user is not banned.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to unban users.", ephemeral=True)

    @app_commands.command(name="warn", description="Warns a member and records it.")
    @app_commands.describe(member="The member to warn.", reason="The reason for the warning.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        await self.bot.db.execute("INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)", (interaction.guild.id, member.id, interaction.user.id, reason))
        try:
            await member.send(f"You have been warned in **{interaction.guild.name}** for: `{reason}`")
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"⚠️ Warned {member.mention}. Reason: {reason}", ephemeral=True)

    @app_commands.command(name="warnings", description="Checks the warnings for a member.")
    @app_commands.describe(member="The member whose warnings to check.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        results = await self.bot.db.fetchall("SELECT moderator_id, reason, timestamp, warn_id FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC", (interaction.guild.id, member.id))
        if not results:
            return await interaction.response.send_message(f"{member.display_name} has no warnings.", ephemeral=True)

        embed = discord.Embed(title=f"Warnings for {member.display_name}", color=discord.Colour.orange())
        for row in results[:10]:
            moderator = interaction.guild.get_member(row['moderator_id'])
            mod_name = moderator.mention if moderator else f"ID: {row['moderator_id']}"
            timestamp_dt = dt.fromisoformat(row['timestamp'])
            embed.add_field(name=f"Warn ID: {row['warn_id']} | {discord.utils.format_dt(timestamp_dt, style='R')}", value=f"**Reason:** {row['reason']}\n**Moderator:** {mod_name}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="rmwarn", description="Removes a specific warning from a user.")
    @app_commands.describe(warn_id="The ID of the warning to remove.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def rmwarn(self, interaction: discord.Interaction, warn_id: int):
        warning = await self.bot.db.fetchone("SELECT * FROM warnings WHERE warn_id = ? AND guild_id = ?", (warn_id, interaction.guild.id))
        if not warning:
            return await interaction.response.send_message("No warning found with that ID in this server.", ephemeral=True)

        await self.bot.db.execute("DELETE FROM warnings WHERE warn_id = ?", (warn_id,))
        await interaction.response.send_message(f"✅ Removed warning ID `{warn_id}`.", ephemeral=True)

    @app_commands.command(name="lock", description="Locks the current channel.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        channel = interaction.channel
        await channel.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.response.send_message(f"🔒 Channel {channel.mention} has been locked.")

    @app_commands.command(name="unlock", description="Unlocks the current channel.")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        channel = interaction.channel
        await channel.set_permissions(interaction.guild.default_role, send_messages=None)
        await interaction.response.send_message(f"🔓 Channel {channel.mention} has been unlocked.")
        
    @app_commands.command(name="slowmode", description="Sets the slowmode for the current channel.")
    @app_commands.describe(seconds="The slowmode delay in seconds (0 to disable).")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await interaction.response.send_message("Slowmode has been disabled for this channel.")
        else:
            await interaction.response.send_message(f"Slowmode has been set to {seconds} seconds.")
"""),
    },
    "Images": {
        "implemented_code": dedent(r"""
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self.font_path = str(self.bot.root_path / "assets" / "fonts" / "font.ttf")
        self.template_path = self.bot.root_path / "assets" / "images" / "templates"
        
    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await cog_command_error(interaction, error)

    async def get_avatar_bytes(self, user: discord.User) -> bytes:
        return await user.display_avatar.with_format("png").read()
        
    def generate_wanted_image(self, avatar_bytes: bytes) -> io.BytesIO:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((440, 440))
        template = Image.open(self.template_path / "wanted.png")
        
        template.paste(avatar, (145, 298), avatar)
        
        buffer = io.BytesIO()
        template.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def process_image_effect(self, avatar_bytes: bytes, effect: str) -> io.BytesIO:
        image = Image.open(io.BytesIO(avatar_bytes))
        
        if effect == 'grayscale':
            image = image.convert("L").convert("RGB")
        elif effect == 'invert':
            image = ImageOps.invert(image.convert("RGB"))
            
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    @app_commands.command(name="wanted", description="Generates a wanted poster for a user.")
    @app_commands.describe(user="The user to put on the poster. Defaults to you.")
    async def wanted(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        await interaction.response.defer()
        
        avatar_bytes = await self.get_avatar_bytes(target)
        
        loop = asyncio.get_event_loop()
        buffer = await loop.run_in_executor(None, self.generate_wanted_image, avatar_bytes)
        
        file = discord.File(fp=buffer, filename=f"wanted_{target.id}.png")
        await interaction.followup.send(file=file)

    @app_commands.command(name="grayscale", description="Applies a grayscale filter to a user's avatar.")
    @app_commands.describe(user="The user whose avatar to change. Defaults to you.")
    async def grayscale(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        await interaction.response.defer()
        
        avatar_bytes = await self.get_avatar_bytes(target)
        
        loop = asyncio.get_event_loop()
        buffer = await loop.run_in_executor(None, self.process_image_effect, avatar_bytes, 'grayscale')
        
        file = discord.File(fp=buffer, filename=f"grayscale_{target.id}.png")
        await interaction.followup.send(file=file)
        
    @app_commands.command(name="invert", description="Inverts the colors of a user's avatar.")
    @app_commands.describe(user="The user whose avatar to change. Defaults to you.")
    async def invert(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        target = user or interaction.user
        await interaction.response.defer()
        
        avatar_bytes = await self.get_avatar_bytes(target)
        
        loop = asyncio.get_event_loop()
        buffer = await loop.run_in_executor(None, self.process_image_effect, avatar_bytes, 'invert')
        
        file = discord.File(fp=buffer, filename=f"invert_{target.id}.png")
        await interaction.followup.send(file=file)
""")
    },
}

def create_project_structure(base_path, structure):
    for name, content in structure.items():
        current_path = base_path / name
        if isinstance(content, dict):
            current_path.mkdir(exist_ok=True)
            create_project_structure(current_path, content)
        elif isinstance(content, list):
            current_path.mkdir(exist_ok=True)
            for item in content:
                (current_path / item).touch()

def write_file(path, content):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(dedent(content).strip())
        logging.info(f"✅ Created file: {path}")
    except Exception as e:
        logging.error(f"❌ Error creating file {path}: {e}")

def download_file(url, path):
    max_retries = 3
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    for attempt in range(max_retries):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(url, stream=True, timeout=15, headers=headers) as r:
                r.raise_for_status()
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                logging.info(f"✅ Downloaded asset: {path.name}")
                return True
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Attempt {attempt + 1}/{max_retries} failed to download {url}: {e}")
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                logging.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logging.error(f"❌ Failed to download {url} after {max_retries} attempts.")
    return False

def generate_cog_file(cog_name, cog_data):
    # Indent the implemented code to fit correctly inside the class definition
    indented_code = "\n".join(["    " + line for line in cog_data.get('implemented_code', 'pass').strip().split('\n')])
    return COG_TEMPLATE.strip().format(
        cog_name=cog_name,
        implemented_code=indented_code
    )

def main():
    root = Path.cwd()
    items_in_dir = os.listdir(root)
    is_script_only = len(items_in_dir) == 1 and Path(sys.argv[0]).name in items_in_dir

    if items_in_dir and not is_script_only:
        logging.warning("This script should be run in an empty directory.")
        if input("Do you want to continue anyway? (y/n): ").lower() != 'y':
            logging.info("Aborting project generation.")
            return

    logging.info("🚀 Starting ULTIMATE Maxy Bot project generation...")

    logging.info("\n[1/6] Creating project directories...")
    create_project_structure(root, PROJECT_STRUCTURE)
    logging.info("✅ Directory structure created.")

    logging.info("\n[2/6] Downloading assets...")
    assets_path = root / "assets"
    download_file("https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf", assets_path / "fonts" / "font.ttf")
    time.sleep(1)
    
    bg_path = assets_path / "images" / "profile_backgrounds"
    bg_urls = {
        "default.png": "https://i.imgur.com/rS6S1gY.png",
        "galaxy.png": "https://i.imgur.com/yGf2n5s.jpeg",
        "sunset.png": "https://i.imgur.com/1n2aNAn.jpeg",
        "forest.png": "https://i.imgur.com/Gj3sOaZ.jpeg"
    }
    for filename, url in bg_urls.items():
        download_file(url, bg_path / filename)
        time.sleep(1)
    
    templates_path = assets_path / "images" / "templates"
    download_file("https://i.imgur.com/s1gJcT6.png", templates_path / "wanted.png")
    time.sleep(1)

    logging.info("\n[3/6] Writing core application files (bot.py, database.py)...")
    write_file(root / "bot.py", BOT_PY_CONTENT)
    write_file(root / "utils" / "database.py", DATABASE_PY_CONTENT)
    write_file(root / "dashboard" / "server.js", SERVER_JS_CONTENT)
    write_file(root / "assets/json/shop_items.json", SHOP_ITEMS_JSON_CONTENT)

    logging.info("\n[4/6] Writing dashboard template files...")
    partials_path = root / "dashboard/views/partials"
    write_file(partials_path / "header.ejs", HEADER_EJS)
    write_file(partials_path / "footer.ejs", FOOTER_EJS)
    write_file(partials_path / "sidebar.ejs", SIDEBAR_EJS)
    write_file(partials_path / "user_dropdown.ejs", USER_DROPDOWN_EJS)
    pages_path = root / "dashboard/views/pages"
    write_file(pages_path / "index.ejs", INDEX_EJS)
    write_file(pages_path / "dashboard.ejs", DASHBOARD_EJS)
    write_file(pages_path / "guild_settings.ejs", GUILD_SETTINGS_EJS)
    write_file(root / "dashboard/public/css/style.css", STYLE_CSS_CONTENT)
    write_file(root / "dashboard/public/js/main.js", MAIN_JS_CONTENT)

    logging.info("\n[5/6] Writing configuration and dependency files...")
    write_file(root / ".env.sample", ENV_SAMPLE_CONTENT)
    write_file(root / "requirements.txt", REQUIREMENTS_TXT_CONTENT)
    write_file(root / "dashboard" / "package.json", PACKAGE_JSON_CONTENT)

    logging.info("\n[6/6] Creating FULLY IMPLEMENTED Cog files...")
    cogs_root = root / "cogs"
    write_file(cogs_root / "utils.py", COG_UTILS_PY_CONTENT)

    for name, data in COGS_TO_CREATE.items():
        content = generate_cog_file(name, data)
        write_file(cogs_root / f"{name.lower()}.py", content)
    
    (root / "data").mkdir(exist_ok=True)
    write_file(root / "data" / "config.json", json.dumps({"guild_settings": {}}, indent=4))

    print("\n" + "="*50)
    print("🎉 ULTIMATE Maxy Bot project generation complete! 🎉")
    print("="*50)
    print("\n--- NEXT STEPS ---")
    print("1. Copy `.env.sample` to a new file named `.env` and fill in your bot token and credentials.")
    print("   (DISCORD_BOT_TOKEN, DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET are required!)")
    print("\n2. Install Python dependencies:")
    print("   pip install -r requirements.txt")
    print("\n3. **IMPORTANT FOR MUSIC:** Install FFmpeg on your system and ensure it's in your PATH.")
    print("   (Search 'how to install ffmpeg' for your operating system).")
    print("\n4. Install Node.js dependencies for the dashboard (you must have Node.js installed):")
    print("   cd dashboard")
    print("   npm install")
    print("   cd ..")
    print("\n5. Start the dashboard (run this in a SEPARATE terminal):")
    print("   cd dashboard && npm start")
    print("\n6. Start the bot (run this in your main terminal):")
    print("   python bot.py")
    print("\n7. Go to http://localhost:3000 to manage your bot!")
    print("\n--- TROUBLESHOOTING ---")
    print("- If you see a 'Missing Access' or '403 Forbidden' error on bot start:")
    print("  This means your bot was not invited with the correct permissions.")
    print("  Kick the bot and re-invite it using the `/invite` command.")

if __name__ == "__main__":
    main()