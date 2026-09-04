import os
import uuid
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

# PayPayモジュールの呼び出し
from paypay import PayPay, PayPayError, PayPayLoginError, PayPayNetWorkError, load_tokens

# ----------------------------------------------------
# 1. スリープ防止用 Flask サーバー
# ----------------------------------------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    Thread(target=run).start()


# ----------------------------------------------------
# 2. ボット初期化 & データベース
# ----------------------------------------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

vending_machines = {}
coupons = {}
paypay_client = None


# ----------------------------------------------------
# 3. オートコンプリート関数
# ----------------------------------------------------
async def vending_machine_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=data["name"], value=v_id)
        for v_id, data in vending_machines.items()
        if current.lower() in data["name"].lower()
    ][:25]

async def coupon_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=code, value=code)
        for code in coupons.keys()
        if current.lower() in code.lower()
    ][:25]


# ----------------------------------------------------
# 4. UIコンポーネント
# ----------------------------------------------------
class PurchaseModal(discord.ui.Modal, title="商品購入"):
    def __init__(self, v_id: str, item_id: str, payment_method: str):
        super().__init__()
        self.v_id = v_id
        self.item_id = item_id
        self.payment_method = payment_method

        self.quantity = discord.ui.TextInput(
            label="購入個数*", placeholder="1", default="1", required=True
        )
        self.paypay_link = discord.ui.TextInput(
            label="PayPay送金リンク*",
            placeholder="https://pay.paypay.ne.jp/...",
            required=True,
        )
        self.passcode = discord.ui.TextInput(
            label="パスコード (設定されている場合)",
            placeholder="4桁の数字",
            required=False,
            max_length=4
        )
        self.add_item(self.quantity)
        self.add_item(self.paypay_link)
        self.add_item(self.passcode)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            qty = int(self.quantity.value)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await interaction.followup.send("購入個数は1以上の整数で入力してください。", ephemeral=True)
            return

        item = vending_machines.get(self.v_id, {}).get("items", {}).get(self.item_id)
        if not item:
            await interaction.followup.send("商品が見つかりませんでした。", ephemeral=True)
            return

        if item["type"] == "有限":
            stock_list = item.get("stock_list", [])
            if len(stock_list) < qty:
                await interaction.followup.send(f"在庫が足りません。(現在在庫: {len(stock_list)}個)", ephemeral=True)
                return

        unit_price = item["money"] if self.payment_method == "マネー" else item["manera"]
        total_price = unit_price * qty

        if not paypay_client:
            await interaction.followup.send("PayPay連携が初期化されていません。`/paypay_login` を1度実行してください。", ephemeral=True)
            return

        try:
            link_url = self.paypay_link.value
            pass_code = self.passcode.value if self.passcode.value else None

            link_info = paypay_client.link_check(link_url)

            sent_amount = link_info.money if self.payment_method == "マネー" else link_info.money_light
            if sent_amount < total_price:
                await interaction.followup.send(
                    f"金額（またはマネー種別）が不足しています。\n"
                    f"必要額: {total_price}円 ({self.payment_method}) / 送金額: {sent_amount}円",
                    ephemeral=True
                )
                return

            paypay_client.link_receive(url=link_url, password=pass_code)

        except PayPayLoginError:
            await interaction.followup.send("❌ 認証情報の完全自動更新に失敗しました。`/paypay_login` で1度再ログインしてください。", ephemeral=True)
            return
        except PayPayError as e:
            await interaction.followup.send(f"❌ PayPay処理エラー: {e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ 決済失敗: {e}", ephemeral=True)
            return

        delivery_msg = []
        if item["type"] == "有限":
            drawn = item["stock_list"][:qty]
            item["stock_list"] = item["stock_list"][qty:]
            for d in drawn:
                msg_part = f"\n{d['msg']}" if d.get("msg") else ""
                delivery_msg.append(f"```\n{d['content']}\n```" + msg_part)
        else:
            delivery_msg.append(f"ご購入ありがとうございます！\n商品: {item['name']} x {qty}")

        result_text = f"✅ **決済が完了し、残高に直接チャージされました！**\n\n" + "\n".join(delivery_msg)
        await interaction.followup.send(result_text, ephemeral=True)


class PaymentSelect(discord.ui.Select):
    def __init__(self, v_id: str, item_id: str):
        self.v_id = v_id
        self.item_id = item_id
        options = [
            discord.SelectOption(label="マネー", value="マネー", description="マネーで決済"),
            discord.SelectOption(label="マネーライト", value="マネーライト", description="マネーライトで決済"),
        ]
        super().__init__(placeholder="決済方法を選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PurchaseModal(self.v_id, self.item_id, self.values[0]))


class EditItemModal(discord.ui.Modal, title="商品内容変更"):
    def __init__(self, v_id: str, item_id: str, current_item: dict):
        super().__init__()
        self.v_id = v_id
        self.item_id = item_id

        self.item_name = discord.ui.TextInput(label="商品名*", default=str(current_item["name"]), required=True)
        self.description = discord.ui.TextInput(label="説明文", default=str(current_item.get("description", "") or ""), required=False)
        self.item_type = discord.ui.TextInput(label="タイプ(有限/無限)*", default=str(current_item["type"]), required=True)
        self.money = discord.ui.TextInput(label="マネー*", default=str(current_item["money"]), required=True)
        self.manera = discord.ui.TextInput(label="マネーライト*", default=str(current_item["manera"]), required=True)

        self.add_item(self.item_name)
        self.add_item(self.description)
        self.add_item(self.item_type)
        self.add_item(self.money)
        self.add_item(self.manera)

    async def on_submit(self, interaction: discord.Interaction):
        if self.item_type.value not in ["有限", "無限"]:
            await interaction.response.send_message("エラー: タイプは「有限」または「無限」で入力してください。", ephemeral=True)
            return

        try:
            m_val = int(self.money.value)
            ml_val = int(self.manera.value)
        except ValueError:
            await interaction.response.send_message("エラー: マネー・マネーライトは数値で入力してください。", ephemeral=True)
            return

        item = vending_machines[self.v_id]["items"][self.item_id]
        item["name"] = self.item_name.value
        item["description"] = self.description.value
        item["type"] = self.item_type.value
        item["money"] = m_val
        item["manera"] = ml_val

        await interaction.response.send_message(f"商品「{self.item_name.value}」の内容を更新しました。", ephemeral=True)


class EditItemSelect(discord.ui.Select):
    def __init__(self, v_id: str):
        self.v_id = v_id
        items = vending_machines[v_id]["items"]
        options = [
            discord.SelectOption(label=data["name"], value=i_id, emoji=data.get("emoji"))
            for i_id, data in items.items()
        ]
        super().__init__(placeholder="内容を変更する商品を選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_id = self.values[0]
        item = vending_machines[self.v_id]["items"][item_id]
        await interaction.response.send_modal(EditItemModal(self.v_id, item_id, item))


class DeleteItemSelect(discord.ui.Select):
    def __init__(self, v_id: str):
        self.v_id = v_id
        items = vending_machines[v_id]["items"]
        options = [
            discord.SelectOption(label=data["name"], value=i_id, emoji=data.get("emoji"))
            for i_id, data in items.items()
        ]
        super().__init__(placeholder="削除する商品を選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        item_id = self.values[0]
        item_name = vending_machines[self.v_id]["items"][item_id]["name"]

        view = discord.ui.View()
        confirm_btn = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger)
        cancel_btn = discord.ui.Button(label="キャンセル", style=discord.ButtonStyle.secondary)

        async def confirm_callback(inter: discord.Interaction):
            del vending_machines[self.v_id]["items"][item_id]
            await inter.response.edit_message(content=f"選択した商品「{item_name}」を削除しました。", view=None)

        async def cancel_callback(inter: discord.Interaction):
            await inter.response.edit_message(content="処理をキャンセルしました。", view=None)

        confirm_btn.callback = confirm_callback
        cancel_btn.callback = cancel_callback
        view.add_item(confirm_btn)
        view.add_item(cancel_btn)

        await interaction.response.send_message("本当に削除しますか？\n実行した場合この操作は取り消せません。", view=view, ephemeral=True)


class PayPayOTPModal(discord.ui.Modal, title="PayPay SMS認証"):
    otp = discord.ui.TextInput(
        label="SMSに届いた認証コード*",
        placeholder="1234 (数字のみ)",
        required=True,
        max_length=10
    )

    def __init__(self, temp_paypay: PayPay):
        super().__init__()
        self.temp_paypay = temp_paypay

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        global paypay_client

        try:
            self.temp_paypay.login(otp=self.otp.value)
            paypay_client = self.temp_paypay

            await interaction.followup.send(
                f"✅ **初回設定が完了しました！**\n"
                f"トークンは自動保存されました。今後はBot起動時も全自動でトークン更新が行われるため、何もしなくて大丈夫です！",
                ephemeral=True
            )
        except PayPayLoginError as e:
            await interaction.followup.send(f"❌ 認証エラー: 認証コードが正しいか確認してください。\n詳細: {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ ログイン処理エラー: {e}", ephemeral=True)


# ----------------------------------------------------
# 5. スラッシュコマンド群
# ----------------------------------------------------
@bot.event
async def on_ready():
    global paypay_client
    
    saved_data = load_tokens()
    if saved_data and saved_data.get("refresh_token"):
        paypay_client = PayPay(
            access_token=saved_data.get("access_token"),
            refresh_token=saved_data.get("refresh_token"),
            client_uuid=saved_data.get("client_uuid")
        )
        try:
            paypay_client.refresh_access_token()
            print("🚀 保存されたトークンを使用して全自動ログイン完了")
        except Exception as e:
            print(f"⚠️ 自動更新失敗: {e}")
    else:
        print("⚠️ PayPayの保存データが見つかりません。初回のみ `/paypay_login` を実行してください。")

    await bot.tree.sync()
    print(f"Bot ログイン完了: {bot.user}")


@bot.tree.command(name="paypay_login", description="PayPayにログインします（初回のみ1度だけ実行してください）")
@app_commands.describe(phone="PayPay登録電話番号(ハイフンなし)", password="PayPayパスワード")
async def paypay_login_cmd(interaction: discord.Interaction, phone: str, password: str):
    try:
        temp_paypay = PayPay(phone=phone, password=password)
        await interaction.response.send_modal(PayPayOTPModal(temp_paypay))

    except PayPayLoginError as e:
        await interaction.response.send_message(f"❌ ログイン失敗: 電話番号またはパスワードが違います。\n詳細: {e}", ephemeral=True)
    except PayPayNetWorkError as e:
        await interaction.response.send_message(f"❌ ネットワークエラーが発生しました。\n詳細: {e}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ エラーが発生しました: {e}", ephemeral=True)


@bot.tree.command(name="自販機作成", description="自販機を作成")
@app_commands.describe(name="自販機の名前")
async def create_vending_machine(interaction: discord.Interaction, name: str):
    v_id = str(uuid.uuid4())
    vending_machines[v_id] = {"name": name, "items": {}}
    await interaction.response.send_message(f"自販機「{name}」を作成しました。(ID: `{v_id}`)", ephemeral=True)


@bot.tree.command(name="自販機削除", description="自販機を完全に削除します。")
@app_commands.describe(vending_machine_id="削除する自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_vending_machine(interaction: discord.Interaction, vending_machine_id: str):
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("指定された自販機が見つかりません。", ephemeral=True)
        return

    target_name = vending_machines[vending_machine_id]["name"]
    embed = discord.Embed(
        title="# 自販機削除確認",
        description=f"本当に自販機「{target_name}」を削除しますか？\n**この操作は取り消せません。\nすべての商品と在庫のデータも削除されます。**",
        color=discord.Color.red(),
    )

    view = discord.ui.View()
    delete_btn = discord.ui.Button(label="削除する", style=discord.ButtonStyle.danger)
    cancel_btn = discord.ui.Button(label="キャンセル", style=discord.ButtonStyle.secondary)

    async def delete_cb(inter: discord.Interaction):
        del vending_machines[vending_machine_id]
        await inter.response.edit_message(content=f"自販機「{target_name}」を完全に削除しました。", embed=None, view=None)

    async def cancel_cb(inter: discord.Interaction):
        await inter.response.edit_message(content="削除をキャンセルしました。", embed=None, view=None)

    delete_btn.callback = delete_cb
    cancel_btn.callback = cancel_cb
    view.add_item(delete_btn)
    view.add_item(cancel_btn)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="自販機設置", description="自販機を設置します")
@app_commands.describe(vending_machine_id="設置する自販機", panel_title="パネルのタイトル", panel_description="パネルの説明文")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def place_vending_machine(interaction: discord.Interaction, vending_machine_id: str, panel_title: str = None, panel_description: str = None):
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("指定された自販機が見つかりません。", ephemeral=True)
        return

    vm_data = vending_machines[vending_machine_id]
    title = panel_title if panel_title else vm_data["name"]
    embed = discord.Embed(title=title, color=discord.Color.green())

    if panel_description:
        embed.description = panel_description

    for item_id, item in vm_data["items"].items():
        price_info = f"マネー:{item['money']}円 / マネーライト:{item['manera']}円"
        desc = f"{item['description']}\n" if item.get("description") else ""
        emoji_str = f"{item['emoji']} " if item.get("emoji") else ""
        embed.add_field(name=f"{emoji_str}{item['name']}", value=f"{desc}{price_info}", inline=False)

    view = discord.ui.View()
    buy_btn = discord.ui.Button(label="🛒購入する", style=discord.ButtonStyle.success)
    stock_btn = discord.ui.Button(label="在庫確認", style=discord.ButtonStyle.danger)

    async def buy_cb(inter: discord.Interaction):
        options = [
            discord.SelectOption(label=data["name"], value=i_id, emoji=data.get("emoji"))
            for i_id, data in vm_data["items"].items()
        ]
        if not options:
            await inter.response.send_message("商品が登録されていません。", ephemeral=True)
            return

        select = discord.ui.Select(placeholder="購入する商品を選択", options=options)

        async def select_cb(s_inter: discord.Interaction):
            selected_item_id = select.values[0]
            p_view = discord.ui.View()
            p_view.add_item(PaymentSelect(vending_machine_id, selected_item_id))
            await s_inter.response.send_message("決済方法を選択してください。", view=p_view, ephemeral=True)

        select.callback = select_cb
        item_view = discord.ui.View()
        item_view.add_item(select)
        await inter.response.send_message("商品を選択してください", view=item_view, ephemeral=True)

    async def stock_cb(inter: discord.Interaction):
        stock_info = []
        for i_id, i_data in vm_data["items"].items():
            stock_num = "無限" if i_data["type"] == "無限" else str(len(i_data.get("stock_list", [])))
            stock_info.append(f"**{i_data['name']}**\n在庫:{stock_num}")

        msg = "\n\n".join(stock_info) if stock_info else "商品が登録されていません。"
        await inter.response.send_message(msg, ephemeral=True)

    buy_btn.callback = buy_cb
    stock_btn.callback = stock_cb
    view.add_item(buy_btn)
    view.add_item(stock_btn)

    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="商品追加", description="自販機に商品を追加")
@app_commands.describe(vending_machine_id="商品を追加する自販機", type="商品タイプ", monay="マネーの値段", manera="マネーライトの金額", name="商品名", description="商品説明文", emoji="一絵文字のみ")
@app_commands.choices(type=[app_commands.Choice(name="有限", value="有限"), app_commands.Choice(name="無限", value="無限")])
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def add_item(interaction: discord.Interaction, vending_machine_id: str, type: str, monay: int, manera: int, name: str, description: str = None, emoji: str = None):
    if vending_machine_id not in vending_machines:
        await interaction.response.send_message("指定された自販機が見つかりません。", ephemeral=True)
        return

    item_id = str(uuid.uuid4())
    vending_machines[vending_machine_id]["items"][item_id] = {
        "name": name,
        "type": type,
        "money": monay,
        "manera": manera,
        "description": description,
        "emoji": emoji,
        "stock_list": [],
    }

    vm_name = vending_machines[vending_machine_id]["name"]
    await interaction.response.send_message(f"自販機「{vm_name}」に商品名「{name}」を追加しました。", ephemeral=True)


@bot.tree.command(name="商品内容変更", description="商品の内容を変更")
@app_commands.describe(vending_machine_id="対象の自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def edit_item(interaction: discord.Interaction, vending_machine_id: str):
    if vending_machine_id not in vending_machines or not vending_machines[vending_machine_id]["items"]:
        await interaction.response.send_message("指定された自販機が存在しないか、商品が登録されていません。", ephemeral=True)
        return

    view = discord.ui.View()
    view.add_item(EditItemSelect(vending_machine_id))
    await interaction.response.send_message("内容を変更する商品を選択してください", view=view, ephemeral=True)


@bot.tree.command(name="商品削除", description="商品を削除")
@app_commands.describe(vending_machine_id="削除する商品がある自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_item(interaction: discord.Interaction, vending_machine_id: str):
    if vending_machine_id not in vending_machines or not vending_machines[vending_machine_id]["items"]:
        await interaction.response.send_message("指定された自販機が存在しないか、商品が登録されていません。", ephemeral=True)
        return

    view = discord.ui.View()
    view.add_item(DeleteItemSelect(vending_machine_id))
    await interaction.response.send_message("削除する商品を選択", view=view, ephemeral=True)


@bot.tree.command(name="在庫追加", description="自販機に在庫を追加します")
@app_commands.describe(vending_machine_id="在庫を追加する自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def add_stock(interaction: discord.Interaction, vending_machine_id: str):
    vm = vending_machines.get(vending_machine_id)
    if not vm or not vm["items"]:
        await interaction.response.send_message("自販機または商品が存在しません。", ephemeral=True)
        return

    options = [
        discord.SelectOption(label=data["name"], value=i_id)
        for i_id, data in vm["items"].items()
    ]
    select = discord.ui.Select(placeholder="在庫を追加する商品を選択", options=options)

    async def select_callback(inter: discord.Interaction):
        item_id = select.values[0]
        item = vm["items"][item_id]

        class AddStockModal(discord.ui.Modal, title="在庫内容追加"):
            content = discord.ui.TextInput(
                label="商品内容*", 
                style=discord.TextStyle.paragraph, 
                placeholder="在庫の内容を入力", 
                required=True,
                max_length=1500
            )
            msg = discord.ui.TextInput(
                label="送付用メッセージ", 
                style=discord.TextStyle.paragraph, 
                required=False,
                max_length=300
            )

            async def on_submit(self, m_inter: discord.Interaction):
                if "stock_list" not in item:
                    item["stock_list"] = []
                item["stock_list"].append({"content": self.content.value, "msg": self.msg.value})

                msg_str = f"\n{self.msg.value}" if self.msg.value else ""
                res_text = f"追加した商品は購入時に送信されます:{msg_str}\n```\n{self.content.value}\n```"
                await m_inter.response.send_message(res_text, ephemeral=True)

        await inter.response.send_modal(AddStockModal())

    select.callback = select_callback
    view = discord.ui.View()
    view.add_item(select)
    await interaction.response.send_message("商品を選択してください", view=view, ephemeral=True)


@bot.tree.command(name="在庫内容確認", description="自販機内のすべての在庫を出力")
@app_commands.describe(vending_machine_id="在庫の内容を確認する自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def check_stock(interaction: discord.Interaction, vending_machine_id: str):
    vm = vending_machines.get(vending_machine_id)
    if not vm:
        await interaction.response.send_message("自販機が見つかりません。", ephemeral=True)
        return

    lines = []
    for i_data in vm["items"].values():
        for st in i_data.get("stock_list", []):
            lines.append(f"```\n{st['content']}\n```")

    if not lines:
        await interaction.response.send_message("在庫はありません。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    current_msg = ""
    for line in lines:
        if len(current_msg) + len(line) + 1 > 1900:
            await interaction.followup.send(current_msg, ephemeral=True)
            current_msg = line
        else:
            current_msg += ("\n" + line) if current_msg else line

    if current_msg:
        await interaction.followup.send(current_msg, ephemeral=True)


@bot.tree.command(name="在庫引出", description="指定数の在庫を引き出します")
@app_commands.describe(vending_machine_id="在庫を引き出す自販機", quantity="引き出す個数")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def withdraw_stock(interaction: discord.Interaction, vending_machine_id: str, quantity: int):
    vm = vending_machines.get(vending_machine_id)
    if not vm or not vm["items"]:
        await interaction.response.send_message("自販機または商品がありません。", ephemeral=True)
        return

    options = [
        discord.SelectOption(label=data["name"], value=i_id)
        for i_id, data in vm["items"].items()
    ]
    select = discord.ui.Select(placeholder="引き出す商品を選択", options=options)

    async def select_callback(inter: discord.Interaction):
        item_id = select.values[0]
        item = vm["items"][item_id]
        stock_list = item.get("stock_list", [])

        if len(stock_list) < quantity:
            await inter.response.send_message(f"在庫が足りません。(現在: {len(stock_list)}個)", ephemeral=True)
            return

        drawn = stock_list[:quantity]
        item["stock_list"] = stock_list[quantity:]

        drawn_text = "\n".join([f"```\n{d['content']}\n```" for d in drawn])
        await inter.response.send_message(f"在庫「\n{drawn_text}\n」を引き出しました。", ephemeral=True)

    select.callback = select_callback
    view = discord.ui.View()
    view.add_item(select)
    await interaction.response.send_message("商品を選択してください", view=view, ephemeral=True)


@bot.tree.command(name="クーポン作成", description="クーポンを作成します")
@app_commands.describe(vending_machine_id="クーポンを利用できる自販機", code="クーポンコード", coupon="適用金額")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def create_coupon(interaction: discord.Interaction, vending_machine_id: str, code: str, coupon: int):
    coupons[code] = {"vm_id": vending_machine_id, "amount": coupon}
    await interaction.response.send_message(
        f"クーポンコード「{code}」を作成しました。\n"
        f"利用可能自販機: ```\n{vending_machine_id}\n```\n"
        f"クーポンコード: ```\n{code}\n```\n"
        f"適用金額: ```\n{coupon}\n```",
        ephemeral=True,
    )


@bot.tree.command(name="クーポン一覧", description="利用可能なクーポン一覧を表示")
async def list_coupons(interaction: discord.Interaction):
    if not coupons:
        await interaction.response.send_message("クーポンはありません。", ephemeral=True)
        return

    lines = []
    for code, data in coupons.items():
        lines.append(
            f"利用可能自販機: ```\n{data['vm_id']}\n```\n"
            f"クーポンコード: ```\n{code}\n```\n"
            f"適用金額: ```\n{data['amount']}\n```"
        )

    await interaction.response.send_message("\n\n".join(lines), ephemeral=True)


@bot.tree.command(name="クーポン削除", description="クーポンを削除します")
@app_commands.describe(code="削除するクーポンコード")
@app_commands.autocomplete(code=coupon_autocomplete)
async def delete_coupon(interaction: discord.Interaction, code: str):
    if code not in coupons:
        await interaction.response.send_message("指定されたクーポンが存在しません。", ephemeral=True)
        return

    data = coupons[code]
    embed = discord.Embed(title="本当に削除しますか？\nこの動作は取り消せません。", color=discord.Color.red())
    embed.description = (
        f"利用可能自販機: ```\n{data['vm_id']}\n```\n"
        f"クーポンコード: ```\n{code}\n```\n"
        f"適用金額: ```\n{data['amount']}\n```"
    )

    view = discord.ui.View()
    confirm_btn = discord.ui.Button(label="削除する", style=discord.ButtonStyle.danger)
    cancel_btn = discord.ui.Button(label="キャンセル", style=discord.ButtonStyle.secondary)

    async def confirm_cb(inter: discord.Interaction):
        del coupons[code]
        res_embed = discord.Embed(title="クーポンコードの削除が完了しました。", color=discord.Color.green())
        await inter.response.edit_message(embed=res_embed, view=None)

    async def cancel_cb(inter: discord.Interaction):
        await inter.response.edit_message(content="処理をキャンセルしました。", embed=None, view=None)

    confirm_btn.callback = confirm_cb
    cancel_btn.callback = cancel_cb
    view.add_item(confirm_btn)
    view.add_item(cancel_btn)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ----------------------------------------------------
# 6. 起動処理
# ----------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("エラー: DISCORD_TOKEN が設定されていません。")
