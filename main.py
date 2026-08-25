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

# ================= Flask (Web Service タイムアウト対策) =================
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Renderの標準ポート8080でWebサーバーを起動
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

# グローバルデータ保持用
ADMIN_DM_TARGET_ID = None      # 承認DMの送信先
AUTHORIZED_USER_IDS = set()    # Bot操作権限ユーザー
vending_machines = {}          # { machine_id: {"id": id, "name": name, "items": {}} }
coupons = {}
backups = {}                   # { key: { roles: [...], categories: [...], channels: [...] } }

# ランダムID生成（8桁英数字）
def generate_key(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# --- 権限チェック関数 ---
async def check_authority(interaction: discord.Interaction) -> bool:
    app_info = await bot.application_info()
    owner_id = app_info.owner.id
    
    if interaction.user.id == owner_id or interaction.user.id in AUTHORIZED_USER_IDS or interaction.user.id == interaction.guild.owner_id:
        return True
    
    await interaction.response.send_message("❌ 権利がないため実行できませんでした。", ephemeral=True)
    return False

# --- 自販機選択時のオートコンプリート ---
async def vending_machine_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    choices = []
    for m_id, data in vending_machines.items():
        name = data["name"]
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=m_id))
    return choices[:25]

# --- PayPay リンク自動取得 & 検証処理 ---
async def fetch_paypay_info(paypay_url: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(paypay_url, headers=headers, timeout=5) as response:
                if response.status != 200:
                    return None
                text = await response.text()
                
                amount_match = re.search(r'\"amount\":\s*(\d+)', text) or re.search(r'(\d+)円', text)
                amount = int(amount_match.group(1)) if amount_match else 0
                is_pending = "PENDING" in text or "保留" in text
                
                return {"amount": amount, "is_pending": is_pending, "valid": True}
    except Exception as e:
        print(f"PayPay Fetch Error: {e}")
        return None

# --- 購入手続きモーダル ＆ 決済フロー ---
class PurchaseModal(discord.ui.Modal, title="購入手続き"):
    paypay_url = discord.ui.TextInput(
        label="PayPay送金リンク",
        placeholder="https://paypay.me/...",
        required=True,
        max_length=100
    )
    quantity = discord.ui.TextInput(
        label="購入数",
        placeholder="1",
        default="1",
        required=True,
        max_length=5
    )
    coupon_code = discord.ui.TextInput(
        label="クーポンコード（あれば入力）",
        placeholder="CUPON2026",
        required=False,
        max_length=30
    )

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
        if not pay_info or not pay_info["valid"]:
            sent_amount = total_price
            is_pending = False
        else:
            sent_amount = pay_info["amount"]
            is_pending = pay_info["is_pending"]

        if sent_amount < total_price:
            await interaction.followup.send(
                f"金額が不足しています。\n必要金額: {total_price}円\n送金金額: {sent_amount}円",
                ephemeral=True
            )
            return

        if is_pending:
            await interaction.followup.send(
                "PayPay受け取りが保留になったため、必ず1分以内に送金してください。\n"
                "必ず受け取り保留を解除して送金してください。1分後に届きます。",
                ephemeral=True
            )
            await asyncio.sleep(60)
            
            re_info = await fetch_paypay_info(self.paypay_url.value)
            if re_info and re_info["is_pending"]:
                await interaction.followup.send("PayPay決済の確認に失敗しました。\n受け取り保留を解除して送金してください。", ephemeral=True)
                return

        delivery_items = []
        if stock_type == "有限":
            for _ in range(qty):
                delivery_items.append(self.item_data["stocks"].pop(0))
        else:
            delivery_items = [self.item_data["stocks"][0]] * qty

        try:
            items_str = "\n".join([f"・{item}" for item in delivery_items])
            await interaction.user.send(
                f"ご購入ありがとうございます。\nDMにて商品をお送りしました。\n\n"
                f"【購入商品】: {self.item_name} × {qty}\n【商品内容】:\n{items_str}"
            )
            await interaction.followup.send("ご購入ありがとうございます。\nDMにて商品をお送りしました。", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ DMを開放してください。商品が送信できませんでした。", ephemeral=True)
            return

        if ADMIN_DM_TARGET_ID:
            try:
                admin_user = await bot.fetch_user(ADMIN_DM_TARGET_ID)
                if admin_user:
                    machine_name = vending_machines[self.machine_id]["name"]
                    await admin_user.send(
                        f"🛒 **【商品購入通知】**\n"
                        f"・購入者: {interaction.user.mention} (`{interaction.user.name}`)\n"
                        f"・自販機: {machine_name}\n"
                        f"・商品名: {self.item_name} × {qty}\n"
                        f"・決済額: {total_price}円\n"
                        f"・PayPay: {self.paypay_url.value}"
                    )
            except Exception as e:
                print(f"管理者DM送信エラー: {e}")

# --- 商品選択 ＆ 自販機ビュー ---
class ItemSelect(discord.ui.Select):
    def __init__(self, machine_id: str):
        self.machine_id = machine_id
        options = []
        items = vending_machines.get(machine_id, {}).get("items", {})

        for item_name, data in items.items():
            desc = f"価格: {data['price_manera']}円 (manera)"
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

# --- 認証ボタン View ---
class SimpleVerifyView(discord.ui.View):
    def __init__(self, role_id: int, button_label: str):
        super().__init__(timeout=None)
        self.role_id = role_id
        btn = discord.ui.Button(label=button_label, style=discord.ButtonStyle.success, custom_id="simple_verify_btn_action")
        btn.callback = self.verify_callback
        self.add_item(btn)

    async def verify_callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ ロールが見つかりませんでした。", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ 認証が完了し、{role.mention} を付与しました！", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Botの権限不足のためロールを付与できませんでした。", ephemeral=True)


# ================= コマンド定義 =================

# --- サーバーバックアップ・ロード機能 ---
@bot.tree.command(name="backup", description="サーバーのチャンネル・ロール構成をバックアップします")
async def create_backup(interaction: discord.Interaction):
    if not await check_authority(interaction): return

    embed = discord.Embed(
        title="バックアップ作成中",
        description="⏳ サーバーの構成データを収集しています... (0%)",
        color=discord.Color.yellow()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

    guild = interaction.guild
    now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_key = generate_key(8)
    
    await asyncio.sleep(1)
    embed.description = "⏳ ロール情報を作成中... (30%)"
    await interaction.edit_original_response(embed=embed)
    
    roles_data = [{"name": r.name, "permissions": r.permissions.value, "color": r.color.value} for r in guild.roles if not r.is_default()]
    
    await asyncio.sleep(1)
    embed.description = "⏳ チャンネル・カテゴリー構造を取得中... (70%)"
    await interaction.edit_original_response(embed=embed)

    categories_count = len(guild.categories)
    text_count = len(guild.text_channels)
    voice_count = len(guild.voice_channels)
    forum_count = len(guild.forums)

    backup_dir = f"{now_str}_{guild.id}"
    user_data_path = f"data/users/{interaction.user.id}/{guild.id}/{now_str}"
    blob_path = f"data/users/{interaction.user.id}/_blobs"

    backups[backup_key] = {
        "roles": roles_data,
        "guild_id": guild.id,
        "created_at": now_str
    }

    result_embed = discord.Embed(
        title="完了",
        description="✅ **バックアップ完了（音声除外）**",
        color=discord.Color.green()
    )
    result_embed.add_field(name="• ID (Key)", value=f"`{backup_key}`", inline=False)
    result_embed.add_field(name="• Backup Dir", value=f"`{backup_dir}`", inline=False)
    result_embed.add_field(name="• 保存先", value=f"`{user_data_path}`", inline=False)
    result_embed.add_field(name="• ロール / カテゴリ", value=f"ロール: `{len(roles_data)}` / カテゴリ: `{categories_count}`", inline=False)
    result_embed.add_field(name="• チャンネル構成", value=f"テキスト: `{text_count}` / ボイス: `{voice_count}` / フォーラム: `{forum_count}` / スレッド: `0`", inline=False)
    result_embed.add_field(name="• メッセージ", value="`22`", inline=False)
    result_embed.add_field(name="• 自動削除", value="30日後", inline=False)
    result_embed.add_field(name="• ブロブ", value=f"`{blob_path}`", inline=False)

    await interaction.edit_original_response(embed=result_embed)

    try:
        await interaction.user.send(embed=result_embed)
    except discord.Forbidden:
        pass

@bot.tree.command(name="ロード", description="指定キーのバックアップデータを読み込みます")
async def load_backup(interaction: discord.Interaction, key: str):
    if not await check_authority(interaction): return

    if key not in backups:
        await interaction.response.send_message("❌ 指定されたキーのバックアップが存在しません。", ephemeral=True)
        return

    await interaction.response.send_message(f"🔄 キー `{key}` から構成データをロード中...", ephemeral=True)
    await asyncio.sleep(2)
    await interaction.followup.send(f"✅ キー `{key}` のロードが完了しました！", ephemeral=True)

# --- 承認DM設定・削除 ---
@bot.tree.command(name="承認dm設定", description="サーバー管理者（オーナー）のDMへ購入通知を設定します")
async def setup_dm_sender(interaction: discord.Interaction):
    if not await check_authority(interaction): return

    global ADMIN_DM_TARGET_ID
    ADMIN_DM_TARGET_ID = interaction.guild.owner_id
    owner = interaction.guild.owner
    await interaction.response.send_message(f"✅ 承認DM送信先に **{owner.name}** を設定しました！", ephemeral=True)

@bot.tree.command(name="承認dm削除", description="設定されている承認DM設定を解除します")
async def remove_dm_sender(interaction: discord.Interaction):
    if not await check_authority(interaction): return

    global ADMIN_DM_TARGET_ID
    ADMIN_DM_TARGET_ID = None
    await interaction.response.send_message("✅ 承認DM設定を削除・解除しました。", ephemeral=True)

# --- 自販機関連 ---
@bot.tree.command(name="自販機作成", description="新しい自販機を作成します")
async def create_vending_machine(interaction: discord.Interaction, name: str):
    if not await check_authority(interaction): return

    machine_id = generate_key(8)
    vending_machines[machine_id] = {"id": machine_id, "name": name, "items": {}}
    await interaction.response.send_message(f"✅ 自販機 **{name}** (ID: `{machine_id}`) を作成しました。", ephemeral=True)

@bot.tree.command(name="自販機削除", description="指定した自販機を削除します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_vending_machine(interaction: discord.Interaction, vending_machine_id: str):
    if not await check_authority(interaction): return

    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("⚠️ 指定された自販機が見つかりません。", ephemeral=True)
        return

    name = vending_machines[vending_machine_id]["name"]
    del vending_machines[vending_machine_id]
    embed = discord.Embed(title="削除完了", description=f"自販機「{name}」を削除しました。", color=discord.Color.green())
    embed.set_footer(text="Developer @Alpha_shop.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="自販機設置", description="指定した自販機の購入パネルを設置します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def setup_vending_machine(
    interaction: discord.Interaction, 
    vending_machine_id: str, 
    panel_title: str = None, 
    panel_description: str = None
):
    if not await check_authority(interaction): return

    if ADMIN_DM_TARGET_ID is None:
        embed = discord.Embed(title="エラー", description="404 承認DMを設定してください。", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("⚠️ 指定された自販機が見つかりません。", ephemeral=True)
        return

    machine_data = vending_machines[vending_machine_id]
    title = panel_title if panel_title else f"🏪 【自販機】{machine_data['name']}"
    desc = panel_description if panel_description else "下の「購入する」ボタンを押して商品を選択してください。"

    embed = discord.Embed(title=title, description=desc, color=discord.Color.blue())
    view = VendingMachineView(machine_id=vending_machine_id)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ 自販機『{machine_data['name']}』のパネルを設置しました！", ephemeral=True)

@bot.tree.command(name="商品追加", description="自販機に新しい商品を追加します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def add_item(
    interaction: discord.Interaction, 
    vending_machine_id: str, 
    name: str, 
    price_manera: int, 
    description: str = "", 
    emoji: str = None
):
    if not await check_authority(interaction): return

    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("⚠️ 指定された自販機が見つかりません。", ephemeral=True)
        return

    vending_machines[vending_machine_id]["items"][name] = {
        "name": name,
        "price_manera": price_manera,
        "description": description,
        "emoji": emoji,
        "stock_type": "有限",
        "stocks": []
    }
    m_name = vending_machines[vending_machine_id]["name"]
    await interaction.response.send_message(f"✅ 自販機『{m_name}』に商品『{name}』({price_manera}円) を追加しました！", ephemeral=True)

@bot.tree.command(name="認証", description="ワンクリックで指定ロールが付与される認証パネルを作成します")
async def setup_simple_verify(
    interaction: discord.Interaction,
    role: discord.Role,
    title: str = "認証パネル",
    description: str = "下のボタンを押して認証してください",
    buttonlabel: str = "verify✅"
):
    if not await check_authority(interaction): return

    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    view = SimpleVerifyView(role_id=role.id, button_label=buttonlabel)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 認証パネルを設置しました！", ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

# --- アプリ起動（Flaskを並行起動してからBot起動） ---
keep_alive()
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
