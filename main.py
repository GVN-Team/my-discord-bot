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
    command_prefix="!",
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
    embed.add_field(name="主な所持権限", value=f"```{perm_str}```", inline=False)
    embed.set_footer(text=f"実行者: {ctx.author.name}", icon_url=ctx.author.display_avatar.url)

    await ctx.send(embed=embed)

@bot.command(name="serverinfo", aliases=["server", "si"])
async def server_info(ctx):
    guild = ctx.guild
    total_members = guild.member_count
    bots = sum(1 for m in guild.members if m.bot)
    humans = total_members - bots
    
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)
    roles_count = len(guild.roles)

    embed = discord.Embed(
        title=f"🏰 {guild.name} サーバー統計情報",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
        
    embed.add_field(name="サーバーID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="サーバー所有者", value=f"{guild.owner.mention}", inline=True)
    embed.add_field(name="開設日時", value=guild.created_at.strftime("%Y/%m/%d %H:%M"), inline=True)
    
    embed.add_field(
        name=f"👥 メンバー構成 ({total_members}名)",
        value=f"└ ユーザー: **{humans}** 名\n└ Bot: **{bots}** つ",
        inline=True
    )
    embed.add_field(
        name="💬 チャンネル構成",
        value=f"└ テキスト: **{text_channels}**\n└ ボイス: **{voice_channels}**\n└ カテゴリ: **{categories}**",
        inline=True
    )
    embed.add_field(
        name="🛡️ その他データ",
        value=f"└ ロール数: **{roles_count}**\n└ ブースト数: **{guild.premium_subscription_count}**",
        inline=True
    )
    embed.set_footer(text=f"Requested by {ctx.author.name}")

    await ctx.send(embed=embed)


# =========================================================
# 7. 高度なロール管理機能
# =========================================================
@bot.command(name="addrole", aliases=["roleadd"])
@commands.has_permissions(manage_roles=True)
async def add_role(ctx, member: discord.Member, *, role_input: str):
    role = discord.utils.get(ctx.guild.roles, name=role_input)
    if not role and role_input.startswith("<@&") and role_input.endswith(">"):
        role_id = int(role_input[3:-1])
        role = ctx.guild.get_role(role_id)
        
    if not role:
        await ctx.send(f"❌ エラー: `{role_input}` というロールが見つかりません。")
        return

    if role.position >= ctx.guild.me.top_role.position:
        await ctx.send("❌ エラー: Botの最上位ロールより高い（または同じ）位置にあるロールは操作できません。")
        return

    if role in member.roles:
        await ctx.send(f"⚠️ {member.mention} は既に `{role.name}` ロールを保持しています。")
        return

    await member.add_roles(role)
    embed = discord.Embed(
        title="✅ ロール付与完了",
        description=f"{member.mention} に `{role.name}` ロールを付与しました。",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)
    await send_log(ctx.guild, "🛡️ ロール手動付与", f"実行者: {ctx.author.mention}\n対象: {member.mention}\n付与ロール: `{role.name}`")

@bot.command(name="removerole", aliases=["roleremove", "delrole"])
@commands.has_permissions(manage_roles=True)
async def remove_role(ctx, member: discord.Member, *, role_input: str):
    role = discord.utils.get(ctx.guild.roles, name=role_input)
    if not role and role_input.startswith("<@&") and role_input.endswith(">"):
        role_id = int(role_input[3:-1])
        role = ctx.guild.get_role(role_id)

    if not role:
        await ctx.send(f"❌ エラー: `{role_input}` というロールが見つかりません。")
        return

    if role.position >= ctx.guild.me.top_role.position:
        await ctx.send("❌ エラー: Botの最上位ロールより高い位置にあるロールは操作できません。")
        return

    if role not in member.roles:
        await ctx.send(f"⚠️ {member.mention} は `{role.name}` ロールを持っていません。")
        return

    await member.remove_roles(role)
    embed = discord.Embed(
        title="🗑️ ロール削除完了",
        description=f"{member.mention} から `{role.name}` ロールを剥奪しました。",
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)
    await send_log(ctx.guild, "🛡️ ロール手動剥奪", f"実行者: {ctx.author.mention}\n対象: {member.mention}\n剥奪ロール: `{role.name}`", discord.Color.orange())

@bot.command(name="roleall")
@commands.has_permissions(administrator=True)
async def role_all(ctx, *, role_input: str):
    role = discord.utils.get(ctx.guild.roles, name=role_input)
    if not role:
        await ctx.send(f"❌ エラー: `{role_input}` というロールが見つかりません。")
        return

    msg = await ctx.send(f"🔄 **{role.name}** を全一般メンバー（Bot除く）に一括付与しています... 少々お待ちください。")
    count = 0
    for member in ctx.guild.members:
        if not member.bot and role not in member.roles:
            try:
                await member.add_roles(role)
                count += 1
                await asyncio.sleep(0.5)
            except Exception:
                continue

    await msg.edit(content=f"✅ 処理完了: 計 **{count}** 名のメンバーに `{role.name}` ロールを一括付与しました。")
    await send_log(ctx.guild, "🛡️ ロール一括付与", f"実行者: {ctx.author.mention}\n対象人数: {count}名\n対象ロール: `{role.name}`")

@bot.command(name="roles", aliases=["rolelist"])
async def list_roles(ctx):
    roles = sorted([r for r in ctx.guild.roles if r.name != "@everyone"], key=lambda r: r.position, reverse=True)
    
    if not roles:
        await ctx.send("現在カスタムロールはありません。")
        return

    description_lines = []
    for r in roles[:20]:
        description_lines.append(f"• {r.mention} — **{len(r.members)}** 名 (ID: `{r.id}`)")

    embed = discord.Embed(
        title=f"📜 {ctx.guild.name} のロール一覧",
        description="\n".join(description_lines),
        color=discord.Color.blue()
    )
    if len(roles) > 20:
        embed.set_footer(text=f"他 {len(roles) - 20} 個のロールが存在します。")

    await ctx.send(embed=embed)


# =========================================================
# 8. モデレーション機能 (Kick, Ban, Mute, Clear)
# =========================================================
@bot.command(name="clear", aliases=["purge", "clean"])
@commands.has_permissions(manage_messages=True)
async def clear_messages(ctx, amount: int = 10):
    if amount < 1:
        await ctx.send("❌ 1以上の数値を指定してください。")
        return
    
    if amount > 100:
        await ctx.send("⚠️ 1度に削除できるメッセージは最大100件までです。100件に制限して実行します。")
        amount = 100

    deleted = await ctx.channel.purge(limit=amount + 1)
    
    msg = await ctx.send(f"🧹 **{len(deleted) - 1}** 件のメッセージを削除しました。")
    await send_log(
        ctx.guild, 
        "🧹 メッセージ一括削除", 
        f"実行者: {ctx.author.mention}\n実行チャンネル: {ctx.channel.mention}\n削除件数: {len(deleted) - 1}件",
        discord.Color.gold()
    )
    
    await asyncio.sleep(3)
    try:
        await msg.delete()
    except discord.NotFound:
        pass

@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick_member(ctx, member: discord.Member, *, reason: str = "理由なし"):
    if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        await ctx.send("❌ 自分と同等以上の権限を持つメンバーをキックすることはできません。")
        return

    try:
        await member.kick(reason=reason)
        embed = discord.Embed(
            title="👞 メンバーをキックしました",
            description=f"対象: {member.mention}\n理由: {reason}",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        await send_log(ctx.guild, "👞 Kick実行", f"実行者: {ctx.author.mention}\n対象: {member.mention} (`{member.id}`)\n理由: {reason}", discord.Color.red())
    except Exception as e:
        await ctx.send(f"❌ キック処理に失敗しました: {e}")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban_member(ctx, user: Union[discord.Member, discord.User], *, reason: str = "理由なし"):
    try:
        await ctx.guild.ban(user, reason=reason)
        embed = discord.Embed(
            title="🔨 メンバーをBANしました",
            description=f"対象: {user.mention}\n理由: {reason}",
            color=discord.Color.dark_red()
        )
        await ctx.send(embed=embed)
        await send_log(ctx.guild, "🔨 BAN実行", f"実行者: {ctx.author.mention}\n対象: {user.mention} (`{user.id}`)\n理由: {reason}", discord.Color.dark_red())
    except Exception as e:
        await ctx.send(f"❌ BAN処理に失敗しました: {e}")

@bot.command(name="timeout", aliases=["mute"])
@commands.has_permissions(moderate_members=True)
async def timeout_member(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "理由なし"):
    if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        await ctx.send("❌ 自分と同等以上の権限を持つメンバーをタイムアウトすることはできません。")
        return

    duration = timedelta(minutes=minutes)
    try:
        await member.timeout(duration, reason=reason)
        embed = discord.Embed(
            title="🤐 タイムアウトを設定しました",
            description=f"対象: {member.mention}\n期間: **{minutes}** 分間\n理由: {reason}",
            color=discord.Color.dark_gold()
        )
        await ctx.send(embed=embed)
        await send_log(ctx.guild, "🤐 タイムアウト設定", f"実行者: {ctx.author.mention}\n対象: {member.mention}\n期間: {minutes}分\n理由: {reason}", discord.Color.dark_gold())
    except Exception as e:
        await ctx.send(f"❌ タイムアウト処理に失敗しました: {e}")


# =========================================================
# 9. 自動安全フィルター & メッセージ受信イベント
# =========================================================
NG_WORDS = ["荒らし", "スパム", "Discord招待リンク禁止"]

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    for word in NG_WORDS:
        if word in message.content and not message.author.guild_permissions.administrator:
            try:
                await message.delete()
                warning_msg = await message.channel.send(
                    f"⚠️ {message.author.mention} 不適切なワードが含まれていたため削除しました。"
                )
                await send_log(
                    message.guild,
                    "⚠️ NGワード検知削除",
                    f"送信者: {message.author.mention}\nチャンネル: {message.channel.mention}\n内容: `{message.content}`",
                    discord.Color.red()
                )
                await asyncio.sleep(4)
                await warning_msg.delete()
                return
            except discord.Forbidden:
                pass

    await process_exp(message)
    await bot.process_commands(message)


# =========================================================
# 10. ヘルプ & エラーハンドリング & 起動メイン処理
# =========================================================
@bot.command(name="help")
async def custom_help(ctx):
    embed = discord.Embed(
        title="🤖 Bot コマンドヘルプ一覧",
        description="利用可能なコマンドの一覧です。",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )

    embed.add_field(
        name="👤 ユーザー・レベル機能",
        value="`!userinfo` : ユーザー情報\n`!serverinfo` : サーバー統計\n`!rank` : レベル確認\n`!ping` : 応答速度",
        inline=False
    )
    embed.add_field(
        name="🛡️ ロール・チケット管理",
        value="`!addrole` / `!removerole` : ロール操作\n`!roleall` : 一括ロール付与\n`!roles` : ロール一覧\n`!setup_ticket` : チケット設置",
        inline=False
    )
    embed.add_field(
        name="👞 モデレーション",
        value="`!clear [件数]` : メッセージ削除\n`!timeout` : タイムアウト\n`!kick` / `!ban` : キック・BAN",
        inline=False
    )

    embed.set_footer(text=f"実行者: {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)

@bot.command(name="ping")
async def ping_check(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! レイテンシ: **{latency} ms**")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 このコマンドを実行する権限が不足しています。")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ 引数が不足しています。`!help` を確認してください。")
    else:
        print(f"エラー発生: {error}", file=sys.stderr)

if __name__ == "__main__":
    # Webサーバーを先に別スレッドで立ち上げる
    keep_alive()
    
    # Discord Botの起動
    TOKEN = os.getenv("DISCORD_TOKEN")
    if TOKEN:
        try:
            bot.run(TOKEN)
        except Exception as e:
            print(f"❌ 起動エラー: {e}")
    else:
        print("❌ エラー: DISCORD_TOKEN が設定されていません。")
