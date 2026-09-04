import re
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = "YOUR_BOT_TOKEN_HERE"
ALLOWED_INVITE_URL = "https://discord.gg/mzDCQBZWK"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def parse_hex_color(hex_str: str) -> discord.Color:
    hex_str = hex_str.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", hex_str):
        return discord.Color(int(hex_str, 16))
    return discord.Color(0x5865F2)

class DynamicVerifyView(discord.ui.View):
    def __init__(self, role_id: int, label: str, style: discord.ButtonStyle):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label=label,
            style=style,
            custom_id=f"verify_btn:{role_id}"
        )
        button.callback = self.button_callback
        self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        custom_id = interaction.data.get("custom_id", "")
        role_id = int(custom_id.split(":")[1])
        
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message("設定されたロールが見つかりませんでした。", ephemeral=True)
            return

        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role)
            await interaction.response.send_message(f"**{role.name}** ロールを解除しました。", ephemeral=True)
        else:
            await member.add_roles(role)
            await interaction.response.send_message(f"**{role.name}** ロールを付与しました！", ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user.name}")

@bot.tree.command(name="verify", description="認証パネルを作成します")
@app_commands.describe(
    role="付与するロール（必須）",
    title="パネルのタイトル（任意）",
    description="説明文（任意 / {{role}} はロール名に置換されます）",
    buttonlabel="ボタンの表示ラベル（任意）",
    buttoncolor="ボタンの色（任意 / 例: #abc123）"
)
@app_commands.checks.has_permissions(administrator=True)
async def verify(
    interaction: discord.Interaction,
    role: discord.Role,
    title: str = None,
    description: str = None,
    buttonlabel: str = None,
    buttoncolor: str = None
):
    guild_invites = await interaction.guild.invites()
    allowed_code = ALLOWED_INVITE_URL.strip().lower()
    
    is_valid_server = False
    for inv in guild_invites:
        if inv.url.lower() == allowed_code or f"https://discord.gg/{inv.code.lower()}" in allowed_code:
            is_valid_server = True
            break

    if not is_valid_server:
        await interaction.response.send_message(
            "❌ **エラー:** このサーバーは指定された招待リンク（`https://discord.gg/mzDCQBZWK`）と一致しないため、このコマンドは使用できません。",
            ephemeral=True
        )
        return

    embed_title = title if title is not None else "認証"
    
    if description is None:
        embed_description = f"ボタンを押すと{role.mention}が付与されます。"
    else:
        embed_description = description.replace("{{role}}", role.mention)
        
    btn_label = buttonlabel if buttonlabel is not None else "✅┋認証する"
    hex_color_str = buttoncolor if buttoncolor is not None else "#5865F2"

    btn_style = discord.ButtonStyle.primary

    embed_color = parse_hex_color(hex_color_str)
    embed = discord.Embed(
        title=embed_title,
        description=embed_description,
        color=embed_color
    )

    view = DynamicVerifyView(
        role_id=role.id,
        label=btn_label,
        style=btn_style
    )

    await interaction.response.send_message(embed=embed, view=view)

bot.run(TOKEN)
