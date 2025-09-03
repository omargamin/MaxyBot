# -*- coding: utf-8 -*-
import os
import re
from pathlib import Path
import shutil

BASE_DIR = Path(__file__).parent
BACKUP_DIR = BASE_DIR / "backup_nextconvert"

def read_file(file_path: Path) -> str:
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(file_path: Path, content: str):
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"✅ Converted '{file_path}'")

def backup_file(file_path: Path):
    backup_path = BACKUP_DIR / file_path.relative_to(BASE_DIR)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if not backup_path.exists():
        shutil.copy2(file_path, backup_path)
        print(f"💾 Backed up '{file_path}' to '{backup_path}'")

def convert_content(content: str) -> str:
    # تحويل discord → discord.py
    content = re.sub(r'\bnextcord\b', 'discord', content)

    # InteractionBot → commands.Bot
    content = re.sub(r'\bcommands\.InteractionBot\b', 'commands.Bot', content)

    # إزالة await الغلط على add_cog
    content = re.sub(r'await\s+(\w+)\.add_cog', r'\1.add_cog', content)

    # إصلاح Color.red/green/orange
    content = re.sub(r'discord\.Color\.red\(\)', 'discord.Colour.red()', content)
    content = re.sub(r'discord\.Color\.green\(\)', 'discord.Colour.green()', content)
    content = re.sub(r'discord\.Color\.orange\(\)', 'discord.Colour.orange()', content)

    # إصلاح __init__ الغلط
    content = re.sub(r'def\s+__init__\(([^)]*?)\):\s*\n\s*self\):', r'def __init__(\1):', content)

    # إضافة تعريفات آمنة لو مش موجودة
    if 'self.http_session' not in content:
        content = content.replace('def __init__(',
                                  'def __init__(self):\n        self.http_session = None\n', 1)
    if 'self.config_cache_from_file' not in content:
        content = content.replace('def __init__(',
                                  'def __init__(self):\n        self.config_cache_from_file = {}\n', 1)

    # إصلاح أي async def بدون await داخلي (متكرر في cogs)
    content = re.sub(r'async def (\w+)\((.*?)\):\s*\n\s*pass', r'async def \1(\2):\n    return', content)

    return content

def convert_file(file_path: Path):
    if BACKUP_DIR in file_path.parents:
        return
    backup_file(file_path)
    content = read_file(file_path)
    content = convert_content(content)
    write_file(file_path, content)

def convert_all_py_files(base_dir: Path):
    for folder_name in ['cogs', 'utils']:
        folder_path = base_dir / folder_name
        if folder_path.exists():
            for py_file in folder_path.rglob('*.py'):
                convert_file(py_file)
    for py_file in base_dir.glob('*.py'):
        convert_file(py_file)

if __name__ == "__main__":
    BACKUP_DIR.mkdir(exist_ok=True)
    convert_all_py_files(BASE_DIR)
    print("🎉 All Python files have been scanned, backed up, safely converted, and patched!")
