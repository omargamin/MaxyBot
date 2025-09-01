# main_folder/cogs/admin.py

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List

# Standard library imports
import io
import os
import textwrap
import traceback
import contextlib
import asyncio

# Third-party imports
import discord
from discord import app_commands
from discord.ext import commands

# Local application/library specific imports
if TYPE_CHECKING:
    from ..bot import MaxyBot # Assuming your bot class is named MaxyBot in a 'bot.py' file

from .utils import cog_command_error # Assuming you have this utility for error handling

class Admin(commands.Cog, name="Admin"):
    """
    مجموعة أوامر مخصصة لمالك البوت فقط.
    """
    def __init__(self, bot: MaxyBot):
        self.bot = bot
        self._last_result = None # To store the result of the last eval

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the user invoking the command is the bot owner."""
        return await self.bot.is_owner(interaction.user)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Custom error handler for this cog."""
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("❌ هذا الأمر مخصص لمالك البوت فقط.", ephemeral=True)
        else:
            # Pass other errors to the global error handler if it exists
            await cog_command_error(interaction, error)

    async def cog_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """An autocomplete function for cog management commands."""
        cogs_path = os.path.join(os.path.dirname(__file__), "..", "cogs")
        # Ensure the path is correct if your structure is different
        if not os.path.exists('cogs'):
            # Fallback in case the relative path is tricky
            cogs_path = 'cogs'
            
        choices = []
        for filename in os.listdir(cogs_path):
            if filename.endswith(".py") and not filename.startswith("__"):
                cog_name = filename[:-3]
                if current.lower() in cog_name.lower():
                    choices.append(app_commands.Choice(name=cog_name, value=cog_name))
        return choices[:25] # Discord limit

    ## ----------------- Bot Management Commands ----------------- ##

    @app_commands.command(name="shutdown", description="[للمالك فقط] إيقاف تشغيل البوت.")
    async def shutdown(self, interaction: discord.Interaction):
        """Shuts down the bot."""
        await interaction.response.send_message("جاري إيقاف التشغيل...", ephemeral=True)
        await self.bot.close()

    @app_commands.command(name="status", description="[للمالك فقط] تغيير حالة البوت.")
    @app_commands.describe(
        message="الرسالة التي ستظهر في الحالة",
        activity_type="نوع الحالة (Playing, Watching, etc.)"
    )
    @app_commands.choices(activity_type=[
        app_commands.Choice(name="Playing", value=0),
        app_commands.Choice(name="Listening to", value=2),
        app_commands.Choice(name="Watching", value=3),
        app_commands.Choice(name="Competing in", value=5)
    ])
    async def status(self, interaction: discord.Interaction, message: str, activity_type: int):
        """Changes the bot's presence."""
        activity = discord.Activity(name=message, type=discord.ActivityType(activity_type))
        await self.bot.change_presence(status=discord.Status.online, activity=activity)
        await interaction.response.send_message(f"✅ تم تغيير الحالة إلى: `{activity.type.name.capitalize()} {message}`", ephemeral=True)

    ## ----------------- Cog Management Commands ----------------- ##

    @app_commands.command(name="load", description="[للمالك فقط] تحميل إضافة (cog).")
    @app_commands.describe(cog="اسم الإضافة المراد تحميلها")
    @app_commands.autocomplete(cog=cog_autocomplete)
    async def load(self, interaction: discord.Interaction, cog: str):
        """Loads a cog."""
        try:
            await self.bot.load_extension(f"cogs.{cog.lower()}")
            await interaction.response.send_message(f"✅ تم تحميل إضافة `{cog}` بنجاح.", ephemeral=True)
        except commands.ExtensionAlreadyLoaded:
            await interaction.response.send_message(f"⚠️ الإضافة `{cog}` محملة بالفعل.", ephemeral=True)
        except commands.ExtensionNotFound:
            await interaction.response.send_message(f"❌ لم يتم العثور على الإضافة `{cog}`.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔥 حدث خطأ أثناء تحميل `{cog}`:\n```py\n{e}\n```", ephemeral=True)

    @app_commands.command(name="unload", description="[للمالك فقط] إلغاء تحميل إضافة (cog).")
    @app_commands.describe(cog="اسم الإضافة المراد إلغاء تحميلها")
    @app_commands.autocomplete(cog=cog_autocomplete)
    async def unload(self, interaction: discord.Interaction, cog: str):
        """Unloads a cog."""
        try:
            await self.bot.unload_extension(f"cogs.{cog.lower()}")
            await interaction.response.send_message(f"✅ تم إلغاء تحميل `{cog}` بنجاح.", ephemeral=True)
        except commands.ExtensionNotLoaded:
            await interaction.response.send_message(f"⚠️ الإضافة `{cog}` ليست محملة.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔥 حدث خطأ أثناء إلغاء تحميل `{cog}`:\n```py\n{e}\n```", ephemeral=True)

    @app_commands.command(name="reload", description="[للمالك فقط] إعادة تحميل إضافة (cog).")
    @app_commands.describe(cog="اسم الإضافة لإعادة تحميلها")
    @app_commands.autocomplete(cog=cog_autocomplete)
    async def reload(self, interaction: discord.Interaction, cog: str):
        """Reloads a cog."""
        try:
            await self.bot.reload_extension(f"cogs.{cog.lower()}")
            await interaction.response.send_message(f"✅ تم إعادة تحميل `{cog}` بنجاح.", ephemeral=True)
        except commands.ExtensionNotLoaded:
            await interaction.response.send_message(f"⚠️ الإضافة `{cog}` ليست محملة.", ephemeral=True)
        except commands.ExtensionNotFound:
            await interaction.response.send_message(f"❌ لم يتم العثور على الإضافة `{cog}`.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"🔥 حدث خطأ أثناء إعادة تحميل `{cog}`:\n```py\n{e}\n```", ephemeral=True)

    @app_commands.command(name="cogs", description="[للمالك فقط] عرض قائمة بجميع الإضافات المحملة.")
    async def list_cogs(self, interaction: discord.Interaction):
        """Lists all loaded cogs."""
        loaded_cogs = ", ".join(f"`{cog}`" for cog in self.bot.cogs.keys())
        embed = discord.Embed(
            title="📦 الإضافات (Cogs)",
            description=f"**الإضافات المحملة حاليًا:**\n{loaded_cogs}",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    ## ----------------- Advanced Development Commands ----------------- ##

    @app_commands.command(name="eval", description="[للمالك فقط] تنفيذ كود بايثون.")
    @app_commands.describe(code="الكود المراد تنفيذه")
    async def _eval(self, interaction: discord.Interaction, *, code: str):
        """Evaluates Python code."""
        await interaction.response.defer(ephemeral=True)
        
        # Clean up the code block if present
        if code.startswith('```') and code.endswith('```'):
            code = '\n'.join(code.split('\n')[1:-1])
        
        # Environment for the eval
        env = {
            'bot': self.bot,
            'interaction': interaction,
            'channel': interaction.channel,
            'guild': interaction.guild,
            'user': interaction.user,
            '_': self._last_result, # Access last result with '_'
        }
        env.update(globals())

        # Wrap code in an async function to allow 'await'
        body = textwrap.indent(code, '  ')
        stdout = io.StringIO()
        to_compile = f'import asyncio\nasync def func():\n{body}'

        try:
            exec(to_compile, env)
            func = env['func']
            with contextlib.redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            embed = discord.Embed(title="🔥 خطأ في التنفيذ", color=discord.Color.red())
            embed.add_field(name="Input", value=f"```py\n{code}\n```", inline=False)
            embed.add_field(name="Error", value=f"```py\n{e.__class__.__name__}: {e}\n{traceback.format_exc()}\n```", inline=False)
            return await interaction.followup.send(embed=embed)

        value = stdout.getvalue()
        embed = discord.Embed(title="✅ تم تنفيذ الكود", color=discord.Color.green())
        embed.add_field(name="Input", value=f"```py\n{code}\n```", inline=False)
        
        output = ""
        if value:
            output += f"**Output (stdout):**\n```py\n{value}\n```\n"
        if ret is not None:
            self._last_result = ret
            output += f"**Returned:**\n```py\n{ret}\n```"
        if not output:
            output = "لا يوجد مخرجات."
            
        embed.description = output
        
        # Handle long outputs
        if len(embed.description) > 4000:
             with io.StringIO(embed.description) as f:
                f.seek(0)
                await interaction.followup.send("المخرجات طويلة جدًا، تم إرسالها كملف.", file=discord.File(f, "output.txt"))
        else:
            await interaction.followup.send(embed=embed)

    @app_commands.command(name="shell", description="[للمالك فقط] تنفيذ أوامر shell.")
    @app_commands.describe(command="الأمر المراد تنفيذه")
    async def shell(self, interaction: discord.Interaction, *, command: str):
        """Runs a shell command."""
        await interaction.response.defer(ephemeral=True)
        
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            embed = discord.Embed(title="✅ Shell Command Executed", color=discord.Color.green())
            output = stdout.decode('utf-8', 'replace') if stdout else "No output."
        else:
            embed = discord.Embed(title="🔥 Shell Command Failed", color=discord.Color.red())
            output = stderr.decode('utf-8', 'replace') if stderr else "No error output."
        
        embed.add_field(name="Input", value=f"```sh\n{command}\n```", inline=False)
        
        if len(output) > 1000:
            embed.add_field(name="Output", value="المخرجات طويلة جدًا، تم إرسالها كملف.", inline=False)
            with io.StringIO(output) as f:
                f.seek(0)
                await interaction.followup.send(embed=embed, file=discord.File(f, "output.txt"))
        else:
            embed.add_field(name="Output", value=f"```sh\n{output}\n```", inline=False)
            await interaction.followup.send(embed=embed)


async def setup(bot: MaxyBot):
    """Sets up the Admin cog."""
    await bot.add_cog(Admin(bot))