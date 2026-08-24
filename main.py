import os
import discord
from discord.ext import commands
from discord import app_commands

# --- Botの初期化設定 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 管理者用チャンネルのID（※ご自身のチャンネルID数字に変更してください）
ADMIN_CHANNEL_ID = 123456789012345678  

# --- 承認・拒否ボタンの処理 ---
class ApproveView(discord.ui.View):
    def __init__(self, user: discord.User, item_name: str, paypay_url: str):
        super().__init__(timeout=None)
        self.user = user
        self.item_name = item_name
        self.paypay_url = paypay_url

    @discord.ui.button(label="承認（商品を送信）", style=discord.ButtonStyle.green, custom_id="approve_btn")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.user.send(
                f"【購入完了通知】\n"
                f"ご購入ありがとうございます！\n"
                f"商品名: **{self.item_name}**\n"
                f"商品コード: `EXAMPLE-1234-ABCD`"
            )
            await interaction.response.send_message(f"✅ {self.user.mention} への商品送信が完了しました！", ephemeral=True)
            self.stop()
        except discord.Forbidden:
            await interaction.response.send_message("⚠️ ユーザーのDMが閉じられているため送信できませんでした。", ephemeral=True)

    @discord.ui.button(label="拒否", style=discord.ButtonStyle.red, custom_id="deny_btn")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.user.send(f"【購入キャンセル】\n`{self.item_name}` の決済が確認できなかったため、購入申請が拒否されました。")
        except discord.Forbidden:
            pass
        await interaction.response.send_message(f"❌ {self.user.mention} の申請を拒否しました。", ephemeral=True)
        self.stop()

# --- 自販機パネルに表示される「購入」ボタン ---
class VendingMachineView(discord.ui.View):
    def __init__(self, item_name: str, price: int):
        super().__init__(timeout=None)
        self.item_name = item_name
        self.price = price

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.primary, emoji="🛒")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PayPayModal(self.item_name, self.price))

# --- PayPayリンク入力用ポップアップ画面 ---
class PayPayModal(discord.ui.Modal, title="購入手続き"):
    paypay_url = discord.ui.TextInput(
        label="PayPay送金リンク",
        placeholder="https://paypay.me/...",
        required=True,
        max_length=100
    )

    def __init__(self, item_name: str, price: int):
        super().__init__()
        self.item_name = item_name
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        admin_channel = interaction.client.get_channel(ADMIN_CHANNEL_ID)
        if not admin_channel:
            await interaction.response.send_message("管理チャンネルが見つかりません。管理者に連絡してください。", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ 購入申請を受け付けました！管理者の確認後にDMへ商品が届きます。\n"
            f"商品名: {self.item_name} / 価格: {self.price}円", 
            ephemeral=True
        )

        embed = discord.Embed(title="💳 新しい購入申請", color=discord.Color.blue())
        embed.add_field(name="購入者", value=interaction.user.mention, inline=False)
        embed.add_field(name="商品名", value=self.item_name, inline=True)
        embed.add_field(name="金額", value=f"{self.price}円", inline=True)
        embed.add_field(name="PayPayリンク", value=self.paypay_url.value, inline=False)

        view = ApproveView(user=interaction.user, item_name=self.item_name, paypay_url=self.paypay_url.value)
        await admin_channel.send(embed=embed, view=view)

# --- スラッシュコマンド（自販機の作成） ---
@bot.tree.command(name="create_vending", description="チャンネルに商品購入用の自販機パネルを作成します")
@app_commands.describe(
    item_name="販売する商品の名前を入力してください（例: VIPロール）",
    price="商品の価格（数字のみ）を入力してください（例: 500）",
    description="パネルに表示する商品の説明テキストを入力してください"
)
async def create_vending(interaction: discord.Interaction, item_name: str, price: int, description: str):
    embed = discord.Embed(
        title=f"🏪 【販売】{item_name}",
        description=f"{description}\n\n**価格:** `{price}円`",
        color=discord.Color.green()
    )
    embed.set_footer(text="下の「購入する」ボタンを押してPayPayリンクを送信してください")
    
    view = VendingMachineView(item_name=item_name, price=price)
    
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("自販機パネルを作成しました！", ephemeral=True)

# --- Bot起動イベント ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")

TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
