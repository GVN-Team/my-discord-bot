import discord
from discord import app_commands
from discord.ext import commands

class MainHelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="チケット機能", value="ticket", description="問い合わせパネルの設置・運用", emoji="🎟️"),
            discord.SelectOption(label="認証機能", value="verify", description="ロール付与認証パネルの設置", emoji="✅"),
            discord.SelectOption(label="PayPay連携", value="paypay", description="PayPayアカウント連携・全自動決済", emoji="💳"),
            discord.SelectOption(label="自販機・商品管理", value="vending", description="自販機作成・設置・商品登録・削除", emoji="🛒"),
            discord.SelectOption(label="在庫管理", value="stock", description="在庫の追加・内容確認・引き出し", emoji="📦"),
            discord.SelectOption(label="クーポン管理", value="coupon", description="割引クーポンの作成・一覧・削除", emoji="🏷️"),
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
        }
        await interaction.response.send_message(embed=embeds[val], ephemeral=True)

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
        await interaction.response.send_message(embed=embeds[val], ephemeral=True)

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    help_group = app_commands.Group(name="help", description="Botのヘルプを表示します")

    @help_group.command(name="all", description="Botの全機能と使い方を表示します")
    async def help_all_cmd(self, interaction: discord.Interaction):
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

        view = discord.ui.View(timeout=None)
        view.add_item(MainHelpSelect())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @help_group.command(name="member", description="【サーバー参加者用】自販機の購入ガイドとFAQ")
    async def help_member_cmd(self, interaction: discord.Interaction):
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

        view = discord.ui.View(timeout=None)
        view.add_item(MemberHelpSelect())
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
