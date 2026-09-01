import os
import re
import uuid
import discord
from discord import app_commands
from discord.ext import commands
from paypaython import PayPay

# ----------------------------------------------------
# PayPay 設定
# ----------------------------------------------------
# ローカルPC等で事前に取得したアクセストークンをここに貼り付けます
SAVED_TOKEN = "ここに取得したトークン文字列を貼り付ける"


def get_paypay_client() -> PayPay:
  """保存済みのアクセストークンで直接初期化"""
  return PayPay(access_token=SAVED_TOKEN)


def safe_paypay_receive(link: str):
  """トークンを使って受取を実行する関数"""
  client = get_paypay_client()
  link_info = client.link_check(link)
  receive_result = client.link_receive(link)
  return link_info, receive_result


# ----------------------------------------------------
# 簡易データベース (メモリ保持)
# ----------------------------------------------------
vending_machines = {}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


async def vending_machine_autocomplete(
    interaction: discord.Interaction, current: str
):
  return [
      app_commands.Choice(name=data["name"], value=v_id)
      for v_id, data in vending_machines.items()
      if current.lower() in data["name"].lower()
  ][:25]


# ----------------------------------------------------
# UIコンポーネント
# ----------------------------------------------------


# モーダル: 商品購入フォーム
class PurchaseModal(discord.ui.Modal, title="商品購入"):

  quantity = discord.ui.TextInput(
      label="個数*",
      placeholder="購入数を入力",
      required=True,
      style=discord.TextStyle.short,
  )
  paypay_link = discord.ui.TextInput(
      label="PayPay送金リンク*",
      placeholder="https://pay.paypay.ne.jp/...",
      required=True,
      style=discord.TextStyle.short,
  )

  def __init__(self, v_id: str, item_id: str, payment_method: str):
    super().__init__()
    self.v_id = v_id
    self.item_id = item_id
    self.payment_method = payment_method

  async def on_submit(self, interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    # 1. 個数の数値チェック
    try:
      qty = int(self.quantity.value)
      if qty <= 0:
        raise ValueError
    except ValueError:
      await interaction.followup.send(
          "エラー: 個数は1以上の正の整数で入力してください。", ephemeral=True
      )
      return

    # 2. 商品情報の取得と必要金額の計算
    item = vending_machines[self.v_id]["items"].get(self.item_id)
    if not item:
      await interaction.followup.send(
          "エラー: 商品が見つかりませんでした。", ephemeral=True
      )
      return

    unit_price = (
        item["money"] if self.payment_method == "マネー" else item["manera"]
    )
    expected_total = unit_price * qty
    link = self.paypay_link.value.strip()

    # 3. PayPay受取処理
    try:
      link_info, receive_result = safe_paypay_receive(link)
      link_amount = int(link_info.money)

      # 金額の一致確認
      if link_amount != expected_total:
        await interaction.followup.send(
            f"❌ 金額が一致しません。\n"
            f"必要金額: {expected_total} 円 ({self.payment_method})\n"
            f"送金リンクの金額: {link_amount} 円",
            ephemeral=True,
        )
        return

      # 在庫更新（有限の場合）
      if item["type"] == "有限":
        item["stock"] = max(0, item.get("stock", 0) - qty)

      await interaction.followup.send(
          f"✅ 決済および受取が完了しました！\n"
          f"商品: {item['name']} × {qty}個\n"
          f"受取金額: {link_amount}円\n"
          f"ステータス: {receive_result.status}",
          ephemeral=True,
      )

    except Exception as e:
      await interaction.followup.send(
          f"❌ 受取処理に失敗しました。トークンの期限切れか、リンクが無効の可能性があります。\n"
          f"エラー内容: {e}",
          ephemeral=True,
      )


# ドロップダウン: 決済方法選択
class PaymentSelect(discord.ui.Select):

  def __init__(self, v_id: str, item_id: str):
    self.v_id = v_id
    self.item_id = item_id
    options = [
        discord.SelectOption(
            label="マネー", value="マネー", description="マネーで決済"
        ),
        discord.SelectOption(
            label="マネーライト",
            value="マネーライト",
            description="マネーライトで決済",
        ),
    ]
    super().__init__(
        placeholder="決済方法を選択してください",
        min_values=1,
        max_values=1,
        options=options,
    )

  async def callback(self, interaction: discord.Interaction):
    selected = self.values[0]
    await interaction.response.send_modal(
        PurchaseModal(
            v_id=self.v_id, item_id=self.item_id, payment_method=selected
        )
    )


# ドロップダウン: 購入商品選択
class PurchaseItemSelect(discord.ui.Select):

  def __init__(self, v_id: str):
    self.v_id = v_id
    items = vending_machines[v_id]["items"]
    options = [
        discord.SelectOption(
            label=data["name"], value=i_id, emoji=data.get("emoji")
        )
        for i_id, data in items.items()
    ]
    super().__init__(
        placeholder="購入する商品を選択してください",
        min_values=1,
        max_values=1,
        options=options,
    )

  async def callback(self, interaction: discord.Interaction):
    item_id = self.values[0]
    view = discord.ui.View()
    view.add_item(PaymentSelect(v_id=self.v_id, item_id=item_id))
    await interaction.response.send_message(
        "決済方法を選択してください。", view=view, ephemeral=True
    )


# モーダル: 商品内容変更
class EditItemModal(discord.ui.Modal, title="商品内容変更"):

  def __init__(self, v_id: str, item_id: str, current_item: dict):
    super().__init__()
    self.v_id = v_id
    self.item_id = item_id

    self.item_name = discord.ui.TextInput(
        label="商品名*", default=current_item["name"], required=True
    )
    self.description = discord.ui.TextInput(
        label="説明文", default=current_item.get("description", ""), required=False
    )
    self.item_type = discord.ui.TextInput(
        label="タイプ(有限か無限以外だったらエラー)*",
        default=current_item["type"],
        required=True,
    )
    self.money = discord.ui.TextInput(
        label="マネー*", default=str(current_item["money"]), required=True
    )
    self.manera = discord.ui.TextInput(
        label="マネーライト*", default=str(current_item["manera"]), required=True
    )

    self.add_item(self.item_name)
    self.add_item(self.description)
    self.add_item(self.item_type)
    self.add_item(self.money)
    self.add_item(self.manera)

  async def on_submit(self, interaction: discord.Interaction):
    if self.item_type.value not in ["有限", "無限"]:
      await interaction.response.send_message(
          "エラー: タイプは「有限」または「無限」で入力してください。",
          ephemeral=True,
      )
      return

    try:
      m_val = int(self.money.value)
      ml_val = int(self.manera.value)
    except ValueError:
      await interaction.response.send_message(
          "エラー: マネー・マネーライトは数値で入力してください。",
          ephemeral=True,
      )
      return

    item = vending_machines[self.v_id]["items"][self.item_id]
    item["name"] = self.item_name.value
    item["description"] = self.description.value
    item["type"] = self.item_type.value
    item["money"] = m_val
    item["manera"] = ml_val

    await interaction.response.send_message(
        f"商品「{self.item_name.value}」の内容を更新しました。", ephemeral=True
    )


# ドロップダウン: 変更対象商品選択
class EditItemSelect(discord.ui.Select):

  def __init__(self, v_id: str):
    self.v_id = v_id
    items = vending_machines[v_id]["items"]
    options = [
        discord.SelectOption(
            label=data["name"], value=i_id, emoji=data.get("emoji")
        )
        for i_id, data in items.items()
    ]
    super().__init__(
        placeholder="内容を変更する商品を選択してください",
        min_values=1,
        max_values=1,
        options=options,
    )

  async def callback(self, interaction: discord.Interaction):
    item_id = self.values[0]
    item = vending_machines[self.v_id]["items"][item_id]
    await interaction.response.send_modal(
        EditItemModal(self.v_id, item_id, item)
    )


# ドロップダウン: 削除対象商品選択
class DeleteItemSelect(discord.ui.Select):

  def __init__(self, v_id: str):
    self.v_id = v_id
    items = vending_machines[v_id]["items"]
    options = [
        discord.SelectOption(
            label=data["name"], value=i_id, emoji=data.get("emoji")
        )
        for i_id, data in items.items()
    ]
    super().__init__(
        placeholder="削除する商品を選択してください",
        min_values=1,
        max_values=1,
        options=options,
    )

  async def callback(self, interaction: discord.Interaction):
    item_id = self.values[0]
    item_name = vending_machines[self.v_id]["items"][item_id]["name"]

    view = discord.ui.View()
    confirm_btn = discord.ui.Button(
        label="削除", style=discord.ButtonStyle.danger
    )
    cancel_btn = discord.ui.Button(
        label="キャンセル", style=discord.ButtonStyle.secondary
    )

    async def confirm_callback(inter: discord.Interaction):
      del vending_machines[self.v_id]["items"][item_id]
      await inter.response.edit_message(
          content=f"選択した商品「{item_name}」を削除しました。", view=None
      )

    async def cancel_callback(inter: discord.Interaction):
      await inter.response.edit_message(
          content="処理をキャンセルしました。", view=None
      )

    confirm_btn.callback = confirm_callback
    cancel_btn.callback = cancel_callback

    view.add_item(confirm_btn)
    view.add_item(cancel_btn)

    await interaction.response.send_message(
        "本当に削除しますか？\n実行した場合この操作は取り消せません。",
        view=view,
        ephemeral=True,
    )


# ----------------------------------------------------
# スラッシュコマンド定義
# ----------------------------------------------------


@bot.event
async def on_ready():
  await bot.tree.sync()
  print(f"ログイン完了: {bot.user}")


# /自販機作成
@bot.tree.command(name="自販機作成", description="自販機を作成")
@app_commands.describe(name="自販機の名前")
async def create_vending_machine(interaction: discord.Interaction, name: str):
  v_id = str(uuid.uuid4())
  vending_machines[v_id] = {"name": name, "items": {}}
  await interaction.response.send_message(
      f"自販機「{name}」を作成しました。(ID: `{v_id}`)", ephemeral=True
  )


# /自販機削除
@bot.tree.command(name="自販機削除", description="自販機を完全に削除します。")
@app_commands.describe(vending_machine_id="削除する自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_vending_machine(
    interaction: discord.Interaction, vending_machine_id: str
):
  if vending_machine_id not in vending_machines:
    await interaction.response.send_message(
        "指定された自販機が見つかりません。", ephemeral=True
    )
    return

  target_name = vending_machines[vending_machine_id]["name"]
  embed = discord.Embed(
      title="# 自販機削除確認",
      description=(
          f"本当に自販機「{target_name}」を削除しますか？\n"
          "**この操作は取り消せません。\n"
          "すべての商品と在庫のデータも削除されます。**"
      ),
      color=discord.Color.red(),
  )

  view = discord.ui.View()
  delete_btn = discord.ui.Button(
      label="削除する", style=discord.ButtonStyle.danger
  )
  cancel_btn = discord.ui.Button(
      label="キャンセル", style=discord.ButtonStyle.secondary
  )

  async def delete_cb(inter: discord.Interaction):
    del vending_machines[vending_machine_id]
    await inter.response.edit_message(
        content=f"自販機「{target_name}」を完全に削除しました。",
        embed=None,
        view=None,
    )

  async def cancel_cb(inter: discord.Interaction):
    await inter.response.edit_message(
        content="削除をキャンセルしました。", embed=None, view=None
    )

  delete_btn.callback = delete_cb
  cancel_btn.callback = cancel_cb

  view.add_item(delete_btn)
  view.add_item(cancel_btn)

  await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# /自販機設置
@bot.tree.command(name="自販機設置", description="自販機を設置します")
@app_commands.describe(
    vending_machine_id="設置する自販機",
    panel_title="パネルのタイトル",
    panel_description="パネルの説明文",
)
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def place_vending_machine(
    interaction: discord.Interaction,
    vending_machine_id: str,
    panel_title: str = None,
    panel_description: str = None,
):
  if vending_machine_id not in vending_machines:
    await interaction.response.send_message(
        "指定された自販機が見つかりません。", ephemeral=True
    )
    return

  vm_data = vending_machines[vending_machine_id]
  title = panel_title if panel_title else vm_data["name"]

  embed = discord.Embed(title=title, color=discord.Color.green())
  if panel_description:
    embed.description = panel_description

  for item_id, item in vm_data["items"].items():
    field_value = f"マネー:{item['money']} | マネーライト:{item['manera']}"
    if item.get("description"):
      field_value = f"{item['description']}\n" + field_value
    emoji_str = f"{item['emoji']} " if item.get("emoji") else ""
    embed.add_field(
        name=f"{emoji_str}{item['name']}", value=field_value, inline=False
    )

  view = discord.ui.View()
  buy_btn = discord.ui.Button(
      label="🛒購入する", style=discord.ButtonStyle.success
  )
  stock_btn = discord.ui.Button(
      label="在庫確認", style=discord.ButtonStyle.danger
  )

  async def buy_cb(inter: discord.Interaction):
    if not vm_data["items"]:
      await inter.response.send_message(
          "この自販機には商品がありません。", ephemeral=True
      )
      return
    buy_view = discord.ui.View()
    buy_view.add_item(PurchaseItemSelect(vending_machine_id))
    await inter.response.send_message(
        "購入する商品を選択してください。", view=buy_view, ephemeral=True
    )

  async def stock_cb(inter: discord.Interaction):
    stock_info = []
    for i_id, i_data in vm_data["items"].items():
      stock_num = (
          "無限" if i_data["type"] == "無限" else str(i_data.get("stock", 0))
      )
      stock_info.append(f"**{i_data['name']}**\n在庫:{stock_num}")
    msg = (
        "\n\n".join(stock_info)
        if stock_info
        else "商品が登録されていません。"
    )
    await inter.response.send_message(msg, ephemeral=True)

  buy_btn.callback = buy_cb
  stock_btn.callback = stock_cb

  view.add_item(buy_btn)
  view.add_item(stock_btn)

  await interaction.response.send_message(embed=embed, view=view)


# /商品追加
@bot.tree.command(name="商品追加", description="自販機に商品を追加")
@app_commands.describe(
    vending_machine_id="商品を追加する自販機",
    type="商品タイプ",
    monay="マネーの値段",
    manera="マネーライトの金額",
    name="商品名",
    description="商品説明文",
    emoji="一絵文字のみ",
)
@app_commands.choices(
    type=[
        app_commands.Choice(name="有限", value="有限"),
        app_commands.Choice(name="無限", value="無限"),
    ]
)
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def add_item(
    interaction: discord.Interaction,
    vending_machine_id: str,
    type: str,
    monay: int,
    manera: int,
    name: str,
    description: str = None,
    emoji: str = None,
):
  if vending_machine_id not in vending_machines:
    await interaction.response.send_message(
        "指定された自販機が見つかりません。", ephemeral=True
    )
    return

  item_id = str(uuid.uuid4())
  vending_machines[vending_machine_id]["items"][item_id] = {
      "name": name,
      "type": type,
      "money": monay,
      "manera": manera,
      "description": description,
      "emoji": emoji,
      "stock": "無限" if type == "無限" else 0,
  }

  vm_name = vending_machines[vending_machine_id]["name"]
  await interaction.response.send_message(
      f"自販機「{vm_name}」に商品名「{name}」を追加しました。", ephemeral=True
  )


# /商品内容変更
@bot.tree.command(name="商品内容変更", description="商品の内容を変更")
@app_commands.describe(vending_machine_id="対象の自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def edit_item(
    interaction: discord.Interaction, vending_machine_id: str
):
  if (
      vending_machine_id not in vending_machines
      or not vending_machines[vending_machine_id]["items"]
  ):
    await interaction.response.send_message(
        "指定された自販機が存在しないか、商品が登録されていません。",
        ephemeral=True,
    )
    return

  view = discord.ui.View()
  view.add_item(EditItemSelect(vending_machine_id))
  await interaction.response.send_message(
      "内容を変更する商品を選択してください", view=view, ephemeral=True
  )


# /商品削除
@bot.tree.command(name="商品削除", description="商品を削除")
@app_commands.describe(vending_machine_id="削除する商品がある自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_item(
    interaction: discord.Interaction, vending_machine_id: str
):
  if (
      vending_machine_id not in vending_machines
      or not vending_machines[vending_machine_id]["items"]
  ):
    await interaction.response.send_message(
        "指定された自販機が存在しないか、商品が登録されていません。",
        ephemeral=True,
    )
    return

  view = discord.ui.View()
  view.add_item(DeleteItemSelect(vending_machine_id))
  await interaction.response.send_message(
      "削除する商品を選択", view=view, ephemeral=True
  )


# ----------------------------------------------------
# 起動処理
# ----------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")

if TOKEN is None:
  print("エラー: 環境変数 'DISCORD_TOKEN' が設定されていません。")
else:
  bot.run(TOKEN)
