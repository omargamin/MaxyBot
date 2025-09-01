# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
import asyncio
import datetime
import random
import logging
import functools
import time

# إعداد الـ logging لعرض معلومات مفيدة
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

# -----------------------
# Cog Command Error Handler
# -----------------------
async def cog_command_error(ctx: commands.Context, error: commands.CommandError):
    """
    Handler عام لأي خطأ يحصل في أي كوماند داخل أي Cog.
    سيعرض رسالة للمستخدم ويقوم بتسجيل الخطأ (logging).
    """
    # تجاهل الأخطاء في الرسائل الخاصة بصمت في بعض الأحيان
    if isinstance(ctx.channel, discord.DMChannel):
        try:
            await ctx.send(f"❌ حدث خطأ في الرسائل الخاصة: {error}")
        except discord.Forbidden:
            pass  # لا يمكننا حتى إرسال رسالة خطأ
        return

    # التعامل مع الأخطاء الشائعة برسائل واضحة للمستخدم
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ ليس لديك الصلاحيات اللازمة لتنفيذ هذا الأمر.", ephemeral=True)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ الأمر ناقص. تحتاج إلى تحديد `{error.param.name}`.", ephemeral=True)
    elif isinstance(error, commands.CommandNotFound):
        pass  # تجاهل الأوامر غير الموجودة
    elif isinstance(error, commands.CommandOnCooldown):
         await ctx.send(f"⏳ هذا الأمر في فترة تهدئة. يرجى المحاولة مرة أخرى بعد **{error.retry_after:.2f} ثانية**.", ephemeral=True)
    else:
        # للأخطاء الأخرى، أرسل رسالة عامة
        await ctx.send(f"❌ حدث خطأ غير متوقع: {error}", ephemeral=True)

    # تسجيل تفصيلي لكل الأخطاء في الكونسول للمطور
    logging.error(
        f"[Error] Cog: {ctx.cog.qualified_name if ctx.cog else 'None'} | "
        f"Command: {ctx.command} | User: {ctx.author} ({ctx.author.id}) | Error: {error}"
    )

# -----------------------
# Utils Cog
# -----------------------
class Utils(commands.Cog):
    """
    مكتبة أدوات شاملة لكل Cogs: تحسين الأداء، التعامل مع Discord بأمان، وإعادة المحاولة آليًا.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------
    # Safety Helpers
    # -----------------------
    async def safe_send(self, destination, content=None, embed=None, delete_after=None):
        """يرسل رسالة مع معالجة الأخطاء المحتملة."""
        try:
            return await destination.send(content=content, embed=embed, delete_after=delete_after)
        except discord.Forbidden:
            logging.warning(f"[Utils] لا يمكن إرسال رسالة إلى {destination}. صلاحيات ناقصة.")
        except discord.HTTPException as e:
            logging.error(f"[Utils] خطأ في إرسال الرسالة (HTTP): {e}")
        return None

    async def safe_edit(self, message: discord.Message, content=None, embed=None):
        """يعدل رسالة مع معالجة الأخطاء المحتملة."""
        try:
            return await message.edit(content=content, embed=embed)
        except discord.Forbidden:
            logging.warning(f"[Utils] لا يمكن تعديل الرسالة {message.id}. صلاحيات ناقصة.")
        except discord.HTTPException as e:
            logging.error(f"[Utils] خطأ في تعديل الرسالة (HTTP): {e}")
        return None

    async def safe_delete(self, message: discord.Message, delay: float = 0):
        """يحذف رسالة مع معالجة الأخطاء المحتملة."""
        try:
            await message.delete(delay=delay)
            return True
        except discord.Forbidden:
            logging.warning(f"[Utils] لا يمكن حذف الرسالة {message.id}. صلاحيات ناقصة.")
        except discord.NotFound:
            logging.warning(f"[Utils] محاولة حذف رسالة غير موجودة: {message.id}")
        except discord.HTTPException as e:
            logging.error(f"[Utils] خطأ في حذف الرسالة (HTTP): {e}")
        return False

    async def bulk_delete_safe(self, channel: discord.TextChannel, limit: int):
        """يحذف عددًا من الرسائل بشكل آمن."""
        try:
            deleted = await channel.purge(limit=limit)
            return len(deleted)
        except discord.Forbidden:
            logging.warning(f"[Utils] لا يمكن حذف الرسائل في {channel}. صلاحيات ناقصة.")
        except discord.HTTPException as e:
            logging.error(f"[Utils] خطأ في الحذف الجماعي للرسائل (HTTP): {e}")
        return 0

    # -----------------------
    # Discord Helpers
    # -----------------------
    def format_user(self, member: discord.Member) -> str:
        """يعرض اسم المستخدم ومعرفه بشكل منسق."""
        return f"{member.display_name} ({member.id})"

    def format_guild(self, guild: discord.Guild) -> str:
        """يعرض اسم السيرفر وعدد أعضائه ومعرفه بشكل منسق."""
        return f"{guild.name} | Members: {guild.member_count} | ID: {guild.id}"

    def random_color(self) -> discord.Color:
        """يختار لونًا عشوائيًا."""
        return discord.Color(random.randint(0, 0xFFFFFF))

    # -----------------------
    # Performance Helpers
    # -----------------------
    async def sync_app_commands(self):
        """يقوم بمزامنة أوامر السلاش (App Commands)."""
        try:
            await self.bot.tree.sync()
            logging.info("[Utils] تمت مزامنة أوامر التطبيق بنجاح.")
        except Exception as e:
            logging.error(f"[Utils] خطأ أثناء مزامنة أوامر التطبيق: {e}")

    def get_timestamp(self) -> str:
        """يرجع الوقت والتاريخ الحاليين كنص."""
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def timeit(self, func):
        """Decorator لقياس وقت تنفيذ دالة async."""
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            result = await func(*args, **kwargs)
            end_time = time.perf_counter()
            logging.info(f"[TimeIt] {func.__name__} استغرقت {end_time - start_time:.4f} ثانية.")
            return result
        return wrapper

    # -----------------------
    # Retry Helpers
    # -----------------------
    async def retry_async(self, coro, retries=3, delay=1.5, backoff=2):
        """
        Decorator لإعادة محاولة تنفيذ دالة async عند فشلها.
        يزيد من مدة الانتظار بين كل محاولة.
        """
        current_delay = delay
        for attempt in range(retries):
            try:
                return await coro()
            except Exception as e:
                logging.warning(f"[Retry] المحاولة {attempt + 1}/{retries} فشلت لـ {coro.__name__ if hasattr(coro, '__name__') else 'coroutine'}: {e}")
                if attempt + 1 == retries:
                    logging.error(f"[Retry] فشلت جميع المحاولات ({retries}) لـ {coro.__name__ if hasattr(coro, '__name__') else 'coroutine'}.")
                    raise  # إظهار الخطأ الأخير بعد فشل كل المحاولات
                await asyncio.sleep(current_delay)
                current_delay *= backoff  # زيادة مدة الانتظار للمحاولة التالية
        return None

# -----------------------
# Setup Function
# -----------------------
async def setup(bot: commands.Bot):
    """يتم استدعاء هذه الدالة تلقائيًا عند تحميل الـ Cog."""
    await bot.add_cog(Utils(bot))
    logging.info("Utils Cog has been loaded.")