import os
import re
import random
import string
import datetime
import asyncio
from threading import Thread
from flask import Flask
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands

# ================= Flask (Render タイムアウト対策) =================
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ================= Bot 初期化 =================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# グローバルデータ構造
ADMIN_DM_TARGET_ID = None
AUTHORIZED_USER_IDS = set()
vending_machines = {}
coupons = {}
backups = {}  # { backup_id: { "owner_id": int, "created_at": str, "data": dict } }

def generate_key(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def check_authority(interaction: discord.Interaction) -> bool:
    app_info = await bot.application_info()
    owner_id = app_info.owner.id
    if interaction.user.id == owner_id or interaction.user.id in AUTHORIZED_USER_IDS or interaction.user.id == interaction.guild.owner_id:
        return True
    await interaction.response.send_message("❌ 権利がないため実行できませんでした。", ephemeral=True)
    return False

# ================= オートコンプリート補助関数 =================
async def vending_machine_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    for m_id, data in vending_machines.items():
        name = data["name"]
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=m_id))
    return choices[:25]

async def backup_key_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    user_id = interaction.user.id
    for b_id, b_data in backups.items():
        if b_data.get("owner_id") == user_id:
            label = f"{b_id} ({b_data.get('created_at', '日時不明')})"
            if current.lower() in b_id.lower() or current.lower() in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=b_id))
    return choices[:25]

async def coupon_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    for c_code, c_data in coupons.items():
        label = f"{c_code} (割引: {c_data['discount']}円)"
        if current.lower() in c_code.lower():
            choices.append(app_commands.Choice(name=label, value=c_code))
    return choices[:25]

async def fetch_paypay_info(paypay_url: str):
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(paypay_url, headers=headers, timeout=5) as response:
                if response.status != 200: return None
                text = await response.text()
                amount_match = re.search(r'\"amount\":\s*(\d+)', text) or re.search(r'(\d+)円', text)
                amount = int(amount_match.group(1)) if amount_match else 0
                is_pending = "PENDING" in text or "保留" in text
                return {"amount": amount, "is_pending": is_pending, "valid": True}
    except Exception as e:
        print(f"PayPay Fetch Error: {e}")
        return None

# ================= 自販機 UI ＆ 決済処理 =================
class PurchaseModal(discord.ui.Modal, title="購入手続き"):
    paypay_url = discord.ui.TextInput(label="PayPay送金リンク", placeholder="https://paypay.me/...", required=True, max_length=100)
    quantity = discord.ui.TextInput(label="購入数", placeholder="1", default="1", required=True, max_length=5)
    coupon_code = discord.ui.TextInput(label="クーポンコード（あれば入力）", placeholder="CUPON2026", required=False, max_length=30)

    def __init__(self, machine_id: str, item_name: str, item_data: dict):
        super().__init__()
        self.machine_id = machine_id
        self.item_name = item_name
        self.item_data = item_data

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            qty = int(self.quantity.value)
            if qty <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 購入数は1以上の数字を入力してください。", ephemeral=True)
            return

        stocks = self.item_data.get("stocks", [])
        stock_type = self.item_data.get("stock_type", "有限")
        if stock_type == "有限" and len(stocks) < qty:
            await interaction.followup.send(f"⚠️ 在庫が不足しています。（残り: {len(stocks)}個）", ephemeral=True)
            return

        unit_price = self.item_data["price_manera"]
        total_price = unit_price * qty

        code = self.coupon_code.value.strip()
        if code in coupons:
            cp = coupons[code]
            if cp.get("vending_machine_id") == self.machine_id and cp.get("count", 0) > 0:
                total_price = max(0, total_price - cp["discount"])
                cp["count"] -= 1

        pay_info = await fetch_paypay_info(self.paypay_url.value)
        sent_amount = pay_info["amount"] if pay_info and pay_info["valid"] else total_price
        is_pending = pay_info["is_pending"] if pay_info and pay_info["valid"] else False

        if sent_amount < total_price:
            await interaction.followup.send(f"金額が不足しています。\n必要金額: {total_price}円\n送金金額: {sent_amount}円", ephemeral=True)
            return

        if is_pending:
            await interaction.followup.send("PayPay受け取りが保留になったため、必ず1分以内に送金してください。\n必ず受け取り保留を解除して送金してください。1分後に届きます。", ephemeral=True)
            await asyncio.sleep(60)
            re_info = await fetch_paypay_info(self.paypay_url.value)
            if re_info and re_info["is_pending"]:
                await interaction.followup.send("PayPay決済の確認に失敗しました。\n受け取り保留を解除して送金してください。", ephemeral=True)
                return

        delivery_items = []
        if stock_type == "有限":
            for _ in range(qty): delivery_items.append(self.item_data["stocks"].pop(0))
        else:
            delivery_items = [self.item_data["stocks"][0]] * qty

        try:
            items_str = "\n".join([f"・{item}" for item in delivery_items])
            await interaction.user.send(f"ご購入ありがとうございます。\nDMにて商品をお送りしました。\n\n【購入商品】: {self.item_name} × {qty}\n【商品内容】:\n{items_str}")
            await interaction.followup.send("ご購入ありがとうございます。\nDMにて商品をお送りしました。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ DMを開放してください。商品が送信できませんでした。", ephemeral=True)
            return

        if ADMIN_DM_TARGET_ID:
            try:
                admin_user = await bot.fetch_user(ADMIN_DM_TARGET_ID)
                if admin_user:
                    machine_name = vending_machines[self.machine_id]["name"]
                    await admin_user.send(f"🛒 **【商品購入通知】**\n・購入者: {interaction.user.mention} (`{interaction.user.name}`)\n・自販機: {machine_name}\n・商品名: {self.item_name} × {qty}\n・決済額: {total_price}円\n・PayPay: {self.paypay_url.value}")
            except Exception as e:
                print(f"管理者DM送信エラー: {e}")

class ItemSelect(discord.ui.Select):
    def __init__(self, machine_id: str):
        self.machine_id = machine_id
        options = []
        items = vending_machines.get(machine_id, {}).get("items", {})
        for item_name, data in items.items():
            desc = f"価格: {data['price_manera']}円"
            emoji = data.get("emoji") if data.get("emoji") else None
            options.append(discord.SelectOption(label=item_name, description=desc, emoji=emoji))
        if not options:
            options.append(discord.SelectOption(label="商品がありません", value="none"))
        super().__init__(placeholder="購入する商品を選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("現在選択できる商品がありません。", ephemeral=True)
            return
        selected_item_name = self.values[0]
        item_data = vending_machines[self.machine_id]["items"][selected_item_name]
        await interaction.response.send_modal(PurchaseModal(self.machine_id, selected_item_name, item_data))

class ItemSelectView(discord.ui.View):
    def __init__(self, machine_id: str):
        super().__init__(timeout=None)
        self.add_item(ItemSelect(machine_id))

class VendingMachineView(discord.ui.View):
    def __init__(self, machine_id: str):
        super().__init__(timeout=None)
        self.machine_id = machine_id

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.primary, emoji="🛒")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.machine_id not in vending_machines:
            await interaction.response.send_message("⚠️ この自販機は現在利用できません。", ephemeral=True)
            return
        view = ItemSelectView(self.machine_id)
        await interaction.response.send_message("購入する商品を選んでください：", view=view, ephemeral=True)


# ================= バックアップ削除 UI =================
class BackupDeleteConfirmView(discord.ui.View):
    def __init__(self, backup_id: str):
        super().__init__(timeout=60)
        self.backup_id = backup_id

    @discord.ui.button(label="削除する", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.backup_id in backups:
            del backups[self.backup_id]
            await interaction.response.send_message(f"🗑️ バックアップデータ `{self.backup_id}` を完全に削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 指定されたバックアップデータは見つかりませんでした。", ephemeral=True)
        self.stop()

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ 削除処理をキャンセルしました。", ephemeral=True)
        self.stop()

class BackupSelectDropdown(discord.ui.Select):
    def __init__(self, user_id: int):
        options = []
        for b_id, b_data in backups.items():
            if b_data.get("owner_id") == user_id:
                desc = f"作成日: {b_data.get('created_at', '不明')} | サーバー: {b_data['data'].get('guild_name', '不明')}"
                options.append(discord.SelectOption(label=f"ID: {b_id}", value=b_id, description=desc[:100]))
        if not options:
            options.append(discord.SelectOption(label="削除可能なバックアップがありません", value="none"))
        super().__init__(placeholder="削除するバックアップデータを選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("❌ 削除できるバックアップデータがありません。", ephemeral=True)
            return
        selected_id = self.values[0]
        view = BackupDeleteConfirmView(selected_id)
        msg_content = (
            f"本当にバックアップデータ「**{selected_id}**」を削除しますか？\n\n"
            "```\n(この操作は取り消せません\n"
            "全てのバックアップデータ及び、ロール/カテゴリ/テキスト/ボイス/フォーラム(タグ)/スレッド/メッセージ/アイコン、データを削除します。)\n```"
        )
        await interaction.response.send_message(msg_content, view=view, ephemeral=True)

class BackupDeleteSelectView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(BackupSelectDropdown(user_id))


# ================= コマンド定義 =================

# --- バックアップ関連コマンド ---
@bot.tree.command(name="backup", description="サーバー全体(ロール/カテゴリ/テキスト/ボイス/フォーラム/スレッド/メッセージ/アイコン)をバックアップしKeyを発行します")
async def backup(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    backup_id = generate_key(8)

    # 1. サーバーアイコンの取得
    icon_url = str(guild.icon.url) if guild.icon else None

    # 2. ロール情報の取得
    roles_data = []
    for r in sorted(guild.roles, key=lambda x: x.position):
        if not r.is_default() and not r.managed:
            roles_data.append({
                "name": r.name,
                "permissions": r.permissions.value,
                "color": r.color.value,
                "hoist": r.hoist,
                "mentionable": r.mentionable
            })

    # チャンネル処理共通関数
    async def extract_channel_info(ch):
        info = {
            "name": ch.name,
            "type": str(ch.type),
            "topic": getattr(ch, "topic", None),
            "messages": [],
            "threads": [],
            "tags": []
        }
        # フォーラムタグ
        if hasattr(ch, "available_tags") and ch.available_tags:
            info["tags"] = [{"name": t.name, "emoji": str(t.emoji) if t.emoji else None, "moderated": t.moderated} for t in ch.available_tags]

        # メッセージ取得
        if isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
            try:
                async for msg in ch.history(limit=50, oldest_first=True):
                    if not msg.author.bot:
                        info["messages"].append({"author": msg.author.display_name, "content": msg.content})
            except Exception: pass

        # スレッド取得
        if hasattr(ch, "threads"):
            for th in ch.threads:
                th_info = {"name": th.name, "messages": []}
                try:
                    async for msg in th.history(limit=30, oldest_first=True):
                        if not msg.author.bot:
                            th_info["messages"].append({"author": msg.author.display_name, "content": msg.content})
                except Exception: pass
                info["threads"].append(th_info)

        return info

    # 3. カテゴリ・チャンネル・スレッド・メッセージの取得
    categories_data = []
    for cat in guild.categories:
        cat_channels = []
        for ch in cat.channels:
            cat_channels.append(await extract_channel_info(ch))
        categories_data.append({"name": cat.name, "channels": cat_channels})

    no_cat_channels = []
    for ch in guild.channels:
        if ch.category is None and not isinstance(ch, discord.CategoryChannel):
            no_cat_channels.append(await extract_channel_info(ch))

    # データの統合保存
    import datetime
    backups[backup_id] = {
        "owner_id": interaction.user.id,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data": {
            "guild_name": guild.name,
            "icon_url": icon_url,
            "roles": roles_data,
            "categories": categories_data,
            "no_cat_channels": no_cat_channels
        }
    }

    embed = discord.Embed(title="📦 バックアップ作成完了", color=discord.Color.green())
    embed.add_field(name="🔑 バックアップKey", value=f"`{backup_id}`", inline=False)
    embed.add_field(name="📊 保存データ概要", value=f"・ロール: {len(roles_data)}件\n・カテゴリ: {len(categories_data)}件\n・アイコン/メッセージ/スレッド/フォーラム対応済み", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="restore", description="バックアップ済みのデータを復元します")
@app_commands.autocomplete(key=backup_key_autocomplete)
async def restore(interaction: discord.Interaction, key: str):
    if not await check_authority(interaction): return
    if key not in backups:
        await interaction.response.send_message("❌ 指定されたバックアップKeyが見つかりません。", ephemeral=True)
        return

    await interaction.response.send_message("⚠️ 復元プロセスを開始します。既存の全チャンネル・ロールを削除して復元します...", ephemeral=True)
    guild = interaction.guild
    b_data = backups[key]["data"]

    # 1. アイコン復元
    if b_data.get("icon_url"):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(b_data["icon_url"]) as resp:
                    if resp.status == 200:
                        icon_bytes = await resp.read()
                        await guild.edit(icon=icon_bytes)
        except Exception: pass

    # 2. 既存チャンネル・ロール削除
    for ch in list(guild.channels):
        try: await ch.delete(); await asyncio.sleep(0.2)
        except Exception: pass
    for r in list(guild.roles):
        if not r.is_default() and not r.managed and r < guild.me.top_role:
            try: await r.delete(); await asyncio.sleep(0.2)
            except Exception: pass

    # 3. ロール復元
    for r_data in b_data["roles"]:
        try:
            await guild.create_role(name=r_data["name"], permissions=discord.Permissions(r_data["permissions"]), color=discord.Color(r_data["color"]), hoist=r_data["hoist"], mentionable=r_data["mentionable"])
            await asyncio.sleep(0.2)
        except Exception: pass

    # チャンネル作成用ヘルパー
    async def create_channel_obj(parent, ch_data):
        try:
            ch_type = ch_data["type"]
            created_ch = None
            if ch_type == "text":
                created_ch = await (parent.create_text_channel(ch_data["name"], topic=ch_data["topic"]) if parent else guild.create_text_channel(ch_data["name"], topic=ch_data["topic"]))
            elif ch_type == "voice":
                created_ch = await (parent.create_voice_channel(ch_data["name"]) if parent else guild.create_voice_channel(ch_data["name"]))
            elif ch_type == "forum":
                tags = [discord.ForumTag(name=t["name"], moderated=t["moderated"]) for t in ch_data.get("tags", [])]
                created_ch = await (parent.create_forum(ch_data["name"], available_tags=tags) if parent else guild.create_forum(ch_data["name"], available_tags=tags))
            else:
                created_ch = await (parent.create_text_channel(ch_data["name"]) if parent else guild.create_text_channel(ch_data["name"]))

            if created_ch:
                for m in ch_data.get("messages", []):
                    await created_ch.send(f"**[{m['author']}]**: {m['content']}")
                for th_data in ch_data.get("threads", []):
                    try:
                        new_th = await created_ch.create_thread(name=th_data["name"])
                        for tm in th_data.get("messages", []):
                            await new_th.send(f"**[{tm['author']}]**: {tm['content']}")
                    except Exception: pass
        except Exception: pass

    # 4. カテゴリ＆チャンネル復元
    for cat_data in b_data["categories"]:
        try:
            new_cat = await guild.create_category(cat_data["name"])
            for ch_data in cat_data["channels"]:
                await create_channel_obj(new_cat, ch_data)
                await asyncio.sleep(0.2)
        except Exception: pass

    for ch_data in b_data["no_cat_channels"]:
        await create_channel_obj(None, ch_data)
        await asyncio.sleep(0.2)

    try:
        sys_ch = await guild.create_text_channel("復元完了通知")
        await sys_ch.send(f"✅ バックアップKey `{key}` による復元処理が完了しました！")
    except Exception: pass

@bot.tree.command(name="backup_delete", description="自分のバックアップをリストから選択して削除します")
async def backup_delete(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    view = BackupDeleteSelectView(interaction.user.id)
    await interaction.response.send_message("🗑️ 削除したいバックアップデータを選択してください：", view=view, ephemeral=True)

@bot.tree.command(name="backuplist", description="自分のバックアップデータ及びバックアップキーを一覧表示します")
async def backuplist(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    user_id = interaction.user.id
    user_backups = {k: v for k, v in backups.items() if v.get("owner_id") == user_id}

    if not user_backups:
        await interaction.response.send_message("ℹ️ 保有しているバックアップデータはありません。", ephemeral=True)
        return

    embed = discord.Embed(title="📋 バックアップデータ一覧", color=discord.Color.blue())
    for b_id, b_info in user_backups.items():
        g_name = b_info["data"].get("guild_name", "不明なサーバー")
        c_at = b_info.get("created_at", "日時不明")
        embed.add_field(name=f"🔑 Key: `{b_id}`", value=f"・対象サーバー: **{g_name}**\n・作成日時: {c_at}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- クーポン関連コマンド ---
@bot.tree.command(name="coupon_create", description="割引クーポンを作成します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def coupon_create(interaction: discord.Interaction, vending_machine_id: str, discount: int, count: int, code: str = None):
    if not await check_authority(interaction): return
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("❌ 指定された自販機が見つかりません。", ephemeral=True)
        return

    cp_code = code if code else generate_key(6)
    coupons[cp_code] = {"vending_machine_id": vending_machine_id, "discount": discount, "count": count}
    await interaction.response.send_message(f"🎟️ クーポンを作成しました！\n・コード: `{cp_code}`\n・割引額: {discount}円\n・使用可能回数: {count}回", ephemeral=True)

@bot.tree.command(name="coupon_delete", description="指定したクーポンを削除します")
@app_commands.autocomplete(code=coupon_autocomplete)
async def coupon_delete(interaction: discord.Interaction, code: str):
    if not await check_authority(interaction): return
    if code in coupons:
        del coupons[code]
        await interaction.response.send_message(f"🗑️ クーポン `{code}` を削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 指定されたクーポンが見つかりません。", ephemeral=True)

@bot.tree.command(name="coupon_list", description="作成されたクーポンの一覧を表示します")
async def coupon_list(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    if not coupons:
        await interaction.response.send_message("ℹ️ 発行されているクーポンはありません。", ephemeral=True)
        return

    embed = discord.Embed(title="🎟️ 発行済みクーポン一覧", color=discord.Color.gold())
    for code, data in coupons.items():
        m_name = vending_machines.get(data["vending_machine_id"], {}).get("name", "削除された自販機")
        embed.add_field(name=f"コード: `{code}`", value=f"・対象自販機: {m_name}\n・割引額: {data['discount']}円\n・残り回数: {data['count']}回", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 認証 ＆ 管理者コマンド ---
@bot.tree.command(name="認証", description="ワンクリックで指定ロールが付与される認証パネルを作成します")
async def setup_simple_verify(interaction: discord.Interaction, role: discord.Role, title: str = "認証パネル", description: str = "下のボタンを押して認証してください", buttonlabel: str = "verify✅"):
    if not await check_authority(interaction): return
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    view = SimpleVerifyView(role_id=role.id, button_label=buttonlabel)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 認証パネルを設置しました！", ephemeral=True)

class SimpleVerifyView(discord.ui.View):
    def __init__(self, role_id: int, button_label: str):
        super().__init__(timeout=None)
        btn = discord.ui.Button(label=button_label, style=discord.ButtonStyle.success, custom_id="simple_verify_btn_action")
        btn.callback = self.verify_callback
        self.role_id = role_id
        self.add_item(btn)

    async def verify_callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ {role.mention} を付与しました！", ephemeral=True)
        else:
            await interaction.response.send_message("❌ ロールが見つかりません。", ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (Commands Synced)")

# ================= 起動処理 =================
keep_alive()
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
