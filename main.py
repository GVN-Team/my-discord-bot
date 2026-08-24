from flask import Flask, request, render_template_string
from threading import Thread
import os
import uuid
import secrets
import discord
from discord.ext import commands
from discord import app_commands

# --- ダミーWebサーバー ＆ Web認証用サーバー ---
app = Flask('')

verification_tokens = {}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>サーバー参加認証</title>
    <style>
        body { font-family: sans-serif; background-color: #0f172a; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background-color: #1e293b; padding: 2rem; border-radius: 12px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5); text-align: center; max-width: 400px; width: 90%; }
        h2 { margin-bottom: 1rem; color: #38bdf8; }
        p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
        button { background-color: #2563eb; color: white; border: none; padding: 12px 24px; font-size: 1rem; border-radius: 8px; cursor: pointer; font-weight: bold; width: 100%; margin-top: 1.5rem; transition: background-color 0.2s; }
        button:hover { background-color: #1d4ed8; }
        .success { color: #4ade80; font-weight: bold; font-size: 1.2rem; }
        .error { color: #f87171; font-weight: bold; }
    </style>
</head>
<body>
    <div class="card">
        {% if success %}
            <div class="success">✅ 認証が完了しました！</div>
            <p>Discordに戻ってチャンネルをご確認ください。</p>
        {% elif error %}
            <div class="error">❌ {{ error }}</div>
        {% else %}
            <h2>🤖 サーバー参加認証</h2>
            <p>下のボタンを押して認証を完了させてください。</p>
            <form method="POST">
                <button type="submit">私はロボットではありません（認証完了）</button>
            </form>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return "Vender Bot is alive!"

@app.route('/verify/<token>', methods=['GET', 'POST'])
def verify_page(token):
    if token not in verification_tokens:
        return render_template_string(HTML_TEMPLATE, error="無効または期限切れの認証リンクです。再度Discordから発行してください。")

    if request.method == 'POST':
        data = verification_tokens.pop(token)
        bot.loop.create_task(assign_role(data["guild_id"], data["user_id"], data["role_id"]))
        return render_template_string(HTML_TEMPLATE, success=True)

    return render_template_string(HTML_TEMPLATE, success=False)

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
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

ADMIN_CHANNEL_ID = 123456789012345678  # ※ご自身の管理者チャンネルIDに変更してください
APPROVED_ROLE_ID = None

# --- 許可されたサーバーIDリスト（初期状態） ---
# 最初にBotを入れておきたいサーバーIDをここに書いておくことも可能です
allowed_guild_ids = set()

vending_machines = {}

# 役割付与処理
async def assign_role(guild_id: int, user_id: int, role_id: int):
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    member = guild.get_member(user_id)
    role = guild.get_role(role_id)
    if member and role:
        try:
            await member.add_roles(role)
        except Exception as e:
            print(f"ロール付与エラー: {e}")

# オートコンプリート
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

# --- 承認・拒否ボタン ---
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

# --- PayPay入力 ---
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

# --- 商品選択 ---
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

class VendingMachineView(discord.ui.View):
    def __init__(self, machine_id: str):
        super().__init__(timeout=None)
        self.machine_id = machine_id

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.primary, emoji="🛒")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ItemSelectView(self.machine_id)
        await interaction.response.send_message("購入する商品を選んでください：", view=view, ephemeral=True)

# --- Web認証ボタン ＆ パネル ---
class VerifyView(discord.ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id

    @discord.ui.button(label="認証する", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_start_btn")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        token = secrets.token_urlsafe(16)
        verification_tokens[token] = {
            "user_id": interaction.user.id,
            "guild_id": interaction.guild_id,
            "role_id": self.role_id
        }

        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if not render_url:
            render_url = f"https://{os.getenv('RENDER_SERVICE_NAME', 'vending-bot')}.onrender.com"

        verify_url = f"{render_url}/verify/{token}"

        await interaction.response.send_message(
            f"🔒 下のリンクを開いて認証を完了してください（1回のみ有効）：\n{verify_url}", 
            ephemeral=True
        )

# --- サーバー許可指定コマンド ---
@bot.tree.command(name="サーバー許可", description="[Bot作成者専用] Botの使用を許可するサーバーを指定・管理します")
@app_commands.choices(操作=[
    app_commands.Choice(name="追加", value="add"),
    app_commands.Choice(name="削除", value="remove"),
    app_commands.Choice(name="現在のサーバーを追加", value="add_current"),
    app_commands.Choice(name="一覧表示", value="list")
])
async def manage_allowed_guilds(
    interaction: discord.Interaction, 
    操作: str, 
    サーバーid: str = None
):
    # 作成者チェック
    app_info = await bot.application_info()
    if interaction.user.id != app_info.owner.id:
        await interaction.response.send_message("❌ このコマンドはBotの作成者のみ実行できます。", ephemeral=True)
        return

    if 操作 == "add_current":
        target_id = interaction.guild_id
        allowed_guild_ids.add(target_id)
        await interaction.response.send_message(f"✅ 現在のサーバー (`{target_id}`) を許可リストに追加しました！", ephemeral=True)

    elif 操作 == "add":
        if not サーバーid or not サーバーid.isdigit():
            await interaction.response.send_message("⚠️ 正しいサーバーID（数字）を入力してください。", ephemeral=True)
            return
        target_id = int(サーバーid)
        allowed_guild_ids.add(target_id)
        await interaction.response.send_message(f"✅ サーバーID `{target_id}` を許可リストに追加しました！", ephemeral=True)

    elif 操作 == "remove":
        if not サーバーid or not サーバーid.isdigit():
            await interaction.response.send_message("⚠️ 正しいサーバーID（数字）を入力してください。", ephemeral=True)
            return
        target_id = int(サーバーid)
        if target_id in allowed_guild_ids:
            allowed_guild_ids.remove(target_id)
            # もし現在そのサーバーに参加中なら即退出させる
            guild = bot.get_guild(target_id)
            if guild:
                await guild.leave()
            await interaction.response.send_message(f"❌ サーバーID `{target_id}` を許可リストから削除し、退出処理を行いました。", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ そのサーバーIDはリストに存在しません。", ephemeral=True)

    elif 操作 == "list":
        if not allowed_guild_ids:
            await interaction.response.send_message("📋 許可されているサーバーはありません。（制限なし、または未登録）", ephemeral=True)
        else:
            guild_list_str = "\n".join([f"・`{gid}` ({bot.get_guild(gid).name if bot.get_guild(gid) else '未参加'})" for gid in allowed_guild_ids])
            await interaction.response.send_message(f"📋 **許可されているサーバー一覧:**\n{guild_list_str}", ephemeral=True)

# --- その他のコマンド ---

@bot.tree.command(name="認証パネル設置", description="Webパネル認証のメッセージを設置します")
@app_commands.describe(
    role="認証成功時に付与するロール",
    title="パネルのタイトル",
    description="パネルの説明文"
)
async def setup_verify_panel(
    interaction: discord.Interaction, 
    role: discord.Role, 
    title: str = "🔒 サーバー参加認証", 
    description: str = "下の「認証する」ボタンを押してWebページで認証を完了させてください。"
):
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.green()
    )
    view = VerifyView(role_id=role.id)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 認証パネルを設置しました！", ephemeral=True)

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

@bot.tree.command(name="自販機設置", description="指定した自販機の購入パネルを設置します")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
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

class StockModal(discord.ui.Modal):
    def __init__(self, machine_id: str, item_name: str, stock_type: str):
        super().__init__(title=f"<{stock_type}>在庫内容")
        self.machine_id = machine_id
        self.item_name = item_name
        self.stock_type = stock_type
        self.stock_input = discord.ui.TextInput(
            label="在庫データ（商品内容）",
            style=discord.TextStyle.paragraph,
            placeholder="ここに商品コードやテキストを入力",
            required=True,
            max_length=4000
        )
        self.add_item(self.stock_input)

    async def on_submit(self, interaction: discord.Interaction):
        item_data = vending_machines[self.machine_id]["items"][self.item_name]
        item_data["stock_type"] = self.stock_type
        item_data["stocks"].append(self.stock_input.value)
        await interaction.response.send_message(f"✅ **{self.item_name}** に在庫を追加しました！", ephemeral=True)

class StockItemSelect(discord.ui.Select):
    def __init__(self, machine_id: str, stock_type: str):
        self.machine_id = machine_id
        self.stock_type = stock_type
        options = [discord.SelectOption(label=item_name, value=item_name) for item_name in vending_machines.get(machine_id, {}).get("items", {}).keys()]
        super().__init__(placeholder="商品を選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(StockModal(self.machine_id, self.values[0], self.stock_type))

class StockItemSelectView(discord.ui.View):
    def __init__(self, machine_id: str, stock_type: str):
        super().__init__(timeout=None)
        self.add_item(StockItemSelect(machine_id, stock_type))

@bot.tree.command(name="認証dm送信者", description="承認時の自動DM送信者の権限・設定を行います")
async def setup_dm_sender(interaction: discord.Interaction, role: discord.Role):
    global APPROVED_ROLE_ID
    APPROVED_ROLE_ID = role.id
    await interaction.response.send_message(f"✅ DM送信・承認権限ロールを {role.mention} に設定しました！", ephemeral=True)

# --- 未許可サーバーからの自動脱退処理 ---
@bot.event
async def on_guild_join(guild: discord.Guild):
    # リストにIDが登録されている場合のみチェックを実行（空の場合は誰でも追加可能）
    if allowed_guild_ids and guild.id not in allowed_guild_ids:
        print(f"未許可サーバー ({guild.name} / ID: {guild.id}) に追加されたため脱退します。")
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                await channel.send("⚠️ このBotは許可された特定のサーバー専用です。自動脱退します。")
                break
        await guild.leave()

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")
    
    # 起動時にも未許可サーバーをチェックして退出
    if allowed_guild_ids:
        for guild in bot.guilds:
            if guild.id not in allowed_guild_ids:
                print(f"起動時チェック: 未許可サーバー ({guild.name}) から脱退します。")
                await guild.leave()

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
