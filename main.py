import uuid
import discord
from discord import app_commands
from discord.ext import commands

# ----------------------------------------------------
# 簡易データベース (メモリ保持)
# ----------------------------------------------------
# vending_machines = {
#     "uuid": {
#         "name": "自販機名",
#         "items": {
#             "item_id": {
#                 "name": "商品名", "type": "有限"/"無限",
#                 "money": 100, "manera": 100,
#                 "description": "説明", "emoji": "🍎", "stock": 10
#             }
#         }
#     }
# }
vending_machines = {}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ----------------------------------------------------
# 動的【検知】用 Autocomplete (自販機名・商品名)
# ----------------------------------------------------
async def vending_machine_autocomplete(
    interaction: discord.Interaction, current: str
):
  return [
      app_commands.Choice(name=data["name"], value=v_id)
      for v_id, data in vending_machines.items()
      if current.lower() in data["name"].lower()
  ][:25]


# ----------------------------------------------------
# UIコンポーネント (モーダル・ボタン・ドロップダウン)
# ----------------------------------------------------


# モーダル: 商品購入フォーム 〔自入力〕
class PurchaseModal(discord.ui.Modal, title="商品購入"):
  quantity = discord.ui.TextInput(
      label="個数*",
      placeholder="購入数を入力",
      required=True,
      style=discord.TextStyle.short,
  )
  paypay_link = discord.ui.TextInput(
      label="PayPay送金リンク*",
      placeholder="PayPayのリンクを入力",
      required=True,
      style=discord.TextStyle.short,
  )

  def __init__(self, payment_method: str):
    super().__init__()
    self.payment_method = payment_method

  async def on_submit(self, interaction: discord.Interaction):
    await interaction.response.send_message(
        f"購入申請を受け付けました。\n決済方法: {self.payment_method}\n個数:"
        f" {self.quantity.value}\nリンク: {self.paypay_link.value}",
        ephemeral=True,
    )


# ドロップダウン: 決済方法選択 〔リスト〕
class PaymentSelect(discord.ui.Select):

  def __init__(self):
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
    await interaction.response.send_modal(PurchaseModal(payment_method=selected))


# モーダル: 商品内容変更 〔自入力〕
class EditItemModal(discord.ui.Modal, title="商品内容変更"):

  def __init__(self, v_id: str, item_id: str, current_item: dict):
    super().__init__()
    self.v_id = v_id
    self.item_id = item_id

    self.item_name = discord.ui.TextInput(
        label="商品名*", default=current_item["name"], required=True
    )
    self.description = discord.ui.TextInput(
        label="説明文",
        default=current_item.get("description", ""),
        required=False,
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
        f"商品「{self.item_name.value}」の内容を更新しました。",
        ephemeral=True,
    )


# ドロップダウン: 変更対象商品選択 【検知(選択した自販機の中にある商品名)】
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


# ドロップダウン: 削除対象商品選択 【検知(選択した自販機の中にある商品名)】
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

    # 〔button〕{赤色}削除
    confirm_btn = discord.ui.Button(
        label="削除", style=discord.ButtonStyle.danger
    )
    # 〔button〕{#36363B}キャンセル
    cancel_btn = discord.ui.Button(
        label="キャンセル", style=discord.ButtonStyle.secondary
    )

    async def confirm_callback(inter: discord.Interaction):
      del vending_machines[self.v_id]["items"][item_id]
      await inter.response.edit_message(
          content=f"選択した商品「{item_name}」を削除しました。", view=None
      )

    async def cancel_callback(inter: discord.Interaction):
      await inter.response.edit_message(content="処理をキャンセルしました。", view=None)

    confirm_btn.callback = confirm_callback
    cancel_btn.callback = cancel_callback

    view.add_item(confirm_btn)
    view.add_item(cancel_btn)

    await interaction.response.send_message(
        f"本当に削除しますか？\n実行した場合この操作は取り消せません。",
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
async def create_vending_machine(
    interaction: discord.Interaction, name: str
):
  # ランダムな長いIDを発行 (例: abcdefg-1123456-a1b2c3d4 代替)
  v_id = str(uuid.uuid4())
  vending_machines[v_id] = {"name": name, "items": {}}

  await interaction.response.send_message(
      f"自販機「{name}」を作成しました。(ID: `{v_id}`)", ephemeral=True
  )


# /自販機削除
@bot.tree.command(
    name="自販機削除", description="自販機を完全に削除します。"
)
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

  # 〔自販機〕パネル作成
  embed = discord.Embed(
      title="# 自販機削除確認",  # (((自販機削除確認)))
      description=(
          f"本当に自販機「{target_name}」を削除しますか？\n**この操作は取り消せません。\nすべての商品と在庫のデータも削除されます。**"
      ),
      color=discord.Color.red(),
  )

  view = discord.ui.View()
  # 〔button〕{赤色}削除する
  delete_btn = discord.ui.Button(
      label="削除する", style=discord.ButtonStyle.danger
  )
  # 〔button〕{#36363B}キャンセル
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
    await inter.response.edit_message(content="削除をキャンセルしました。", embed=None, view=None)

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

  # 〔自販機〕〔横線色〕{黄緑} (#8A2BE2/緑系)
  embed = discord.Embed(title=title, color=discord.Color.green())

  # 説明文の設定 (指定がない場合は追加せず改行も発生させない)
  if panel_description:
    embed.description = panel_description

  # その自販機の中にある商品全てを縦に並べて表示
  for item_id, item in vm_data["items"].items():
    field_value = (
        f"マネー:{item['money']}マネーライト:{item['manera']}"
    )
    if item.get("description"):
      field_value = f"{item['description']}\n" + field_value

    emoji_str = f"{item['emoji']} " if item.get("emoji") else ""
    embed.add_field(
        name=f"{emoji_str}{item['name']}", value=field_value, inline=False
    )

  view = discord.ui.View()
  # 〔button〕{黄緑}🛒購入する
  buy_btn = discord.ui.Button(
      label="🛒購入する", style=discord.ButtonStyle.success
  )
  # 〔button〕{赤色}在庫確認
  stock_btn = discord.ui.Button(
      label="在庫確認", style=discord.ButtonStyle.danger
  )

  # 購入ボタン処理
  async def buy_cb(inter: discord.Interaction):
    buy_view = discord.ui.View()
    buy_view.add_item(PaymentSelect())  # 〔リスト〕マネーライト、マネー
    await inter.response.send_message(
        "決済方法を選択してください。", view=buy_view, ephemeral=True
    )

  # 在庫確認ボタン処理
  async def stock_cb(inter: discord.Interaction):
    stock_info = []
    for i_id, i_data in vm_data["items"].items():
      stock_num = (
          "無限"
          if i_data["type"] == "無限"
          else str(i_data.get("stock", "有限"))
      )
      stock_info.append(f"**{i_data['name']}**\n在庫:{stock_num}")

    msg = "\n\n".join(stock_info) if stock_info else "商品が登録されていません。"
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
      f"自販機「{vm_name}」に商品名「{name}」を追加しました。",
      ephemeral=True,
  )


# /商品内容変更
@bot.tree.command(name="商品内容変更", description="商品の内容を変更")
@app_commands.describe(vending_machine_id="対象の自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def edit_item(interaction: discord.Interaction, vending_machine_id: str):
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


bot.run("YOUR_BOT_TOKEN_HERE")
