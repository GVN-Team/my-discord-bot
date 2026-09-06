import os
import uuid
import re
import json
import io
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask

from paypay import PayPay, PayPayError, PayPayLoginError, PayPayNetWorkError, load_tokens

app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    Thread(target=run).start()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

vending_machines = {}
coupons = {}
paypay_client = None

def parse_color(color_hex: str) -> discord.Color:
    match = re.search(r"^#?([0-9a-fA-F]{6})$", color_hex)
    if match:
        return discord.Color(int(match.group(1), 16))
    return discord.Color(0x5865F2)

def format_stock_item(raw_content: str) -> str:
    def repl_double(match):
        return f"```\n{match.group(1)}\n```"

    def repl_single(match):
        return f"`{match.group(1)}`"

    result = re.sub(r"\{\{(.*?)\}\}", repl_double, raw_content, flags=re.DOTALL)
    result = re.sub(r"\{(.*?)\}", repl_single, result, flags=re.DOTALL)

    if "{" not in raw_content and "}" not in raw_content:
        return f"`{raw_content}`"

    return result

class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="🔒┋チケットを閉じる", custom_id="close_ticket_btn")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("このチケットチャンネルを削除します...", ephemeral=True)
        await interaction.channel.delete()

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton())

class TicketButton(discord.ui.Button):
    def __init__(self, label: str, button_color: str):
        style = discord.ButtonStyle.primary
        if button_color and button_color.startswith("#"):
            style = discord.ButtonStyle.primary

        super().__init__(style=style, label=label, custom_id="create_ticket_btn")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user

        channel_name = f"ticket-{user.name}"
        existing_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if existing_channel:
            await interaction.response.send_message(f"⚠️ すでにチケットチャンネルが存在します: {existing_channel.mention}", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        category = interaction.channel.category
        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites, category=category)

        embed = discord.Embed(
            title="お問い合わせチケット",
            description=f"{user.mention} 様\nお問い合わせ内容を入力してお待ちください。\n対応が完了したら「チケットを閉じる」ボタンを押してください。",
            color=discord.Color.blue()
        )

        await ticket_channel.send(embed=embed, view=TicketCloseView())
        await interaction.response.send_message(f"✅ チケットを作成しました: {ticket_channel.mention}", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self, label: str, button_color: str):
        super().__init__(timeout=None)
        self.add_item(TicketButton(label, button_color))

class VerifyButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str):
        super().__init__(style=discord.ButtonStyle.primary, label=label, custom_id=f"verify_btn_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ 設定されたロールが見つかりませんでした。", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.response.send_message("⚠️ 既に認証済みです（ロールを所有しています）。", ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ 認証が完了しました！ **{role.name}** を付与しました。", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Botの権限が不足しているため、ロールを付与できませんでした。Botのロール順位を確認してください。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ エラーが発生しました: {e}", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self, role_id: int, label: str):
        super().__init__(timeout=None)
        self.add_item(VerifyButton(role_id, label))

class MainHelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="チケット機能", value="ticket", description="問い合わせパネルの設置・運用", emoji="🎟️"),
            discord.SelectOption(label="認証機能", value="verify", description="ロール付与認証パネルの設置", emoji="✅"),
            discord.SelectOption(label="PayPay連携", value="paypay", description="PayPayアカウント連携・全自動決済", emoji="💳"),
            discord.SelectOption(label="自販機・商品管理", value="vending", description="自販機作成・設置・商品登録・削除", emoji="🛒"),
            discord.SelectOption(label="在庫管理", value="stock", description="在庫の追加・内容確認・引き出し", emoji="📦"),
            discord.SelectOption(label="クーポン管理", value="coupon", description="割引クーポンの作成・一覧・削除", emoji="🏷️"),
            discord.SelectOption(label="データ保存・復元", value="save_load", description="データのセーブ・ロード", emoji="💾"),
        ]
        super().__init__(placeholder="詳しく知りたい機能を選択してください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        embeds = {
            "ticket": discord.Embed(
                title="🎟️ チケット機能の詳細",
                description="ユーザー専用の問い合わせチャンネルを作成・管理する機能です。\n\n"
                            "**【コマンド】**\n"
                            "`/ticket [title] [description] [buttonlabel] [buttoncolor]`\n"
                            "・チケット発行パネルを設置します。\n"
                            "・「🔒┋チケットを閉じる」ボタンで作成されたチャンネルを削除します。",
                color=discord.Color.blue()
            ),
            "verify": discord.Embed(
                title="✅ 認証機能の詳細",
                description="サーバー参加者にロールを自動付与する認証パネルを作成します。\n\n"
                            "**【コマンド】**\n"
                            "`/verify <role> [title] [description] [buttonlabel] [buttoncolor]`\n"
                            "・指定したロールを付与する認証パネルを設置します。",
                color=discord.Color.green()
            ),
            "paypay": discord.Embed(
                title="💳 PayPay連携の詳細",
                description="PayPayでの自動決済を行うためのアカウント認証を行います。\n\n"
                            "**【コマンド】**\n"
                            "`/paypay_login <phone> <password>`\n"
                            "・初回のみ実行が必要です。SMS認証コードを入力するとトークンが自動保存されます。",
                color=discord.Color.red()
            ),
            "vending": discord.Embed(
                title="🛒 自販機・商品管理の詳細",
                description="自動販売機パネルの管理と商品の登録・変更を行います。\n\n"
                            "**【コマンド】**\n"
                            "・`/自販機作成 <name>` : 新しい自販機を作成します。\n"
                            "・`/自販機設置 <vending_machine_id>` : 自販機パネルを送信します。\n"
                            "・`/自販機削除 <vending_machine_id>` : 自販機を削除します。\n"
                            "・`/商品追加 ...` : 自販機に商品を登録します。\n"
                            "・`/商品内容変更 ...` : 価格や名前を変更します。\n"
                            "・`/商品削除 ...` : 商品を削除します。",
                color=discord.Color.gold()
            ),
            "stock": discord.Embed(
                title="📦 在庫管理の詳細",
                description="自販機に納品する在庫データを管理します。\n\n"
                            "**【コマンド】**\n"
                            "・`/在庫追加 <vending_machine_id>` : 商品に在庫を追加します。\n"
                            "  ※ `{内容}` でインラインコード枠、`{{内容}}` でコードブロック枠でDM送信されます。\n"
                            "・`/在庫内容確認 <vending_machine_id>` : 全在庫を出力します。\n"
                            "・`/在庫引出 <vending_machine_id> <quantity>` : 在庫を指定数引き出します。",
                color=discord.Color.orange()
            ),
            "coupon": discord.Embed(
                title="🏷️ クーポン管理の詳細",
                description="自販機で使える割引クーポンを発行・管理します。\n\n"
                            "**【コマンド】**\n"
                            "・`/クーポン作成 ...` : 割引クーポンを作成します。\n"
                            "・`/クーポン一覧` : 有効なクーポン一覧を表示します。\n"
                            "・`/クーポン削除 ...` : クーポンを削除します。",
                color=discord.Color.purple()
            ),
            "save_load": discord.Embed(
                title="💾 データ保存・復元の詳細",
                description="再デプロイ等でBotのデータが消えるのを防ぐ機能です。\n\n"
                            "**【コマンド】**\n"
                            "・`/save` : 自販機・在庫・売上データをインラインコード文字列で出力します。\n"
                            "・`/load <data_text>` : 出力されたテキストを入力してデータを復元します。",
                color=discord.Color.teal()
            ),
        }
        await interaction.response.send_message(embed=embeds[val], ephemeral=True)

class MainHelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MainHelpSelect())

class MemberHelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🛒 商品の購入手順", value="how_to_buy", description="自販機から商品を購入する流れ", emoji="🛒"),
            discord.SelectOption(label="💳 決済・PayPay送金", value="payment_info", description="PayPayマネー / マネーライトの支払い方", emoji="💳"),
            discord.SelectOption(label="🏷️ クーポンの使い方", value="coupon_info", description="割引クーポンの利用方法", emoji="🏷️"),
            discord.SelectOption(label="📩 商品の受取方法", value="dm_info", description="購入後のDM受取・確認方法", emoji="📩"),
            discord.SelectOption(label="❓ よくある質問・FAQ", value="faq", description="エラーや困ったときの対処法", emoji="❓"),
        ]
        super().__init__(placeholder="知りたい項目を選択してください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        embeds = {
            "how_to_buy": discord.Embed(
                title="🛒 商品の購入手順",
                description="1. 自販機パネルの **「🛒購入する」** ボタンを押します。\n"
                            "2. メニューから欲しい商品を選択します。\n"
                            "3. 「マネー」または「マネーライト」を選択します。\n"
                            "4. 購入個数とクーポンコード（持っている場合）を入力します。\n"
                            "5. 計算画面で金額を確認し、PayPay送金リンクを入力して購入を完了します。",
                color=discord.Color.green()
            ),
            "payment_info": discord.Embed(
                title="💳 決済・PayPay送金について",
                description="・PayPayアプリで指定された合計金額の **送金リンク** を作成してください。\n"
                            "・パスコードを設定した場合は、入力画面でパスコードも入力してください。\n"
                            "・金額不足の場合はエラーが表示されますので、不足分を再度作成してください。",
                color=discord.Color.blue()
            ),
            "coupon_info": discord.Embed(
                title="🏷️ クーポンの使い方",
                description="・購入個数の入力画面に **「クーポンコード」** 入力欄があります。\n"
                            "・コードをお持ちの場合は入力して送信すると、自動的に割引価格で計算されます。",
                color=discord.Color.purple()
            ),
            "dm_info": discord.Embed(
                title="📩 商品の受取方法",
                description="・決済完了後、Botから **ダイレクトメッセージ（DM）** で商品内容が即時送付されます。\n"
                            "・DMが届かない場合は、プライバシー設定で **「サーバーメンバーからのDMを許可する」** がオンになっているか確認してください。",
                color=discord.Color.gold()
            ),
            "faq": discord.Embed(
                title="❓ よくある質問・トラブルシューティング",
                description="**Q. DMが届きません**\n"
                            "A. DMの受信許可設定をオンにしてから、サポートチケット等でお問い合わせください。\n\n"
                            "**Q. 「金額が足りません」と表示されます**\n"
                            "A. クーポン適用後の必要金額に達しているか確認し、正しい金額のリンクを発行してください。\n\n"
                            "**Q. 「在庫が足りません」と表示されます**\n"
                            "A. 申し訳ありません。現在在庫切れです。管理者による補充をお待ちください。",
                color=discord.Color.red()
            ),
        }
        await interaction.response.send_message(embed=embeds[val])

class MemberHelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MemberHelpSelect())

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

async def deliver_items_to_dm(interaction: discord.Interaction, v_id: str, item_id: str, qty: int) -> bool:
    item = vending_machines.get(v_id, {}).get("items", {}).get(item_id)
    if not item:
        await interaction.followup.send("商品が見つかりませんでした。", ephemeral=True)
        return False

    stock_list = item.get("stock_list", [])

    if item["type"] == "有限":
        drawn = stock_list[:qty]
        item["stock_list"] = stock_list[qty:]
    else:
        drawn = [stock_list[0]] * qty if stock_list else []

    # 売上数を加算
    item["sold_count"] = item.get("sold_count", 0) + qty

    delivery_blocks = []
    for d in drawn:
        content_str = d if isinstance(d, str) else d.get("content", "")
        delivery_blocks.append(format_stock_item(content_str))

    # 空行（2回改行）をなくしたDM整形
    dm_text = (
        "# **✅購入が完了しました**\n"
        "```\nご購入ありがとうございます\n```\n"
        f"```\n商品:{item['name']}\n```\n"
        + "\n".join(delivery_blocks)
    )

    try:
        await interaction.user.send(dm_text)
        return True
    except discord.Forbidden:
        await interaction.followup.send("❌ DMの送信に失敗しました。DMの受取許可設定を確認してください。", ephemeral=True)
        return False

class PayPayConfirmView(discord.ui.View):
    def __init__(self, v_id: str, item_id: str, qty: int, paypay_url: str, passcode: str, sent_amount: int):
        super().__init__(timeout=None)
        self.v_id = v_id
        self.item_id = item_id
        self.qty = qty
        self.paypay_url = paypay_url
        self.passcode = passcode
        self.sent_amount = sent_amount

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.success)
    async def confirm_cb(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        item = vending_machines.get(self.v_id, {}).get("items", {}).get(self.item_id)
        if not item:
            await interaction.followup.send("商品が見つかりませんでした。", ephemeral=True)
            return

        if item["type"] == "有限":
            stock_list = item.get("stock_list", [])
            if len(stock_list) < self.qty:
                await interaction.followup.send(f"在庫が足りません。(現在在庫: {len(stock_list)}個)", ephemeral=True)
                return

        try:
            paypay_client.link_receive(url=self.paypay_url, password=self.passcode)
        except PayPayLoginError:
            await interaction.followup.send("❌ 認証情報の完全自動更新に失敗しました。`/paypay_login` で1度再ログインしてください。", ephemeral=True)
            return
        except PayPayError as e:
            await interaction.followup.send(f"❌ PayPay処理エラー: {e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ 決済失敗: {e}", ephemeral=True)
            return

        success = await deliver_items_to_dm(interaction, self.v_id, self.item_id, self.qty)
        if success:
            await interaction.edit_original_response(content="✅商品をDMに送信しました！", view=None)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.danger)
    async def cancel_cb(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="購入をキャンセルしました。", view=None)

class PayPayPaymentModal(discord.ui.Modal, title="PayPay決済"):
    def __init__(self, v_id: str, item_id: str, payment_method: str, qty: int, final_price: int, unit_price: int):
        super().__init__()
        self.v_id = v_id
        self.item_id = item_id
        self.payment_method = payment_method
        self.qty = qty
        self.final_price = final_price
        self.unit_price = unit_price

        self.paypay_link = discord.ui.TextInput(
            label="PayPayリンク",
            placeholder="https://pay.paypay.ne.jp/...",
            required=True
        )
        self.passcode = discord.ui.TextInput(
            label="パスワード",
            placeholder="4桁の数字",
            required=False,
            max_length=4
        )
        self.add_item(self.paypay_link)
        self.add_item(self.passcode)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        item = vending_machines.get(self.v_id, {}).get("items", {}).get(self.item_id)
        if not item:
            await interaction.followup.send("商品が見つかりませんでした。", ephemeral=True)
            return

        if item["type"] == "有限":
            stock_list = item.get("stock_list", [])
            if len(stock_list) < self.qty:
                await interaction.followup.send(f"在庫が足りません。(現在在庫: {len(stock_list)}個)", ephemeral=True)
                return

        if not paypay_client:
            await interaction.followup.send("PayPay連携が初期化されていません。`/paypay_login` を1度実行してください。", ephemeral=True)
            return

        pass_code = self.passcode.value if self.passcode.value else None

        try:
            link_info = paypay_client.link_check(self.paypay_link.value)
            sent_amount = link_info.money if self.payment_method == "マネー" else link_info.money_light

            if sent_amount < self.final_price:
                diff = self.final_price - sent_amount
                await interaction.followup.send(f"{diff}円足りません。もう一度SMSリンクを作成して送信してください。", ephemeral=True)
                return

            calc_result = (self.unit_price * self.qty) - sent_amount
            confirm_str = f"`{self.unit_price}×{self.qty}-{sent_amount}={calc_result}`"

            view = PayPayConfirmView(
                v_id=self.v_id,
                item_id=self.item_id,
                qty=self.qty,
                paypay_url=self.paypay_link.value,
                passcode=pass_code,
                sent_amount=sent_amount
            )

            await interaction.followup.send(confirm_str, view=view, ephemeral=True)

        except PayPayLoginError:
            await interaction.followup.send("❌ 認証情報の完全自動更新に失敗しました。`/paypay_login` で1度再ログインしてください。", ephemeral=True)
        except PayPayError as e:
            await interaction.followup.send(f"❌ PayPay処理エラー: {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 決済失敗: {e}", ephemeral=True)

class ConfirmPurchaseView(discord.ui.View):
    def __init__(self, v_id: str, item_id: str, payment_method: str, qty: int, final_price: int, unit_price: int):
        super().__init__(timeout=None)
        self.v_id = v_id
        self.item_id = item_id
        self.payment_method = payment_method
        self.qty = qty
        self.final_price = final_price
        self.unit_price = unit_price

    @discord.ui.button(label="購入する", style=discord.ButtonStyle.success)
    async def confirm_cb(self, interaction: discord.Interaction, button: discord.ui.Button):
        item = vending_machines.get(self.v_id, {}).get("items", {}).get(self.item_id)
        if not item:
            await interaction.response.send_message("商品が見つかりませんでした。", ephemeral=True)
            return

        if item["type"] == "有限":
            stock_list = item.get("stock_list", [])
            if len(stock_list) < self.qty:
                await interaction.response.send_message(f"在庫が足りません。(現在在庫: {len(stock_list)}個)", ephemeral=True)
                return

        if self.final_price == 0:
            await interaction.response.defer(ephemeral=True)
            success = await deliver_items_to_dm(interaction, self.v_id, self.item_id, self.qty)
            if success:
                await interaction.edit_original_response(content="✅商品をDMに送信しました！", view=None)
        else:
            await interaction.response.send_modal(
                PayPayPaymentModal(self.v_id, self.item_id, self.payment_method, self.qty, self.final_price, self.unit_price)
            )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.danger)
    async def cancel_cb(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="購入をキャンセルしました。", view=None)

class QuantityCouponModal(discord.ui.Modal, title="個数とクーポン入力"):
    def __init__(self, v_id: str, item_id: str, payment_method: str):
        super().__init__()
        self.v_id = v_id
        self.item_id = item_id
        self.payment_method = payment_method

        self.quantity = discord.ui.TextInput(
            label="個数",
            placeholder="1",
            default="1",
            required=True
        )
        self.coupon_code = discord.ui.TextInput(
            label="クーポンコード",
            placeholder="コードをお持ちの場合は入力",
            required=False
        )
        self.add_item(self.quantity)
        self.add_item(self.coupon_code)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(self.quantity.value)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("購入個数は1以上の整数で入力してください。", ephemeral=True)
            return

        item = vending_machines.get(self.v_id, {}).get("items", {}).get(self.item_id)
        if not item:
            await interaction.response.send_message("商品が見つかりませんでした。", ephemeral=True)
            return

        unit_price = item["money"] if self.payment_method == "マネー" else item["manera"]
        base_total = unit_price * qty
        discount = 0

        c_code = self.coupon_code.value.strip() if self.coupon_code.value else None
        if c_code and c_code in coupons:
            c_info = coupons[c_code]
            if c_info["vm_id"] == self.v_id:
                discount = c_info["amount"]

        final_price = max(0, base_total - discount)
        calc_str = f"`{unit_price}×{qty}-{discount}={final_price}`"

        view = ConfirmPurchaseView(
            v_id=self.v_id,
            item_id=self.item_id,
            payment_method=self.payment_method,
            qty=qty,
            final_price=final_price,
            unit_price=unit_price
        )

        await interaction.response.send_message(calc_str, view=view, ephemeral=True)

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
        await interaction.response.send_modal(QuantityCouponModal(self.v_id, self.item_id, self.values[0]))

class EditItemModal(discord.ui.Modal, title="商品内容変更"):
    def __init__(self, v_id: str, item_id: str, current_item: dict):
        super().__init__()
        self.v_id = v_id
        self.item_id = item_id

        self.item_name = discord.ui.TextInput(label="商品名", default=str(current_item["name"]), required=True)
        self.description = discord.ui.TextInput(label="説明文", default=str(current_item.get("description", "") or ""), required=False)
        self.item_type = discord.ui.TextInput(label="タイプ(有限/無限)", default=str(current_item["type"]), required=True)
        self.money = discord.ui.TextInput(label="マネー", default=str(current_item["money"]), required=True)
        self.manera = discord.ui.TextInput(label="マネーライト", default=str(current_item["manera"]), required=True)

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

        view = discord.ui.View(timeout=None)
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

class VendingView(discord.ui.View):
    def __init__(self, vending_machine_id: str):
        super().__init__(timeout=None)
        self.vending_machine_id = vending_machine_id

    # 黄緑色（Success）の購入ボタン
    @discord.ui.button(label="🛒購入する", style=discord.ButtonStyle.success, custom_id="vending_buy_btn")
    async def buy_cb(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        vm_data = vending_machines.get(self.vending_machine_id)
        if not vm_data or not vm_data["items"]:
            await interaction.followup.send("商品が登録されていません。", ephemeral=True)
            return

        options = [
            discord.SelectOption(label=data["name"], value=i_id, emoji=data.get("emoji"))
            for i_id, data in vm_data["items"].items()
        ]

        select = discord.ui.Select(placeholder="購入する商品を選択", options=options)

        async def select_cb(s_inter: discord.Interaction):
            selected_item_id = select.values[0]
            p_view = discord.ui.View(timeout=None)
            p_view.add_item(PaymentSelect(self.vending_machine_id, selected_item_id))
            await s_inter.response.send_message("決済方法を選択してください。", view=p_view, ephemeral=True)

        select.callback = select_cb
        item_view = discord.ui.View(timeout=None)
        item_view.add_item(select)
        await interaction.followup.send("商品を選択してください", view=item_view, ephemeral=True)

    # 赤色（Danger）の在庫確認ボタン
    @discord.ui.button(label="在庫確認", style=discord.ButtonStyle.danger, custom_id="vending_stock_btn")
    async def stock_cb(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        vm_data = vending_machines.get(self.vending_machine_id)
        if not vm_data or not vm_data["items"]:
            await interaction.followup.send("商品が登録されていません。", ephemeral=True)
            return

        stock_info = []
        for i_id, i_data in vm_data["items"].items():
            stock_num = "無限" if i_data["type"] == "無限" else str(len(i_data.get("stock_list", [])))
            sold_num = i_data.get("sold_count", 0)

            # 指定形式に変更
            # ```商品名```
            # ```在庫:X
            # 売上:Y```
            item_block = (
                f"```\n{i_data['name']}\n```\n"
                f"```\n在庫:{stock_num}\n売上:{sold_num}\n```"
            )
            stock_info.append(item_block)

        msg = "\n".join(stock_info)
        await interaction.followup.send(msg, ephemeral=True)

class PayPayOTPModal(discord.ui.Modal, title="PayPay SMS認証"):
    otp = discord.ui.TextInput(
        label="SMSに届いた認証コード",
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

help_group = app_commands.Group(name="help", description="Botのヘルプを表示します")

@help_group.command(name="all", description="Botの全機能と使い方を表示します")
async def help_all_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Bot機能一覧・ヘルプ",
        description="Botの全機能一覧です。下のドロップダウンメニューから詳しく知りたいカテゴリを選択してください。",
        color=discord.Color.blue()
    )
    embed.add_field(name="🎟️ チケット機能", value="`/ticket` : 問い合わせパネル設置", inline=True)
    embed.add_field(name="✅ 認証機能", value="`/verify` : 認証パネル設置", inline=True)
    embed.add_field(name="💳 PayPay連携", value="`/paypay_login` : PayPay自動決済設定", inline=True)
    embed.add_field(name="🛒 自販機管理", value="`/自販機作成`, `/自販機設置` など", inline=True)
    embed.add_field(name="📦 在庫管理", value="`/在庫追加`, `/在庫内容確認` など", inline=True)
    embed.add_field(name="🏷️ クーポン管理", value="`/クーポン作成`, `/クーポン一覧` など", inline=True)
    embed.add_field(name="💾 セーブ/ロード", value="`/save`, `/load` でデータを保管", inline=True)
    embed.add_field(name="🧹 メッセージ削除", value="`/clear` : チャンネルメッセージ削除", inline=True)

    await interaction.response.send_message(embed=embed, view=MainHelpView(), ephemeral=True)

@help_group.command(name="member", description="【サーバー参加者用】自販機の購入ガイドとFAQ")
async def help_member_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 自販機利用ガイド（サーバー参加者向け）",
        description="自販機の購入方法やよくある質問です。\n下のメニューから知りたい項目を選択してください。",
        color=discord.Color.blue()
    )
    embed.add_field(name="🛒 購入手順", value="自販機ボタンから商品と決済方法を選択", inline=False)
    embed.add_field(name="💳 決済方法", value="PayPay送金リンクの作成と入力", inline=False)
    embed.add_field(name="🏷️ クーポン", value="割引クーポンの利用方法", inline=False)
    embed.add_field(name="📩 商品受取", value="購入完了時のDM受取と設定確認", inline=False)
    embed.add_field(name="❓ FAQ", value="トラブル時の対処法", inline=False)

    await interaction.response.send_message(embed=embed, view=MemberHelpView())

bot.tree.add_command(help_group)

# --- セーブ・ロードコマンド ---

@bot.tree.command(name="save", description="現在の自販機・在庫・売上・クーポンデータをテキスト列としてセーブします")
async def save_cmd(interaction: discord.Interaction):
    data = {
        "vending_machines": vending_machines,
        "coupons": coupons
    }
    json_str = json.dumps(data, ensure_ascii=False)
    output_text = f"`{json_str}`"

    if len(output_text) > 2000:
        file_obj = io.BytesIO(json_str.encode('utf-8'))
        await interaction.response.send_message(
            "⚠️ データ量が多く2000文字を超えたため、テキストファイルとして出力しました。\n中身のテキストをコピーして `/load` で読み込んでください。",
            file=discord.File(fp=file_obj, filename="save_data.json"),
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"✅ **セーブデータを出力しました！**\n下記のテキストをコピーし、`/load` コマンドに入力して復元してください：\n{output_text}",
            ephemeral=True
        )

@bot.tree.command(name="load", description="保存したテキスト列を入力して自販機データを復元します")
@app_commands.describe(data_text="セーブ時に出力されたテキスト列を入力")
async def load_cmd(interaction: discord.Interaction, data_text: str):
    global vending_machines, coupons
    try:
        clean_text = data_text.strip("` ").strip()
        data = json.loads(clean_text)

        if "vending_machines" in data:
            vending_machines.clear()
            vending_machines.update(data.get("vending_machines", {}))
            coupons.clear()
            coupons.update(data.get("coupons", {}))
            await interaction.response.send_message("✅ 自販機・在庫・売上・クーポンデータを正常に復元（ロード）しました！", ephemeral=True)
        else:
            vending_machines.clear()
            vending_machines.update(data)
            await interaction.response.send_message("✅ 自販機データを正常に復元（ロード）しました！", ephemeral=True)

    except Exception as e:
        await interaction.response.send_message(f"❌ データのロードに失敗しました。セーブデータのテキスト列が正しいか確認してください。\n詳細: `{e}`", ephemeral=True)

# -----------------------------------

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
        except Exception:
            pass

    await bot.tree.sync()
    print(f"Bot ログイン完了: {bot.user}")

@bot.tree.command(name="ticket", description="チケットパネルを設置します")
@app_commands.describe(
    title="チケットパネルのタイトル",
    description="チケットパネルの説明文",
    buttonlabel="ボタンのラベル",
    buttoncolor="ボタンの色(例:#abc123)"
)
async def ticket_cmd(
    interaction: discord.Interaction,
    title: str = None,
    description: str = None,
    buttonlabel: str = None,
    buttoncolor: str = None
):
    final_title = title if title else "チケット発行"
    final_desc = description if description else "下のボタンを押すとサポートチケットを作成できます。"
    final_label = buttonlabel if buttonlabel else "📩┋チケットを作成"
    color_hex = buttoncolor if buttoncolor else "#5865F2"

    embed_color = parse_color(color_hex)
    embed = discord.Embed(title=final_title, description=final_desc, color=embed_color)

    view = TicketView(final_label, color_hex)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("チケットパネルを設置しました。", ephemeral=True)

@bot.tree.command(name="verify", description="サーバーの認証パネルを設置します")
@app_commands.describe(
    role="付与するロール",
    title="タイトル",
    description="説明文",
    buttonlabel="ボタンのラベル",
    buttoncolor="ボタンの色(例:#abc123)"
)
async def verify_cmd(
    interaction: discord.Interaction,
    role: discord.Role,
    title: str = None,
    description: str = None,
    buttonlabel: str = None,
    buttoncolor: str = None
):
    final_title = title if title else "認証"

    role_text = role.name if role.is_default() else f"<@&{role.id}>"
    final_desc = description if description else f"ボタンを押すと{role_text}が付与されます。"

    final_label = buttonlabel if buttonlabel else "✅┋認証する"
    color_hex = buttoncolor if buttoncolor else "#5865F2"

    embed_color = parse_color(color_hex)
    embed = discord.Embed(title=final_title, description=final_desc, color=embed_color)

    view = VerifyView(role.id, final_label)
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("認証パネルを設置しました。", ephemeral=True)

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

    view = discord.ui.View(timeout=None)
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
    title = panel_title if panel_title else "自販機"
    embed = discord.Embed(title=title, color=discord.Color.green())

    if panel_description:
        embed.description = panel_description

    for item_id, item in vm_data["items"].items():
        field_lines = []
        field_lines.append(f"**{item['name']}**")
        if item.get("description"):
            field_lines.append(item["description"])

        field_lines.append(f"```\nマネー:{item['money']}/マネーライト:{item['manera']}\n```")

        embed.add_field(name="\u200b", value="\n".join(field_lines), inline=False)

    view = VendingView(vending_machine_id)
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
        "sold_count": 0,  # 初期売上数
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

    view = discord.ui.View(timeout=None)
    view.add_item(EditItemSelect(vending_machine_id))
    await interaction.response.send_message("内容を変更する商品を選択してください", view=view, ephemeral=True)

@bot.tree.command(name="商品削除", description="商品を削除")
@app_commands.describe(vending_machine_id="削除する商品がある自販機")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def delete_item(interaction: discord.Interaction, vending_machine_id: str):
    if vending_machine_id not in vending_machines or not vending_machines[vending_machine_id]["items"]:
        await interaction.response.send_message("指定された自販機が存在しないか、商品が登録されていません。", ephemeral=True)
        return

    view = discord.ui.View(timeout=None)
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
                label="商品内容",
                style=discord.TextStyle.paragraph,
                placeholder="例: {アイテムコード} または {{長文テキスト}}",
                required=True,
                max_length=1500
            )

            async def on_submit(self, m_inter: discord.Interaction):
                if "stock_list" not in item:
                    item["stock_list"] = []
                item["stock_list"].append(self.content.value)

                formatted = format_stock_item(self.content.value)
                res_text = f"追加した商品は購入時に送信されます:\n{formatted}"
                await m_inter.response.send_message(res_text, ephemeral=True)

        await inter.response.send_modal(AddStockModal())

    select.callback = select_callback
    view = discord.ui.View(timeout=None)
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
            content_str = st if isinstance(st, str) else st.get("content", "")
            lines.append(format_stock_item(content_str))

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

        drawn_text = "\n".join([format_stock_item(d if isinstance(d, str) else d.get("content", "")) for d in drawn])
        await inter.response.send_message(f"在庫「\n{drawn_text}\n」を引き出しました。", ephemeral=True)

    select.callback = select_callback
    view = discord.ui.View(timeout=None)
    view.add_item(select)
    await interaction.response.send_message("商品を選択してください", view=view, ephemeral=True)

@bot.tree.command(name="クーポン作成", description="クーポンを作成します")
@app_commands.describe(vending_machine_id="クーポンを利用できる自販機", code="クーポンコード", coupon="適用金額")
@app_commands.autocomplete(vending_machine_id=vending_machine_autocomplete)
async def create_coupon(interaction: discord.Interaction, vending_machine_id: str, code: str, coupon: int):
    coupons[code] = {"vm_id": vending_machine_id, "amount": coupon}
    await interaction.response.send_message(
        f"クーポンコード「{code}」を作成しました。\n"
        f"利用可能自販機: `{vending_machine_id}`\n"
        f"クーポンコード: `{code}`\n"
        f"適用金額: `{coupon}`",
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
            f"利用可能自販機: `{data['vm_id']}`\n"
            f"クーポンコード: `{code}`\n"
            f"適用金額: `{data['amount']}`"
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
        f"利用可能自販機: `{data['vm_id']}`\n"
        f"クーポンコード: `{code}`\n"
        f"適用金額: `{data['amount']}`"
    )

    view = discord.ui.View(timeout=None)
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

@bot.tree.command(name="clear", description="実行したチャンネルのメッセージをすべて削除します")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=100)
        await interaction.followup.send(f"✅ {len(deleted)} 件のメッセージを削除しました。", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ Botに「メッセージの管理」権限が不足しています。", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ メッセージの削除中にエラーが発生しました: {e}", ephemeral=True)

@clear_cmd.error
async def clear_cmd_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ このコマンドを実行する権限（メッセージの管理）がありません。", ephemeral=True)

if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
