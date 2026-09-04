import os
import re
from threading import Thread
import discord
from discord.ext import commands
from discord import app_commands
from flask import Flask

# ==========================================
# 1. 常駐化用 Flask Webサーバー（UptimeRobot用）
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Renderなどの環境変数のPORT（無ければ8080）で起動
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# ==========================================
# 2. Discord Bot 本体ロジック
# ==========================================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ALLOWED_INVITE = "https://discord.gg/E5NfgyDM3M"
HEX_COLOR_PATTERN = re.compile(r"^#([A-Fa-f0-9]{6})$")

class VerifyView(discord.ui.View):
    def __init__(self, role: discord.Role, button_label: str, button_color: str):
        super().__init__(timeout=None)
        
        button = discord.ui.Button(
            label=button_label,
            style=discord.ButtonStyle.primary,
            custom_id=f"verify_button_{role.id}"
        )
        button.callback = self.button_callback
        self.role = role
        self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        # 招待リンクチェック
        invites = await interaction.guild.invites()
        is_valid_invite = any(invite.url == ALLOWED_INVITE for invite in invites)

        if not is_valid_invite:
            await interaction.response.send_message(
                "❌ エラー: 指定された招待リンク(https://discord.gg/E5NfgyDM3M) がこのサーバーに存在しないため、認証機能を利用できません。",
                ephemeral=True
            )
            return

        # ロール付与処理
        if self.role in interaction.user.roles:
            await interaction.response.send_message(
                f"すでに {self.role.mention} を所有しています。",
                ephemeral=True
            )
        else:
            try:
                await interaction.user.add_roles(self.role)
                await interaction.response.send_message(
                    f"✅ {self.role.mention} を付与しました！",
                    ephemeral=True
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Botにロールを付与する権限がありません。Botの権限順位を確認してください。",
                    ephemeral=True
                )

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

@bot.tree.command(name="verify", description="認証パネルを作成します")
@app_commands.describe(
    role="付与するロール",
    title="タイトル（任意）",
    description="説明文（任意）",
    buttonlabel="ボタンのラベル（任意）",
    buttoncolor="ボタンの色 16進数（任意 例: #abc123）"
)
async def verify(
    interaction: discord.Interaction,
    role: discord.Role,
    title: str = None,
    description: str = None,
    buttonlabel: str = None,
    buttoncolor: str = None
):
    invites = await interaction.guild.invites()
    is_valid_invite = any(invite.url == ALLOWED_INVITE for invite in invites)

    if not is_valid_invite:
        await interaction.response.send_message(
            "❌ エラー: 指定の招待リンク (https://discord.gg/E5NfgyDM3M) がこのサーバーに作成されていないため、コマンドを実行できません。",
            ephemeral=True
        )
        return

    # デフォルト値の設定
    final_title = title if title else "認証"
    final_description = description if description else f"ボタンを押すと{role.mention}が付与されます。"
    final_label = buttonlabel if buttonlabel else "✅┋認証する"
    hex_code = buttoncolor if buttoncolor else "#5865F2"

    if not HEX_COLOR_PATTERN.match(hex_code):
        await interaction.response.send_message(
            "❌ エラー: カラーコードは `#abc123` のような16進数形式で指定してください。",
            ephemeral=True
        )
        return

    embed_color = int(hex_code.lstrip("#"), 16)

    embed = discord.Embed(
        title=final_title,
        description=final_description,
        color=embed_color
    )

    view = VerifyView(role=role, button_label=final_label, button_color=hex_code)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("認証パネルを作成しました。", ephemeral=True)


# ==========================================
# 3. 起動処理（環境変数 DISCORD_TOKEN の取得）
# ==========================================
if __name__ == "__main__":
    # Webサーバー起動
    keep_alive()

    # 環境変数からトークンを取得
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise ValueError("環境変数 DISCORD_TOKEN が設定されていません。")

    # Bot起動
    bot.run(token)
