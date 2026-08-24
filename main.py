import os
import re
import datetime
import asyncio
import discord
import aiohttp
from discord.ext import commands
from discord import app_commands

# --- Bot初期化 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# グローバル設定
APPROVED_ROLE_ID = None  # 承認DMロールID
AUTHORIZED_USER_IDS = set()  # Bot操作権限ユーザー
vending_machines = {}
coupons = {}

# 権限チェック関数
async def check_authority(interaction: discord.Interaction) -> bool:
    app_info = await bot.application_info()
    owner_id = app_info.owner.id
    
    if interaction.user.id == owner_id or interaction.user.id in AUTHORIZED_USER_IDS:
        return True
    
    await interaction.response.send_message("❌ 権利がないため実行できませんでした。", ephemeral=True)
    return False

# 自販機オートコンプリート
async def vending_machine_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    choices = []
    for machine_id, data in vending_machines.items():
        name = data["name"]
        if current.lower() in name.lower() or current.lower() in machine_id.lower():
            choices.append(app_commands.Choice(name=f"{name} ({machine_id[:8]}...)", value=machine_id))
    return choices[:25]

# --- PayPay リンク自動取得 & 検証処理 ---
async def fetch_paypay_info(paypay_url: str):
    """PayPayリンクから金額と状態を取得する関数"""
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

# --- 購入モーダル ＆ 決済フロー ---
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
        # 3秒タイムアウトを防ぐため最優先で応答
        await interaction.response.defer(ephemeral=True)

        # 1. 個数確認
        try:
            qty = int(self.quantity.value)
            if qty <= 0: raise ValueError
        except ValueError:
            await interaction.followup.send("❌ 購入数は1以上の数字を入力してください。", ephemeral=True)
            return

        # 在庫確認
        stocks = self.item_data.get("stocks", [])
        stock_type = self.item_data.get("stock_type", "有限")
        if stock_type == "有限" and len(stocks) < qty:
            await interaction.followup.send(f"⚠️ 在庫が不足しています。（残り: {len(stocks)}個）", ephemeral=True)
            return

        # 2. 金額計算 & クーポン適用
        unit_price = self.item_data["price_manera"]
        total_price = unit_price * qty

        code = self.coupon_code.value.strip()
        if code in coupons:
            cp = coupons[code]
            if cp["vending_machine_id"] == self.machine_id and cp["count"] > 0:
                total_price = max(0, total_price - cp["discount"])
                cp["count"] -= 1

        # 3. PayPay決済確認
        pay_info = await fetch_paypay_info(self.paypay_url.value)
        if not pay_info or not pay_info["valid"]:
            sent_amount = total_price
            is_pending = False
        else:
            sent_amount = pay_info["amount"]
            is_pending = pay_info["is_pending"]

        # 金額判定
        if sent_amount < total_price:
            await interaction.followup.send(
                f"金額が不足しています。\n"
                f"必要金額: {total_price}円\n"
                f"送金金額: {sent_amount}円",
                ephemeral=True
            )
            return

        # 保留判定と1分待機処理
        if is_pending:
            await interaction.followup.send(
                "PayPay受け取りが保留になったため、必ず1分以内に送金してください。\n"
                "必ず受け取り保留を解除して送金してください。1分後に届きます。",
                ephemeral=True
            )
            await asyncio.sleep(60)
            
            re_info = await fetch_paypay_info(self.paypay_url.value)
            if re_info and re_info["is_pending"]:
                await interaction.followup.send(
                    "PayPay決済の確認に失敗しました。\n"
                    "受け取り保留を解除して送金してください。",
                    ephemeral=True
                )
                return

        # 商品配送処理
        delivery_items = []
        if stock_type == "有限":
            for _ in range(qty):
                delivery_items.append(self.item_data["stocks"].pop(0))
        else:
            delivery_items = [self.item_data["stocks"][0]] * qty

        # DM送信
        try:
            items_str = "\n".join([f"・{item}" for item in delivery_items])
            await interaction.user.send(
                f"ご購入ありがとうございます。\n"
                f"DMにて商品をお送りしました。\n\n"
                f"【購入商品】: {self.item_name} × {qty}\n"
                f"【商品内容】:\n{items_str}"
            )
            await interaction.followup.send(
                "ご購入ありがとうございます。\nDMにて商品をお送りしました。",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send("⚠️ DMを開放してください。商品が送信できませんでした。", ephemeral=True)

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
        view = ItemSelectView(self.machine_id)
        await interaction.response.send_message("購入する商品を選んでください：", view=view, ephemeral=True)

# --- 簡易ボタン認証ビュー ---
class SimpleVerifyView(discord.ui.View):
    def __init__(self, role: discord.Role, button_label: str):
        super().__init__(timeout=None)
        self.role = role
        self.verify_btn.label = button_label

    @discord.ui.button(style=discord.ButtonStyle.success, custom_id="simple_verify_btn")
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.user.add_roles(self.role)
            await interaction.response.send_message(f"✅ 認証が完了し、{self.role.mention} を付与しました！", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Botの権限不足のためロールを付与できませんでした。", ephemeral=True)

# --- 自販機削除確認ビュー ---
class DeleteConfirmView(discord.ui.View):
    def __init__(self, machine_id: str, machine_name: str):
        super().__init__(timeout=60)
        self.machine_id = machine_id
        self.machine_name = machine_name

    @discord.ui.button(label="削除する", style=discord.ButtonStyle.red)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.machine_id in vending_machines:
            del vending_machines[self.machine_id]

        embed = discord.Embed(
            title="削除完了",
            description=f"自販機「{self.machine_name}」を削除しました。",
            color=discord.Color.green()
        )
        embed.set_footer(text="Developer @Alpha_shop.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="キャンセル",
            description="自販機削除をキャンセルしました。",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Developer @Alpha_shop.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ================= コマンド定義 =================

# --- 承認DM設定・削除 ---
@bot.tree.command(name="承認dm設定", description="承認時の自動DM送信・操作権限ロールを設定します")
async def setup_dm_sender(interaction: discord.Interaction, role: discord.Role):
    if not await check_authority(interaction): return

    global APPROVED_ROLE_ID
    APPROVED_ROLE_ID = role.id
    await interaction.response.send_message(f"✅ 承認DM設定を {role.mention} に設定しました！", ephemeral=True)

@bot.tree.command(name="承認dm削除", description="設定されている承認DMロールを削除・解除します")
async def remove_dm_sender(interaction: discord.Interaction):
    if not await check_authority(interaction): return

    global APPROVED_ROLE_ID
    APPROVED_ROLE_ID = None
    await interaction.response.send_message("✅ 承認DM設定を削除・解除しました。", ephemeral=True)


# --- 自販機関連 ---
@bot.tree.command(name="自販機作成", description="新しい自販機を作成します")
async def create_vending_machine(interaction: discord.Interaction, name: str):
    if not await check_authority(interaction): return

    machine_id = str(hash(name))
    vending_machines[machine_id] = {"name": name, "items": {}}

    await interaction.response.send_message(f"自販機 **{name}** を作成しました。\nID: `{machine_id}`", ephemeral=True)

@bot.tree.command(name="自販機削除", description="指定した自販機を削除します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_vending_machine(interaction: discord.Interaction, vending_machine_id: str):
    if not await check_authority(interaction): return

    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("⚠️ 指定された自販機が見つかりません。", ephemeral=True)
        return

    machine_name = vending_machines[vending_machine_id]["name"]
    embed = discord.Embed(
        title="自販機削除確認",
        description=f"本当に自販機「{machine_name}」を削除しますか？\n\n**この操作は取り消せません。**",
        color=discord.Color.red()
    )
    embed.set_footer(text="Developer @Alpha_shop.")
    await interaction.response.send_message(embed=embed, view=DeleteConfirmView(vending_machine_id, machine_name), ephemeral=True)

@bot.tree.command(name="自販機設置", description="指定した自販機の購入パネルを設置します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def setup_vending_machine(
    interaction: discord.Interaction, 
    vending_machine_id: str, 
    panel_title: str = None, 
    panel_description: str = None
):
    if not await check_authority(interaction): return

    if APPROVED_ROLE_ID is None:
        embed = discord.Embed(
            title="エラー",
            description="404 承認DMを設定してください。",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("指定された自販機が見つかりません。", ephemeral=True)
        return

    machine_data = vending_machines[vending_machine_id]
    title = panel_title if panel_title else f"🏪 【自販機】{machine_data['name']}"
    desc = panel_description if panel_description else "下の「購入する」ボタンを押して商品を選択してください。"

    embed = discord.Embed(title=title, description=desc, color=discord.Color.blue())
    view = VendingMachineView(machine_id=vending_machine_id)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"自販機『{machine_data['name']}』パネルを設置しました！", ephemeral=True)

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
        await interaction.response.send_message("指定された自販機が見つかりません。", ephemeral=True)
        return

    vending_machines[vending_machine_id]["items"][name] = {
        "name": name,
        "price_manera": price_manera,
        "description": description,
        "emoji": emoji,
        "stock_type": "有限",
        "stocks": []
    }
    await interaction.response.send_message(f"✅ 商品『{name}』({price_manera}円) を追加しました！", ephemeral=True)


# --- 簡易ボタン認証コマンド ---
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
    view = SimpleVerifyView(role=role, button_label=buttonlabel)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 認証パネルを設置しました！", ephemeral=True)


# --- ロール管理コマンド群 ---
@bot.tree.command(name="ロール追加", description="指定ユーザーにロールを付与します")
async def add_role_to_user(interaction: discord.Interaction, role: discord.Role, user: discord.Member):
    if not await check_authority(interaction): return
    await user.add_roles(role)
    await interaction.response.send_message(f"✅ {user.mention} に {role.mention} を付与しました。", ephemeral=True)

@bot.tree.command(name="ロール削除", description="指定ユーザーからロールを削除します")
async def remove_role_from_user(interaction: discord.Interaction, role: discord.Role, user: discord.Member):
    if not await check_authority(interaction): return
    await user.remove_roles(role)
    await interaction.response.send_message(f"✅ {user.mention} から {role.mention} を削除しました。", ephemeral=True)

@bot.tree.command(name="ロール一覧", description="サーバー内のロール一覧を表示します")
async def list_roles(interaction: discord.Interaction):
    if not await check_authority(interaction): return
    roles = [r.mention for r in interaction.guild.roles if not r.is_default()]
    embed = discord.Embed(title="📜 ロール一覧", description="\n".join(roles) if roles else "ロールが存在しません。", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ロール権限変更", description="ロールの管理者権限フラグを切り替えます")
async def change_role_perm(interaction: discord.Interaction, role: discord.Role, administrator: bool):
    if not await check_authority(interaction): return
    perms = role.permissions
    perms.administrator = administrator
    await role.edit(permissions=perms)
    await interaction.response.send_message(f"✅ {role.mention} の管理者権限を `{administrator}` に変更しました。", ephemeral=True)


# --- ユーザー管理（モデレーション）コマンド群 ---
@bot.tree.command(name="キック", description="指定したユーザーをサーバーからキックします")
async def kick_user(interaction: discord.Interaction, user: discord.Member, reason: str = "理由なし"):
    if not await check_authority(interaction): return
    await user.kick(reason=reason)
    await interaction.response.send_message(f"👞 {user.mention} をキックしました。 (理由: {reason})", ephemeral=True)

@bot.tree.command(name="バン", description="指定したユーザーをサーバーからBANします")
async def ban_user(interaction: discord.Interaction, user: discord.Member, reason: str = "理由なし"):
    if not await check_authority(interaction): return
    await user.ban(reason=reason)
    await interaction.response.send_message(f"🔨 {user.mention} をBANしました。 (理由: {reason})", ephemeral=True)

@bot.tree.command(name="タイムアウト", description="指定ユーザーをタイムアウトします（分数指定）")
async def timeout_user(interaction: discord.Interaction, user: discord.Member, minutes: int, reason: str = "理由なし"):
    if not await check_authority(interaction): return
    duration = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
    await user.timeout(duration, reason=reason)
    await interaction.response.send_message(f"⏰ {user.mention} を {minutes} 分間タイムアウトしました。", ephemeral=True)


# --- ヘルプコマンド ---
@bot.tree.command(name="ヘルプ", description="Botの利用可能なコマンド一覧と使い方を表示します")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Alpha VD Normal - コマンドヘルプ",
        description="利用可能なスラッシュコマンドの一覧です。",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="🏪 自販機管理",
        value="`/自販機作成`, `/自販機削除`, `/自販機設置`, `/商品追加`",
        inline=False
    )
    embed.add_field(
        name="⚙️ 設定・承認",
        value="`/承認DM設定`, `/承認DM削除`, `/認証`",
        inline=False
    )
    embed.add_field(
        name="🛡️ ユーザー・ロール管理",
        value="`/ロール追加`, `/ロール削除`, `/ロール一覧`, `/ロール権限変更`\n`/キック`, `/バン`, `/タイムアウト`",
        inline=False
    )
    embed.set_footer(text="Developer @Alpha_shop.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
