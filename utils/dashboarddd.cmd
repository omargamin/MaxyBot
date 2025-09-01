@echo off
chcp 1256
setlocal

REM --------------------------------------------
REM إعداد مجلد الـ Dashboard
REM --------------------------------------------
set "UTILS_DIR=%~dp0"
set "DASHBOARD_DIR=%UTILS_DIR%dashboard"

if not exist "%DASHBOARD_DIR%" (
    mkdir "%DASHBOARD_DIR%"
    echo Dashboard directory created at %DASHBOARD_DIR%
) else (
    echo Dashboard directory already exists
)

REM --------------------------------------------
REM إنشاء ملفات HTML + CSS + JS
REM --------------------------------------------

REM HTML
> "%DASHBOARD_DIR%\index.html" (
echo ^<!DOCTYPE html^>
echo ^<html lang="en"^>
echo ^<head^>
echo     ^<meta charset="UTF-8"^>
echo     ^<meta name="viewport" content="width=device-width, initial-scale=1.0"^>
echo     ^<title^>MaxyBot Dashboard^</title^>
echo     ^<link rel="stylesheet" href="style.css"^>
echo ^</head^>
echo ^<body^>
echo     ^<header^>
echo         ^<h1^>MaxyBot Dashboard^</h1^>
echo         ^<div class="links"^>
echo             ^<a href="https://discord.gg/tSTFAEgewm" target="_blank" class="btn-support"^>Support Server^</a^>
echo             ^<a href="https://discord.com/oauth2/authorize?client_id=1409945925064982668&permissions=268823630&scope=bot%%20applications.commands" target="_blank" class="btn-invite"^>Invite Bot^</a^>
echo         ^</div^>
echo     ^</header^>
echo     ^<main id="dashboard-content"^>
echo         ^<h2^>Welcome to MaxyBot Dashboard^</h2^>
echo         ^<p^>Here you can manage bot settings and view data.^</p^>
echo     ^</main^>
echo     ^<script src="script.js"^>^</script^>
echo ^</body^>
echo ^</html^>
)

REM CSS
> "%DASHBOARD_DIR%\style.css" (
echo body { margin:0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg,#1e1e2f,#2e2e3f); color:#fff; }
echo header { display:flex; justify-content:space-between; align-items:center; padding:20px; background:#111; box-shadow:0 2px 5px rgba(0,0,0,0.5); }
echo header h1 { font-size:1.8rem; color:#4e9af1; }
echo .links a { margin-left:10px; text-decoration:none; padding:8px 16px; border-radius:8px; font-weight:bold; transition:0.3s; }
echo .btn-support { background:#ff6b81; color:#fff; }
echo .btn-support:hover { background:#ff4757; }
echo .btn-invite { background:#1e90ff; color:#fff; }
echo .btn-invite:hover { background:#0a74da; }
echo main { padding:40px; }
echo main h2 { font-size:2rem; color:#ffd700; }
echo main p { font-size:1.1rem; margin-top:10px; color:#ccc; }
)

REM JS
> "%DASHBOARD_DIR%\script.js" (
echo document.addEventListener('DOMContentLoaded', function() {^
echo     console.log('MaxyBot Dashboard Loaded');^
echo });^
)

REM --------------------------------------------
REM إنشاء cog جديد dashboardE.py
REM --------------------------------------------
set "COG_FILE=%UTILS_DIR%dashboardE.py"
> "%COG_FILE%" (
echo # -*- coding: utf-8 -*-^
echo import discord^
echo from discord.ext import commands^
echo from typing import TYPE_CHECKING^
echo if TYPE_CHECKING:^
echo     from ..bot import MaxyBot^
echo ^#
echo class DashboardE(commands.Cog):^
echo     """Cog to support MaxyBot Dashboard functionality."""^
echo     def __init__(self, bot: 'MaxyBot') -> None:^
echo         self.bot = bot^
echo ^#
echo     @commands.command(name="dashboard_test")^
echo     async def dashboard_test(self, ctx: commands.Context):^
echo         await ctx.send("Dashboard Cog is loaded and ready!")^
echo ^#
echo async def setup(bot: 'MaxyBot') -> None:^
echo     await bot.add_cog(DashboardE(bot))^
)

echo ============================================
echo Dashboard setup completed successfully!
echo ============================================

pause
