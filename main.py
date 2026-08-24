import os
import discord
from discord import app_commands
from discord.ext import commands

# インテントの設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 簡易的な在庫とポイント管理
user_balances = {}  # ユーザーの所持ポイント
stock_list = [
    "ITEM-CODE-AAAA-1111",
    "ITEM-CODE-BBBB-2222",
    "ITEM-CODE-CCCC-3333"
]

ITEM_PRICE = 100  # 商品の価格（ポイント）


# 購入ボタンの処理
class VendingMachineView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="商品を購入する (100pt)", style=discord.ButtonStyle.green, custom_id="buy_button")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        balance = user_balances.get(user_id, 0)

        # 所持ポイントチェック
        if balance < ITEM_PRICE:
            await interaction.response.send_message(
                f"❌ ポイントが足りません！（所持: {balance}pt / 必要: {ITEM_PRICE}pt）", 
                ephemeral=True
            )
            return

        # 在庫チェック
        if not stock_list:
            await interaction.response.send_message("❌ 申し訳ありません。現在売り切れです。", ephemeral=True)
            return

        # 決済処理と商品渡し
        user_balances[user_id] -= ITEM_PRICE
        item = stock_list.pop(0)

        await interaction.response.send_message(
            f"🎉 ご購入ありがとうございます！\n**【商品コード】**:\n`{item}`\n(残高: {user_balances[user_id]}pt)", 
            ephemeral=True
        )


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)


# 自販機設置コマンド (/vending)
@bot.tree.command(name="vending", description="自販機パネルを設置します（管理者用）")
@app_commands.checks.has_permissions(administrator=True)
async def vending(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 自動販売機",
        description=f"下のボタンを押して商品を購入できます。\n\n**価格**: {ITEM_PRICE}pt\n**現在の在庫数**: {len(stock_list)}個",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed, view=VendingMachineView())
    await interaction.response.send_message("自販機パネルを設置しました！", ephemeral=True)


# ポイント付与コマンド (/add_points)
@bot.tree.command(name="add_points", description="指定ユーザーにポイントを付与します")
@app_commands.checks.has_permissions(administrator=True)
async def add_points(interaction: discord.Interaction, target: discord.User, amount: int):
    user_balances[target.id] = user_balances.get(target.id, 0) + amount
    await interaction.response.send_message(
        f"✅ {target.mention} に {amount}pt を付与しました。（現在: {user_balances[target.id]}pt）"
    )


# 残高確認コマンド (/balance)
@bot.tree.command(name="balance", description="自分の所持ポイントを確認します")
async def balance(interaction: discord.Interaction):
    bal = user_balances.get(interaction.user.id, 0)
    await interaction.response.send_message(f"💰 あなたの所持ポイント: **{bal}pt**", ephemeral=True)


# 環境変数「DISCORD_TOKEN」からトークンを読み込んで起動
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("エラー: DISCORD_TOKEN が設定されていません。")
else:
    bot.run(TOKEN)
