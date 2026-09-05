import os
import json
import asyncio
import uuid
import sys
from flask import Flask
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from paypaypython import PayPay

# --------------------------------------------------
# Webサーバー（Render / Replit等の常時起動用）
# --------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --------------------------------------------------
# 設定・データ保存関連
# --------------------------------------------------
DATA_FILE = "vending_data.json"
TOKEN_FILE = "paypay_tokens.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"vending_machines": {}, "coupons": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def save_tokens(data):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Global variables
paypay_client = None
vending_data = load_data()

# --------------------------------------------------
# Botの初期化
# --------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------------------------------------
# UI コンポーネント（チケット・認証・購入）
# --------------------------------------------------
class TicketView(discord.ui.View):
    def __init__(self, button_label: str, button_style: discord.ButtonStyle):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label=button_label,
            style=button_style,
            custom_id="create_ticket_btn"
        )
        button.callback = self.create_ticket
        self.add_item(button)

    async def create_ticket(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        channel_name = f"ticket-{user.name}"
        channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites)
        
        embed = discord.Embed(
            title="🎫 お問い合わせチケット",
            description=f"{user.mention} 様\nお問い合わせありがとうございます。スタッフの対応をお待ちください。\n用件が終わったら下のボタンでチャンネルを閉じることができます。",
            color=discord.Color.blue()
        )
        
        close_view = discord.ui.View(timeout=None)
        close_button = discord.ui.Button(
            label="🔒┋チケットを閉じる",
            style=discord.ButtonStyle.red,
            custom_id="close_ticket_btn"
        )
        
        async def close_callback(close_interaction: discord.Interaction):
            await close_interaction.response.send_message("このチケットを5秒後に削除します...")
            await asyncio.sleep(5)
            await close_interaction.channel.delete()
            
        close_button.callback = close_callback
        close_view.add_item(close_button)
        
        await channel.send(embed=embed, view=close_view)
        await interaction.response.send_message(f"チケットを作成しました: {channel.mention}", ephemeral=True)


class VerifyView(discord.ui.View):
    def __init__(self, role_id: int, button_label: str, button_style: discord.ButtonStyle):
        super().__init__(timeout=None)
        self.role_id = role_id
        button = discord.ui.Button(
            label=button_label,
            style=button_style,
            custom_id=f"verify_btn_{role_id}"
        )
        button.callback = self.verify
        self.add_item(button)

    async def verify(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role:
            if role in interaction.user.roles:
                await interaction.response.send_message("既に認証済みです。", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"{role.mention} を付与しました！", ephemeral=True)
        else:
            await interaction.response.send_message("指定されたロールが見つかりませんでした。", ephemeral=True)


class PurchaseModal(discord.ui.Modal, title="商品購入手続き"):
    quantity = discord.ui.TextInput(
        label="購入個数",
        placeholder="1",
        default="1",
        required=True
    )
    coupon_code = discord.ui.TextInput(
        label="クーポンコード（任意）",
        placeholder="コードをお持ちの場合は入力してください",
        required=False
    )

    def __init__(self, vm_id: str, product_id: str, payment_type: str):
        super().__init__()
        self.vm_id = vm_id
        self.product_id = product_id
        self.payment_type = payment_type

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(self.quantity.value)
            if qty <= 0:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("購入個数は正の整数で入力してください。", ephemeral=True)
            return

        vm = vending_data["vending_machines"].get(self.vm_id)
        if not vm or self.product_id not in vm["products"]:
            await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
            return

        prod = vm["products"][self.product_id]
        if len(prod["stock"]) < qty:
            await interaction.response.send_message(f"在庫が足りません。現在の在庫: {len(prod['stock'])}個", ephemeral=True)
            return

        unit_price = prod["price"]
        total_price = unit_price * qty
        discount = 0

        code = self.coupon_code.value.strip()
        if code:
            coupons = vending_data.get("coupons", {})
            if code in coupons:
                cp = coupons[code]
                if cp["type"] == "fixed":
                    discount = cp["value"]
                elif cp["type"] == "percent":
                    discount = int(total_price * (cp["value"] / 100))
                total_price = max(0, total_price - discount)
            else:
                await interaction.response.send_message("無効なクーポンコードです。", ephemeral=True)
                return

        session_id = str(uuid.uuid4())
        
        embed = discord.Embed(
            title="💳 PayPay決済手続き",
            description=f"以下の手順で決済を完了してください。\n\n"
                        f"**商品名:** {prod['name']}\n"
                        f"**数量:** {qty}個\n"
                        f"**種別:** {'PayPayマネー' if self.payment_type == 'MONEY' else 'PayPayマネーライト'}\n"
                        f"**合計金額:** {total_price}円 (割引: {discount}円)\n\n"
                        f"**【支払方法】**\n"
                        f"1. PayPayアプリで **{total_price}円** の送金リンク（受け取りリンク）を作成してください。\n"
                        f"2. 下の「送金リンクを入力」ボタンを押し、リンク（およびパスコード）を入力してください。",
            color=discord.Color.blue()
        )

        view = PaymentInputView(
            vm_id=self.vm_id,
            product_id=self.product_id,
            qty=qty,
            expected_amount=total_price,
            payment_type=self.payment_type
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class PaymentInputModal(discord.ui.Modal, title="PayPay送金リンク送信"):
    link = discord.ui.TextInput(
        label="PayPay送金リンク",
        placeholder="https://paypay.me/...",
        required=True
    )
    passcode = discord.ui.TextInput(
        label="パスコード（設定している場合）",
        placeholder="4桁の数字",
        required=False
    )

    def __init__(self, vm_id: str, product_id: str, qty: int, expected_amount: int, payment_type: str):
        super().__init__()
        self.vm_id = vm_id
        self.product_id = product_id
        self.qty = qty
        self.expected_amount = expected_amount
        self.payment_type = payment_type

    async def on_submit(self, interaction: discord.Interaction):
        global paypay_client
        if not paypay_client:
            await interaction.response.send_message("PayPay連携が設定されていません。管理者に連絡してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        link_val = self.link.value.strip()
        pass_val = self.passcode.value.strip() if self.passcode.value else None

        try:
            link_info = paypay_client.get_link_info(link_val)
            
            # 金額チェック
            amount = link_info.get("amount", 0)
            if amount < self.expected_amount:
                await interaction.followup.send(f"金額が不足しています。必要額: {self.expected_amount}円, 送金額: {amount}円", ephemeral=True)
                return

            # 受け取り実行
            paypay_client.receive_link(link_val, passcode=pass_val)

            # 在庫引き落とし＆DM送信
            vm = vending_data["vending_machines"][self.vm_id]
            prod = vm["products"][self.product_id]
            
            items = prod["stock"][:self.qty]
            prod["stock"] = prod["stock"][self.qty:]
            save_data(vending_data)

            formatted_items = []
            for item in items:
                if item.startswith("{{") and item.endswith("}}"):
                    formatted_items.append(f"```\n{item[2:-2]}\n```")
                elif item.startswith("{") and item.endswith("}"):
                    formatted_items.append(f"`{item[1:-1]}`")
                else:
                    formatted_items.append(item)

            dm_text = f"🎁 **ご購入ありがとうございます！**\n\n**商品名:** {prod['name']}\n**個数:** {self.qty}\n\n**【納品内容】**\n" + "\n".join(formatted_items)
            
            try:
                await interaction.user.send(dm_text)
                await interaction.followup.send("決済が完了し、DMに商品を送信しました！ご確認ください。", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("決済は完了しましたが、DMの送信に失敗しました。設定を確認の上サポートにお問い合わせください。", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"処理中にエラーが発生しました: {str(e)}", ephemeral=True)


class PaymentInputView(discord.ui.View):
    def __init__(self, vm_id: str, product_id: str, qty: int, expected_amount: int, payment_type: str):
        super().__init__(timeout=300)
        self.vm_id = vm_id
        self.product_id = product_id
        self.qty = qty
        self.expected_amount = expected_amount
        self.payment_type = payment_type

    @discord.ui.button(label="💳 送金リンクを入力", style=discord.ButtonStyle.green)
    async def enter_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PaymentInputModal(
            vm_id=self.vm_id,
            product_id=self.product_id,
            qty=self.qty,
            expected_amount=self.expected_amount,
            payment_type=self.payment_type
        )
        await interaction.response.send_modal(modal)


class PaymentTypeSelect(discord.ui.Select):
    def __init__(self, vm_id: str, product_id: str):
        self.vm_id = vm_id
        self.product_id = product_id
        options = [
            discord.SelectOption(label="PayPayマネー", value="MONEY", description="現金化可能なPayPay残高"),
            discord.SelectOption(label="PayPayマネーライト", value="MONEY_LITE", description="マネーライト残高"),
        ]
        super().__init__(placeholder="決済種別を選択してください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        modal = PurchaseModal(self.vm_id, self.product_id, self.values[0])
        await interaction.response.send_modal(modal)


class ProductSelect(discord.ui.Select):
    def __init__(self, vm_id: str):
        self.vm_id = vm_id
        vm = vending_data["vending_machines"].get(vm_id, {})
        products = vm.get("products", {})

        options = []
        for pid, pdata in products.items():
            stock_count = len(pdata["stock"])
            desc = f"価格: {pdata['price']}円 | 在庫: {stock_count}個"
            options.append(discord.SelectOption(label=pdata["name"], value=pid, description=desc))

        if not options:
            options.append(discord.SelectOption(label="現在商品がありません", value="none"))

        super().__init__(placeholder="購入する商品を選択してください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("現在選択できる商品はありません。", ephemeral=True)
            return

        view = discord.ui.View(timeout=180)
        view.add_item(PaymentTypeSelect(self.vm_id, self.values[0]))
        await interaction.response.send_message("決済種別を選択してください:", view=view, ephemeral=True)


class VendingMachineView(discord.ui.View):
    def __init__(self, vm_id: str):
        super().__init__(timeout=None)
        self.vm_id = vm_id
        button = discord.ui.Button(
            label="🛒 購入する",
            style=discord.ButtonStyle.green,
            custom_id=f"vending_buy_{vm_id}"
        )
        button.callback = self.buy_button
        self.add_item(button)

    async def buy_button(self, interaction: discord.Interaction):
        view = discord.ui.View(timeout=180)
        view.add_item(ProductSelect(self.vm_id))
        await interaction.response.send_message("商品を選択してください:", view=view, ephemeral=True)


# --------------------------------------------------
# スラッシュコマンド群
# --------------------------------------------------

# 1. チケット設置
@bot.tree.command(name="ticket", description="チケット発行パネルを設置します")
@app_commands.describe(
    title="パネルのタイトル",
    description="パネルの説明文",
    buttonlabel="ボタンに表示する文字",
    buttoncolor="ボタンの色 (blue/green/red/grey)"
)
async def ticket_cmd(
    interaction: discord.Interaction,
    title: str = "🎟️ お問い合わせパネル",
    description: str = "下のボタンを押してチケットを作成してください。",
    buttonlabel: str = "チケットを作成",
    buttoncolor: str = "blue"
):
    color_map = {
        "blue": discord.ButtonStyle.primary,
        "green": discord.ButtonStyle.success,
        "red": discord.ButtonStyle.danger,
        "grey": discord.ButtonStyle.secondary,
    }
    style = color_map.get(buttoncolor.lower(), discord.ButtonStyle.primary)

    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    view = TicketView(button_label=buttonlabel, button_style=style)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("チケットパネルを設置しました。", ephemeral=True)


# 2. 認証設置
@bot.tree.command(name="verify", description="認証パネルを設置します")
@app_commands.describe(
    role="付与するロール",
    title="パネルのタイトル",
    description="パネルの説明文",
    buttonlabel="ボタンに表示する文字",
    buttoncolor="ボタンの色 (blue/green/red/grey)"
)
async def verify_cmd(
    interaction: discord.Interaction,
    role: discord.Role,
    title: str = "✅ 認証パネル",
    description: str = "下のボタンを押して認証を完了してください。",
    buttonlabel: str = "認証する",
    buttoncolor: str = "green"
):
    color_map = {
        "blue": discord.ButtonStyle.primary,
        "green": discord.ButtonStyle.success,
        "red": discord.ButtonStyle.danger,
        "grey": discord.ButtonStyle.secondary,
    }
    style = color_map.get(buttoncolor.lower(), discord.ButtonStyle.success)

    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    view = VerifyView(role_id=role.id, button_label=buttonlabel, button_style=style)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("認証パネルを設置しました。", ephemeral=True)


# 3. PayPayログイン
@bot.tree.command(name="paypay_login", description="PayPayアカウントにログインします（管理用）")
@app_commands.describe(phone="電話番号", password="パスワード")
async def paypay_login_cmd(interaction: discord.Interaction, phone: str, password: str):
    global paypay_client
    await interaction.response.defer(ephemeral=True)

    try:
        client = PayPay(phone=phone, password=password)
        otp_prompt = client.login()

        modal = PayPayOTPModal(client=client)
        # Deferred なので Followupで受け取りモーダルを開く誘導または別処理
        await interaction.followup.send("SMSに認証コードを送信しました。下のボタンを押してコードを入力してください。", view=PayPayOTPView(client))
    except Exception as e:
        await interaction.followup.send(f"ログイン初期化エラー: {str(e)}", ephemeral=True)


class PayPayOTPModal(discord.ui.Modal, title="SMS認証コード入力"):
    otp = discord.ui.TextInput(label="認証コード (6桁)", placeholder="123456", required=True)

    def __init__(self, client: PayPay):
        super().__init__()
        self.client = client

    async def on_submit(self, interaction: discord.Interaction):
        global paypay_client
        await interaction.response.defer(ephemeral=True)
        try:
            self.client.verify_otp(self.otp.value.strip())
            paypay_client = self.client
            save_tokens({
                "access_token": client.access_token,
                "refresh_token": client.refresh_token,
                "client_uuid": client.client_uuid
            })
            await interaction.followup.send("PayPayログイン成功！トークンを保存しました。", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"認証失敗: {str(e)}", ephemeral=True)


class PayPayOTPView(discord.ui.View):
    def __init__(self, client: PayPay):
        super().__init__(timeout=180)
        self.client = client

    @discord.ui.button(label="🔑 認証コードを入力する", style=discord.ButtonStyle.primary)
    async def open_otp_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PayPayOTPModal(self.client))


# 4. 自販機管理
@bot.tree.command(name="自販機作成", description="新しい自販機を作成します")
async def create_vm(interaction: discord.Interaction, name: str):
    vm_id = str(uuid.uuid4())[:8]
    vending_data["vending_machines"][vm_id] = {
        "name": name,
        "products": {}
    }
    save_data(vending_data)
    await interaction.response.send_message(f"自販機 '{name}' を作成しました。(ID: `{vm_id}`)", ephemeral=True)


@bot.tree.command(name="自販機設置", description="指定した自販機パネルを設置します")
async def place_vm(interaction: discord.Interaction, vending_machine_id: str):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm:
        await interaction.response.send_message("自販機が見つかりません。", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🛒 {vm['name']}",
        description="下の「購入する」ボタンを押して商品を選択・購入できます。",
        color=discord.Color.blue()
    )
    view = VendingMachineView(vending_machine_id)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("自販機パネルを設置しました。", ephemeral=True)


@bot.tree.command(name="自販機削除", description="自販機を削除します")
async def delete_vm(interaction: discord.Interaction, vending_machine_id: str):
    if vending_machine_id in vending_data["vending_machines"]:
        del vending_data["vending_machines"][vending_machine_id]
        save_data(vending_data)
        await interaction.response.send_message("自販機を削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message("自販機が見つかりません。", ephemeral=True)


# 5. 商品管理
@bot.tree.command(name="商品追加", description="自販機に新しい商品を追加します")
async def add_product(interaction: discord.Interaction, vending_machine_id: str, name: str, price: int):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm:
        await interaction.response.send_message("自販機が見つかりません。", ephemeral=True)
        return

    product_id = str(uuid.uuid4())[:8]
    vm["products"][product_id] = {
        "name": name,
        "price": price,
        "stock": []
    }
    save_data(vending_data)
    await interaction.response.send_message(f"商品 '{name}' ({price}円) を追加しました。(商品ID: `{product_id}`)", ephemeral=True)


@bot.tree.command(name="商品内容変更", description="商品の名前や価格を変更します")
async def edit_product(interaction: discord.Interaction, vending_machine_id: str, product_id: str, new_name: str = None, new_price: int = None):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm or product_id not in vm["products"]:
        await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
        return

    prod = vm["products"][product_id]
    if new_name:
        prod["name"] = new_name
    if new_price is not None:
        prod["price"] = new_price

    save_data(vending_data)
    await interaction.response.send_message("商品内容を更新しました。", ephemeral=True)


@bot.tree.command(name="商品削除", description="商品を削除します")
async def delete_product(interaction: discord.Interaction, vending_machine_id: str, product_id: str):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm or product_id not in vm["products"]:
        await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
        return

    del vm["products"][product_id]
    save_data(vending_data)
    await interaction.response.send_message("商品を削除しました。", ephemeral=True)


# 6. 在庫管理
@bot.tree.command(name="在庫追加", description="商品に在庫を追加します")
async def add_stock(interaction: discord.Interaction, vending_machine_id: str, product_id: str, item: str):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm or product_id not in vm["products"]:
        await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
        return

    vm["products"][product_id]["stock"].append(item)
    save_data(vending_data)
    await interaction.response.send_message(f"在庫を追加しました。現在の在庫数: {len(vm['products'][product_id]['stock'])}", ephemeral=True)


@bot.tree.command(name="在庫内容確認", description="現在の全在庫を出力します")
async def check_stock(interaction: discord.Interaction, vending_machine_id: str, product_id: str):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm or product_id not in vm["products"]:
        await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
        return

    stock = vm["products"][product_id]["stock"]
    if not stock:
        await interaction.response.send_message("在庫は空です。", ephemeral=True)
        return

    text = f"📦 **{vm['products'][product_id]['name']} の在庫一覧 ({len(stock)}個):**\n"
    for idx, s in enumerate(stock, 1):
        text += f"{idx}. `{s}`\n"

    await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(name="在庫引出", description="指定数の在庫を削除/引き出します")
async def pull_stock(interaction: discord.Interaction, vending_machine_id: str, product_id: str, count: int):
    vm = vending_data["vending_machines"].get(vending_machine_id)
    if not vm or product_id not in vm["products"]:
        await interaction.response.send_message("商品が見つかりません。", ephemeral=True)
        return

    stock = vm["products"][product_id]["stock"]
    if len(stock) < count:
        await interaction.response.send_message(f"引き出せる在庫数が不足しています。(現在: {len(stock)}個)", ephemeral=True)
        return

    pulled = stock[:count]
    vm["products"][product_id]["stock"] = stock[count:]
    save_data(vending_data)

    await interaction.response.send_message(f"在庫を {count} 個引き出しました:\n" + "\n".join(pulled), ephemeral=True)


# 7. クーポン管理
@bot.tree.command(name="クーポン作成", description="新しいクーポンを発行します")
@app_commands.choices(coupon_type=[
    app_commands.Choice(name="固定額割引 (円)", value="fixed"),
    app_commands.Choice(name="割合割引 (%)", value="percent")
])
async def create_coupon(interaction: discord.Interaction, code: str, coupon_type: app_commands.Choice[str], value: int):
    if "coupons" not in vending_data:
        vending_data["coupons"] = {}

    vending_data["coupons"][code] = {
        "type": coupon_type.value,
        "value": value
    }
    save_data(vending_data)
    await interaction.response.send_message(f"クーポン `{code}` を作成しました。({coupon_type.name}: {value})", ephemeral=True)


@bot.tree.command(name="クーポン一覧", description="登録されているクーポン一覧を表示します")
async def list_coupons(interaction: discord.Interaction):
    coupons = vending_data.get("coupons", {})
    if not coupons:
        await interaction.response.send_message("有効なクーポンはありません。", ephemeral=True)
        return

    text = "🏷️ **クーポン一覧:**\n"
    for code, info in coupons.items():
        unit = "円引き" if info["type"] == "fixed" else "%引き"
        text += f"・`{code}`: {info['value']}{unit}\n"

    await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(name="クーポン削除", description="クーポンを削除します")
async def delete_coupon(interaction: discord.Interaction, code: str):
    coupons = vending_data.get("coupons", {})
    if code in coupons:
        del coupons[code]
        save_data(vending_data)
        await interaction.response.send_message(f"クーポン `{code}` を削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message("指定されたクーポンは見つかりません。", ephemeral=True)


# --------------------------------------------------
# Bot 起動・初期化処理（Cogの読み込みとPayPay自動ログイン）
# --------------------------------------------------
@bot.event
async def setup_hook():
    # cogs フォルダ内の help.py (別ファイルで作ったヘ ルプ機能) を読み込み
    try:
        await bot.load_extension("cogs.help")
        print("cogs.help の読み込みに成功しました。")
    except Exception as e:
        print(f"cogs.help の読み込みに失敗しました: {e}")


@bot.event
async def on_ready():
    global paypay_client

    # PayPayトークンの復元
    saved_tokens = load_tokens()
    if saved_tokens and saved_tokens.get("refresh_token"):
        try:
            paypay_client = PayPay(
                access_token=saved_tokens.get("access_token"),
                refresh_token=saved_tokens.get("refresh_token"),
                client_uuid=saved_tokens.get("client_uuid")
            )
            paypay_client.refresh_access_token()
            print("PayPayセッションの自動復元に成功しました。")
        except Exception as e:
            print(f"PayPayセッションの復元に失敗しました: {e}")

    # スラッシュコマンドの同期
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} 個のスラッシュコマンドを同期しました。")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

    print(f"Bot ログイン完了: {bot.user}")


# --------------------------------------------------
# エントリーポイント
# --------------------------------------------------
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("エラー: DISCORD_TOKEN が環境変数に設定されていません。")
        sys.exit(1)
    bot.run(token)
