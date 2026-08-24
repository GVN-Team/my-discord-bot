from flask import Flask
from threading import Thread
import os
import uuid
import discord
from discord.ext import commands
from discord import app_commands

# --- ダミーWebサーバー（Render無料枠維持用） ---
app = Flask('')

@app.route('/')
def home():
    return "Vender Bot is alive!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# --- Bot初期化 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

ADMIN_CHANNEL_ID = 123456789012345678  # ※ご自身の管理者チャンネルIDに変更してください
APPROVED_ROLE_ID = None                # 認証DM送信者のロールIDを保持

# メモリ内データベース
# vending_machines = { "自販機ID": { "name": "自販機名", "items": { "商品名": {...} } } }
vending_machines = {}

# オートコンプリート（登録されている自販機を検知して補完）
async def vending_machine_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    choices = []
    for machine_id, data in vending_machines.items():
        name = data["name"]
        # 入力文字列でフィルタリング
        if current.lower() in name.lower() or current.lower() in machine_id.lower():
            choices.append(app_commands.Choice(name=f"{name} ({machine_id[:8]}...)", value=machine_id))
    return choices[:25]  # Discordの仕様上最大25件

# --- 承認・拒否ボタンの処理 ---
class ApproveView(discord.ui.View):
    def __init__(self, user: discord.User, item_data: dict, paypay_url: str):
        super().__init__(timeout=None)
        self.user = user
        self.item_data = item_data
        self.paypay_url = paypay_url

    @discord.ui.button(label="承認（商品を送信）", style=discord.ButtonStyle.green, custom_id="approve_btn")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            stock_content = "ご購入ありがとうございます！"
            if self.item_data.get("stock_type") == "有限":
                if len(self.item_data.get("stocks", [])) > 0:
                    stock_content = self.item_data["stocks"].pop(0)
                else:
                    await interaction.response.send_message("⚠️ 在庫切れのため承認できません。", ephemeral=True)
                    return
            elif self.item_data.get("stock_type") == "無限":
                if len(self.item_data.get("stocks", [])) > 0:
                    stock_content = self.item_data["stocks"][0]

            await self.user.send(
                f"【購入完了通知】\n"
                f"商品名: **{self.item_data['name']}**\n\n"
                f"【商品内容】\n{stock_content}"
            )
            await interaction.response.send_message(f"✅ {self.user.mention} への商品送信が完了しました！", ephemeral=True)
            self.stop()
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ ユーザーのDMが閉じられているため送信できませんでした。", ephemeral=True)

    @discord.ui.button(label="拒否", style=discord.ButtonStyle.red, custom_id="deny_btn")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.user.send(f"【購入キャンセル】\n`{self.item_data['name']}` の決済が確認できなかったため、購入申請が拒否されました。")
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"❌ {self.user.mention} の申請を拒否しました。", ephemeral=True)
        self.stop()

# --- PayPay送金入力フォーム ---
class PayPayModal(discord.ui.Modal, title="購入手続き"):
    paypay_url = discord.ui.TextInput(
        label="PayPay送金リンク",
        placeholder="https://paypay.me/...",
        required=True,
        max_length=100
    )

    def __init__(self, item_data: dict):
        super().__init__()
        self.item_data = item_data

    async def on_submit(self, interaction: discord.Interaction):
        admin_channel = interaction.client.get_channel(ADMIN_CHANNEL_ID)
        if not admin_channel:
            await interaction.response.send_message("管理チャンネルが見つかりません。", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ 購入申請を受け付けました！管理者の確認後にDMへ商品が届きます。\n"
            f"商品名: {self.item_data['name']}", 
            ephemeral=True
        )

        embed = discord.Embed(title="💳 新しい購入申請", color=discord.Color.blue())
        embed.add_field(name="購入者", value=interaction.user.mention, inline=False)
        embed.add_field(name="商品名", value=self.item_data["name"], inline=True)
        embed.add_field(name="価格(マネー)", value=f"{self.item_data['price_money']}円", inline=True)
        embed.add_field(name="価格(マネーライト)", value=f"{self.item_data['price_manera']}円", inline=True)
        embed.add_field(name="PayPayリンク", value=self.paypay_url.value, inline=False)

        view = ApproveView(user=interaction.user, item_data=self.item_data, paypay_url=self.paypay_url.value)
        await admin_channel.send(embed=embed, view=view)

# --- 商品選択セレクトメニュー ---
class ItemSelect(discord.ui.Select):
    def __init__(self, machine_id: str):
        self.machine_id = machine_id
        options = []
        items = vending_machines.get(machine_id, {}).get("items", {})

        for item_name, data in items.items():
            desc = f"マネー: {data['price_money']}円 | マネーライト: {data['price_manera']}円"
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
        await interaction.response.send_modal(PayPayModal(item_data))

class ItemSelectView(discord.ui.View):
    def __init__(self, machine_id: str):
        super().__init__(timeout=None)
        self.add_item(ItemSelect(machine_id))

# --- 自販機パネル（購入ボタン） ---
class VendingMachineView(discord.ui.View):
    def __init__(self, machine_id: str):
        super().__init__(timeout=None)
        self.machine_id = machine_id

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.primary, emoji="🛒")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ItemSelectView(self.machine_id)
        await interaction.response.send_message("購入する商品を選んでください：", view=view, ephemeral=True)

# --- 在庫追加用モーダル ---
class StockModal(discord.ui.Modal):
    def __init__(self, machine_id: str, item_name: str, stock_type: str):
        super().__init__(title=f"<{stock_type}>在庫内容")
        self.machine_id = machine_id
        self.item_name = item_name
        self.stock_type = stock_type

        self.stock_input = discord.ui.TextInput(
            label="在庫データ（商品内容）",
            style=discord.TextStyle.paragraph,
            placeholder="ここに商品コードやテキストを入力（4000文字以内）",
            required=True,
            max_length=4000
        )
        self.add_item(self.stock_input)

    async def on_submit(self, interaction: discord.Interaction):
        item_data = vending_machines[self.machine_id]["items"][self.item_name]
        item_data["stock_type"] = self.stock_type
        item_data["stocks"].append(self.stock_input.value)

        await interaction.response.send_message(
            f"✅ **{self.item_name}** に在庫を追加しました！（タイプ: {self.stock_type}）", 
            ephemeral=True
        )

# --- 在庫追加用の商品選択ドロップダウン ---
class StockItemSelect(discord.ui.Select):
    def __init__(self, machine_id: str, stock_type: str):
        self.machine_id = machine_id
        self.stock_type = stock_type

        options = []
        items = vending_machines.get(machine_id, {}).get("items", {})
        for item_name in items.keys():
            options.append(discord.SelectOption(label=item_name, value=item_name))

        super().__init__(placeholder="在庫を追加する商品を選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_item = self.values[0]
        await interaction.response.send_modal(StockModal(self.machine_id, selected_item, self.stock_type))

class StockItemSelectView(discord.ui.View):
    def __init__(self, machine_id: str, stock_type: str):
        super().__init__(timeout=None)
        self.add_item(StockItemSelect(machine_id, stock_type))

# --- 各種スラッシュコマンド ---

@bot.tree.command(name="自販機作成", description="新しい自販機を作成し、固有のIDを発行します")
async def create_vending_machine(interaction: discord.Interaction, name: str):
    machine_id = str(uuid.uuid4())
    vending_machines[machine_id] = {
        "name": name,
        "items": {}
    }

    dm_setting_msg = ""
    if APPROVED_ROLE_ID is None:
        dm_setting_msg = "\n**承認DMを設定してください。**"

    message = (
        f"自販機 **{name}** を作成しました。\n"
        f"**自販機ID:** `{machine_id}`"
        f"{dm_setting_msg}"
    )

    await interaction.response.send_message(message, ephemeral=True)

@bot.tree.command(name="自販機設置", description="指定した自販機の購入パネルをチャンネルに設置します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
@app_commands.describe(
    vending_machine_id="設置する自販機を選択",
    panel_title="パネルタイトル",
    panel_description="パネル説明文"
)
async def setup_vending_machine(
    interaction: discord.Interaction, 
    vending_machine_id: str, 
    panel_title: str = None, 
    panel_description: str = None
):
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("指定された自販機IDが見つかりません。", ephemeral=True)
        return

    machine_data = vending_machines[vending_machine_id]
    title = panel_title if panel_title else f"🏪 【自販機】{machine_data['name']}"
    desc = panel_description if panel_description else "下の「購入する」ボタンを押して商品を選択してください。"

    embed = discord.Embed(
        title=title,
        description=desc,
        color=discord.Color.blue()
    )
    view = VendingMachineView(machine_id=vending_machine_id)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(f"自販機『{machine_data['name']}』パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="商品追加", description="自販機に新しい商品を追加します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
@app_commands.describe(
    vending_machine_id="対象の自販機を選択",
    name="商品名",
    price_money="マネー価格",
    price_manera="マネーライト価格",
    description="商品の説明（任意）",
    emoji="絵文字（任意）"
)
async def add_item(
    interaction: discord.Interaction, 
    vending_machine_id: str, 
    name: str, 
    price_money: int, 
    price_manera: int, 
    description: str = "", 
    emoji: str = None
):
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("指定された自販機が見つかりません。", ephemeral=True)
        return

    vending_machines[vending_machine_id]["items"][name] = {
        "name": name,
        "price_money": price_money,
        "price_manera": price_manera,
        "description": description,
        "emoji": emoji,
        "stock_type": "有限",
        "stocks": []
    }
    machine_name = vending_machines[vending_machine_id]["name"]
    await interaction.response.send_message(f"✅ 自販機『{machine_name}』に商品『{name}』を追加しました！", ephemeral=True)

@bot.tree.command(name="在庫追加", description="自販機の商品に在庫を登録します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
@app_commands.choices(stock_type=[
    app_commands.Choice(name="有限", value="有限"),
    app_commands.Choice(name="無限", value="無限")
])
async def add_stock(interaction: discord.Interaction, vending_machine_id: str, stock_type: str):
    if vending_machine_id not in vending_machines or not vending_machines[vending_machine_id]["items"]:
        await interaction.response.send_message("指定された自販機または商品が存在しません。", ephemeral=True)
        return

    view = StockItemSelectView(vending_machine_id, stock_type)
    await interaction.response.send_message("在庫を登録する商品を選択してください：", view=view, ephemeral=True)

@bot.tree.command(name="認証dm送信者", description="承認時の自動DM送信者の権限・設定を行います")
async def setup_dm_sender(interaction: discord.Interaction, role: discord.Role):
    global APPROVED_ROLE_ID
    APPROVED_ROLE_ID = role.id
    await interaction.response.send_message(f"✅ DM送信・承認権限ロールを {role.mention} に設定しました！", ephemeral=True)

# --- Bot起動イベント ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
