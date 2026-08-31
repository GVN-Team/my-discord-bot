import os
import sys
import json
import re
import asyncio
import traceback
from datetime import datetime, timedelta
from typing import Optional, Union, Dict
from threading import Thread
from flask import Flask

import discord
from discord.ext import commands, tasks

# =========================================================
# 1. 常時稼働用 Webサーバー設定 (Flask)
# =========================================================
app = Flask('')

@app.route('/')
def home():
    # UptimeRobotからのアクセスを受け取るエンドポイント
    return "Bot is running!"

def run_web():
    # Renderから割り当てられるPORTを取得（デフォルト10000）
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    # Discord Botと同時に動かすためスレッドで起動
    t = Thread(target=run_web)
    t.start()


# =========================================================
# 2. Bot基本設定とIntents（権限）
# =========================================================
intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix="/",
    intents=intents,
    help_command=None
)

# 定数設定
LOG_CHANNEL_NAME = "bot-log"
WELCOME_CHANNEL_NAME = "welcome"
TICKET_CATEGORY_NAME = "🎫-チケット"
AUTO_ROLE_NAME = "メンバー"
DATA_FILE = "user_data.json"

# 簡易データ保存（レベル・発言数管理用）
user_data: Dict[str, Dict[str, int]] = {}

def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                user_data = json.load(f)
        except Exception:
            user_data = {}

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"データ保存エラー: {e}")

load_data()


# =========================================================
# 3. イベント＆詳細ログ監視システム
# =========================================================
@bot.event
async def on_ready():
    print("==================================================")
    print(f" ログイン完了: {bot.user.name} (ID: {bot.user.id})")
    print(f" 参加サーバー数: {len(bot.guilds)}")
    print(" 高機能管理Bot: 正常稼働開始")
    print("==================================================")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="!help | サーバー監視中"
        )
    )

async def send_log(guild: discord.Guild, title: str, description: str, color: discord.Color = discord.Color.blue()):
    channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if channel:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        await channel.send(embed=embed)

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    role = discord.utils.get(guild.roles, name=AUTO_ROLE_NAME)
    if role:
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            pass

    welcome_ch = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
    if welcome_ch:
        embed = discord.Embed(
            title="🎉 メンバーが参加しました！",
            description=f"{member.mention} さん、**{guild.name}** へようこそ！",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="アカウント作成日", value=member.created_at.strftime("%Y/%m/%d %H:%M"))
        await welcome_ch.send(embed=embed)

    desc = f"👤 {member.mention} ({member.name})\n**ID:** `{member.id}`\n**作成日:** {member.created_at.strftime('%Y/%m/%d %H:%M:%S')}"
    await send_log(guild, "📥 メンバー参加", desc, discord.Color.green())

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild
    desc = f"📤 {member.mention} ({member.name})\n**ID:** `{member.id}`"
    await send_log(guild, "🚪 メンバー脱退", desc, discord.Color.red())

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    desc = f"**送信者:** {message.author.mention}\n**チャンネル:** {message.channel.mention}\n**削除された内容:**\n```{message.content}```"
    await send_log(message.guild, "🗑️ メッセージ削除検知", desc, discord.Color.gold())

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    desc = f"**編集者:** {before.author.mention}\n**チャンネル:** {before.channel.mention}\n**変更前:**\n```{before.content}```\n**変更後:**\n```{after.content}```"
    await send_log(before.guild, "✏️ メッセージ編集検知", desc, discord.Color.blue())


# =========================================================
# 4. 発言カウンター & レベルアップシステム
# =========================================================
async def process_exp(message: discord.Message):
    user_id = str(message.author.id)
    if user_id not in user_data:
        user_data[user_id] = {"exp": 0, "level": 1, "messages": 0}

    user_data[user_id]["messages"] += 1
    user_data[user_id]["exp"] += 5
    
    current_exp = user_data[user_id]["exp"]
    current_level = user_data[user_id]["level"]
    next_level_exp = current_level * 50

    if current_exp >= next_level_exp:
        user_data[user_id]["level"] += 1
        new_level = user_data[user_id]["level"]
        await message.channel.send(f"🎊 {message.author.mention} さんが **レベル {new_level}** にレベルアップしました！")
        
        if new_level >= 5:
            rank_role = discord.utils.get(message.guild.roles, name="アクティブメンバー")
            if rank_role and rank_role not in message.author.roles:
                try:
                    await message.author.add_roles(rank_role)
                    await message.channel.send(f"🏅 レベル到達特典として `{rank_role.name}` ロールを付与しました！")
                except discord.Forbidden:
                    pass

    save_data()

@bot.command(name="rank", aliases=["level", "lvl"])
async def show_rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_id = str(member.id)
    
    if user_id not in user_data:
        await ctx.send(f"⚠️ {member.display_name} のレベルデータはまだありません。")
        return

    data = user_data[user_id]
    lvl = data["level"]
    exp = data["exp"]
    next_exp = lvl * 50
    msgs = data["messages"]

    embed = discord.Embed(title=f"📊 {member.display_name} のレベルステータス", color=discord.Color.purple())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="現在のレベル", value=f"**Lv. {lvl}**", inline=True)
    embed.add_field(name="経験値 (EXP)", value=f"{exp} / {next_exp}", inline=True)
    embed.add_field(name="通算発言数", value=f"{msgs} 回", inline=True)
    await ctx.send(embed=embed)


# =========================================================
# 5. お問い合わせ（チケット）機能
# =========================================================
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 チケットを開く", style=discord.ButtonStyle.primary, custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
        
        if not category:
            category = await guild.create_category(TICKET_CATEGORY_NAME)

        channel_name = f"ticket-{interaction.user.name}".lower().replace(" ", "-")
        existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if existing_channel:
            await interaction.response.send_message(f"⚠️ 既にチケットが存在します: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        ticket_ch = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)
        
        close_view = TicketCloseView()
        embed = discord.Embed(
            title="🎫 サポートチケット",
            description=f"{interaction.user.mention} さん、お問い合わせ内容を入力してください。\nサポートスタッフが対応します。\n用件が終わったら下のボタンで閉じられます。",
            color=discord.Color.blue()
        )
        await ticket_ch.send(embed=embed, view=close_view)
        await interaction.response.send_message(f"✅ チケットを作成しました: {ticket_ch.mention}", ephemeral=True)

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 チケットを閉じる", style=discord.ButtonStyle.danger, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 このチケットを5秒後に削除します...")
        await asyncio.sleep(5)
        await interaction.channel.delete()

@bot.command(name="setup_ticket")
@commands.has_permissions(administrator=True)
async def setup_ticket(ctx):
    embed = discord.Embed(
        title="📩 お問い合わせパネル",
        description="質問・不具合報告・各種申請は下のボタンを押してチケットを発行してください。",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed, view=TicketView())


# =========================================================
# 6. 高度なユーザー管理 & サーバー統計機能
# =========================================================
@bot.command(name="userinfo", aliases=["user", "ui"])
async def user_info(ctx, member: discord.Member = None):
    member = member or ctx.author
    
    roles = [role.mention for role in member.roles if role.name != "@everyone"]
    roles_str = ", ".join(roles) if roles else "なし"
    
    permissions = [perm.replace("_", " ").title() for perm, value in member.guild_permissions if value]
    perm_str = ", ".join(permissions[:8]) if permissions else "一般的な権限"
    if len(permissions) > 8:
        perm_str += f" 他 {len(permissions) - 8} 個"

    embed = discord.Embed(
        title=f"👤 ユーザー詳細情報 - {member.display_name}",
        color=member.color if member.color != discord.Color.default() else discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ユーザー名", value=f"{member.name}", inline=True)
    embed.add_field(name="ユーザーID", value=f"`{member.id}`", inline=True)
    embed.add_field(name="アカウント種別", value="Bot" if member.bot else "ユーザー", inline=True)
    embed.add_field(name="アカウント作成日時", value=member.created_at.strftime("%Y/%m/%d %H:%M:%S"), inline=False)
    embed.add_field(name="サーバー参加日時", value=member.joined_at.strftime("%Y/%m/%d %H:%M:%S"), inline=False)
    embed.add_field(name=f"保有ロール ({len(roles)})", value=roles_str, inline=False)
    embed.add_field(name="主な所持権限", value=f"```{perm_str}
