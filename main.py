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

# グローバルデータ
ADMIN_DM_TARGET_ID = None
AUTHORIZED_USER_IDS = set()
vending_machines = {}
coupons = {}
backups = {}

def generate_key(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def check_authority(interaction: discord.Interaction) -> bool:
    app_info = await bot.application_info()
    owner_id = app_info.owner.id
    if interaction.user.id == owner_id or interaction.user.id in AUTHORIZED_USER_IDS or interaction.user.id == interaction.guild.owner_id:
        return True
    await interaction.response.send_message("❌ 権利がないため実行できませんでした。", ephemeral=True)
    return False

async def vending_machine_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    choices = []
    for m_id, data in vending_machines.items():
        name = data["name"]
        if current.lower() in name.lower():
            choices.append(app_commands.Choice(name=name, value=m_id))
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

# ================= 各種コマンド =================

@bot.tree.command(name="承認dm設定", description="サーバー管理者（オーナー）のDMへ購入通知を設定します")
async def setup_dm_sender(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    global ADMIN_DM_TARGET_ID
    ADMIN_DM_TARGET_ID = interaction.guild.owner_id
    await interaction.response.send_message(f"✅ 承認DM送信先に **{interaction.guild.owner.name}** を設定しました！", ephemeral=True)

@bot.tree.command(name="承認dm削除", description="設定されている承認DM設定を解除します")
async def remove_dm_sender(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    global ADMIN_DM_TARGET_ID
    ADMIN_DM_TARGET_ID = None
    await interaction.response.send_message("✅ 承認DM設定を削除・解除しました。", ephemeral=True)

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
    await interaction.response.send_message(f"✅ 自販機「{name}」を削除しました。", ephemeral=True)

@bot.tree.command(name="自販機設置", description="指定した自販機の購入パネルを設置します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def setup_vending_machine(interaction: discord.Interaction, vending_machine_id: str, panel_title: str = None, panel_description: str = None):
    if not await check_authority(interaction): return
    if ADMIN_DM_TARGET_ID is None:
        await interaction.response.send_message("❌ エラー: 承認DMを設定してください。", ephemeral=True)
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
async def add_item(interaction: discord.Interaction, vending_machine_id: str, name: str, price_manera: int, description: str = "", emoji: str = None):
    if not await check_authority(interaction): return
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("⚠️ 指定された自販機が見つかりません。", ephemeral=True)
        return
    vending_machines[vending_machine_id]["items"][name] = {
        "name": name, "price_manera": price_manera, "description": description, "emoji": emoji, "stock_type": "有限", "stocks": []
    }
    await interaction.response.send_message(f"✅ 商品『{name}』({price_manera}円) を追加しました！", ephemeral=True)

@bot.tree.command(name="認証", description="ワンクリックで指定ロールが付与される認証パネルを作成します")
async def setup_simple_verify(interaction: discord.Interaction, role: discord.Role, title: str = "認証パネル", description: str = "下のボタンを押して認証してください", buttonlabel: str = "verify✅"):
    if not await check_authority(interaction): return
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    view = SimpleVerifyView(role_id=role.id, button_label=buttonlabel)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 認証パネルを設置しました！", ephemeral=True)

# ================= 全体バックアップ ＆ 全削除・復元ロード =================

@bot.tree.command(name="backup", description="サーバー全体（ロール・チャンネル・メッセージ・スレッド等）をバックアップします")
async def create_backup(interaction: discord.Interaction):
    if not await check_authority(interaction): return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    backup_key = generate_key(8)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

    roles_data = [
        {"name": r.name, "color": r.color.value, "permissions": r.permissions.value, "hoist": r.hoist, "mentionable": r.mentionable}
        for r in reversed(guild.roles) if not r.is_default() and not r.managed
    ]

    categories_data = []
    stats = {"roles": len(roles_data), "categories": len(guild.categories), "text": 0, "voice": 0, "forum": 0, "threads": 0, "messages": 0}

    async def fetch_channel_messages(channel):
        msgs = []
        try:
            async for m in channel.history(limit=50, oldest_first=True):
                if m.type == discord.MessageType.default:
                    msgs.append({"author": m.author.display_name, "content": m.content, "attachments": [a.url for a in m.attachments]})
        except Exception: pass
        return msgs

    for category in guild.categories:
        cat_dict = {"name": category.name, "channels": []}
        for ch in category.channels:
            ch_data = {"name": ch.name, "type": str(ch.type), "messages": [], "threads": []}
            if isinstance(ch, discord.TextChannel):
                stats["text"] += 1
                ch_data["messages"] = await fetch_channel_messages(ch)
                stats["messages"] += len(ch_data["messages"])
                for th in ch.threads:
                    stats["threads"] += 1
                    th_msgs = await fetch_channel_messages(th)
                    stats["messages"] += len(th_msgs)
                    ch_data["threads"].append({"name": th.name, "messages": th_msgs})
            elif isinstance(ch, discord.VoiceChannel):
                stats["voice"] += 1
            elif isinstance(ch, discord.ForumChannel):
                stats["forum"] += 1
            cat_dict["channels"].append(ch_data)
        categories_data.append(cat_dict)

    backups[backup_key] = {"guild_id": guild.id, "created_at": now_str, "roles": roles_data, "categories": categories_data, "stats": stats}

    user_data_path = f"data/users/{interaction.user.id}/{guild.id}/{now_str}"
    blob_path = f"data/users/{interaction.user.id}/_blobs"

    embed = discord.Embed(title="完了", description="✅ バックアップ完了", color=discord.Color.green())
    embed.add_field(name="• ID", value=f"`{backup_key}`", inline=False)
    embed.add_field(name="• 保存先", value=f"`{user_data_path}`", inline=False)
    embed.add_field(name="• 構成内訳", value=(
        f"・ ロール: {stats['roles']} / カテゴリ: {stats['categories']}\n"
        f"・ テキスト: {stats['text']} / ボイス: {stats['voice']} / フォーラム: {stats['forum']} / スレッド: {stats['threads']}\n"
        f"・ メッセージ: {stats['messages']}"
    ), inline=False)
    embed.add_field(name="• 自動削除", value="30日後", inline=False)
    embed.add_field(name="• ブロブ", value=f"`{blob_path}`", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="ロード", description="サーバーを全削除し、バックアップデータを復元します")
async def load_backup(interaction: discord.Interaction, key: str):
    if not await check_authority(interaction): return
    if key not in backups:
        await interaction.response.send_message("❌ 指定されたIDのバックアップが見つかりません。", ephemeral=True)
        return

    data = backups[key]
    stats = data["stats"]
    guild = interaction.guild
    user = interaction.user

    await interaction.response.send_message("⚠️ サーバーの全削除および復元を開始します。進捗および完了通知はDMに送信されます。", ephemeral=True)
    try: await user.send("🔄 **復元処理を開始しました。完了までそのままお待ちください...**")
    except discord.Forbidden: pass

    # 1. 全削除
    for ch in guild.channels:
        try: await ch.delete()
        except Exception: pass
    for r in guild.roles:
        if not r.is_default() and not r.managed and r < guild.me.top_role:
            try: await r.delete()
            except Exception: pass

    # 2. 全復元
    res = {"role_success": 0, "role_fail": 0, "cat_success": 0, "cat_fail": 0, "text_success": 0, "text_fail": 0, "voice_success": 0, "voice_fail": 0, "forum_success": 0, "forum_fail": 0, "thread_success": 0, "thread_fail": 0, "msg_success": 0, "msg_fail": 0}

    for r_info in data["roles"]:
        try:
            await guild.create_role(name=r_info["name"], color=discord.Color(r_info["color"]), permissions=discord.Permissions(r_info["permissions"]), hoist=r_info["hoist"], mentionable=r_info["mentionable"])
            res["role_success"] += 1
        except Exception: res["role_fail"] += 1

    for cat_info in data["categories"]:
        try:
            new_cat = await guild.create_category(name=cat_info["name"])
            res["cat_success"] += 1
        except Exception:
            res["cat_fail"] += 1
            new_cat = None

        for ch_info in cat_info["channels"]:
            ch_type = ch_info["type"]
            if "text" in ch_type:
                try:
                    created_ch = await guild.create_text_channel(name=ch_info["name"], category=new_cat)
                    res["text_success"] += 1
                except Exception:
                    res["text_fail"] += 1
                    created_ch = None

                if created_ch:
                    for m in ch_info.get("messages", []):
                        try:
                            content = f"**{m['author']}**: {m['content']}"
                            if m["attachments"]: content += "\n" + "\n".join(m["attachments"])
                            await created_ch.send(content)
                            res["msg_success"] += 1
                        except Exception: res["msg_fail"] += 1

                    for th_info in ch_info.get("threads", []):
                        try:
                            new_th = await created_ch.create_thread(name=th_info["name"], type=discord.ChannelType.public_thread)
                            res["thread_success"] += 1
                            for tm in th_info.get("messages", []):
                                try:
                                    t_content = f"**{tm['author']}**: {tm['content']}"
                                    if tm["attachments"]: t_content += "\n" + "\n".join(tm["attachments"])
                                    await new_th.send(t_content)
                                    res["msg_success"] += 1
                                except Exception: res["msg_fail"] += 1
                        except Exception: res["thread_fail"] += 1

            elif "voice" in ch_type:
                try:
                    await guild.create_voice_channel(name=ch_info["name"], category=new_cat)
                    res["voice_success"] += 1
                except Exception: res["voice_fail"] += 1
            elif "forum" in ch_type:
                try:
                    await guild.create_forum(name=ch_info["name"], category=new_cat)
                    res["forum_success"] += 1
                except Exception: res["forum_fail"] += 1

    # 3. 画像通りのDM結果通知
    embed = discord.Embed(title="完了", description="✅ **復元完了（既存チャンネル/カテゴリ/ロールは削除済み）**", color=discord.Color.green())
    embed.add_field(name="• ID", value=f"`{key}`", inline=False)
    embed.add_field(name="• ロール", value=f"作成 {res['role_success']} / 失敗 {res['role_fail']} / 期待 {stats['roles']}", inline=False)
    embed.add_field(name="• カテゴリ", value=f"作成 {res['cat_success']} / 失敗 {res['cat_fail']} / 期待 {stats['categories']}", inline=False)
    embed.add_field(name="• テキスト", value=f"作成 {res['text_success']} / 失敗 {res['text_fail']} / 期待 {stats['text']}", inline=False)
    embed.add_field(name="• ボイス", value=f"作成 {res['voice_success']} / 失敗 {res['voice_fail']} / 期待 {stats['voice']}", inline=False)
    embed.add_field(name="• フォーラム", value=f"作成 {res['forum_success']} / 失敗 {res['forum_fail']} / 期待 {stats['forum']}", inline=False)
    embed.add_field(name="• スレッド", value=f"作成 {res['thread_success']} / 失敗 {res['thread_fail']} / 期待 {stats['threads']}", inline=False)
    embed.add_field(name="• メッセージ", value=f"合計 {res['msg_success']} / 送信失敗 {res['msg_fail']}", inline=False)

    try: await user.send(embed=embed)
    except Exception as e: print(f"DM送信エラー: {e}")

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

keep_alive()
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
