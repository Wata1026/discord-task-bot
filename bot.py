# ============================================================
# Discord タスク管理Bot
#  #add チャンネルに "ad" または "fn" を書き込むと、
#  #task チャンネルのタスク一覧が自動で更新されます
# ============================================================

import os                                  # 環境変数(トークン等)を読むための部品
import json                                # タスクをファイルに保存するための部品
import re                                  # 日付を文字から探すための部品
from datetime import datetime, date        # 日付を扱うための部品
from zoneinfo import ZoneInfo              # 日本時間を使うための部品

import discord                             # Discordを操作する部品
from dotenv import load_dotenv             # .envファイルを読む部品(自分のPCで動かす時用)

# ------------------------------------------------------------
# 設定の読み込み
# ------------------------------------------------------------
load_dotenv()  # .envファイルがあれば中身を読み込む(なければ何もしない)

TOKEN = os.getenv("DISCORD_TOKEN")                    # Botのトークン(パスワード)
ADD_CHANNEL_ID = int(os.getenv("ADD_CHANNEL_ID"))     # #add チャンネルのID
TASK_CHANNEL_ID = int(os.getenv("TASK_CHANNEL_ID"))   # #task チャンネルのID

# タスクの保存先。DATA_DIRが設定されていればそのフォルダ、なければ今のフォルダ
DATA_DIR = os.getenv("DATA_DIR", ".")
DATA_FILE = os.path.join(DATA_DIR, "tasks.json")      # タスクを保存するファイル名

JST = ZoneInfo("Asia/Tokyo")  # 日本時間

# ------------------------------------------------------------
# Botの基本設定
# ------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True            # メッセージの中身を読む許可
client = discord.Client(intents=intents)  # Bot本体を作成


# ------------------------------------------------------------
# タスクの保存・読み込み
# ------------------------------------------------------------
def load_tasks():
    """tasks.json からタスク一覧を読み込む(ファイルがなければ空のリスト)"""
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tasks(tasks):
    """タスク一覧を tasks.json に保存する"""
    os.makedirs(DATA_DIR, exist_ok=True)  # 保存フォルダがなければ作る
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------
# メッセージの解析
# ------------------------------------------------------------
def parse_add(lines):
    """
    "ad" の書き込みからタスクを取り出す。
    例)
      ad
      ■デモリール提出
      ~2026/10/26
    ■ で始まる行 = タイトル、~ で始まる行 = 期限
    1つのメッセージに複数タスクを書いてもOK
    """
    added = []       # 取り出したタスクを入れる箱
    current = None   # いま処理中のタスク

    for line in lines[1:]:          # 1行目("ad")は飛ばして2行目から見る
        line = line.strip()         # 前後の空白を除去
        if not line:                # 空行は無視
            continue

        if line.startswith("■"):    # ■ で始まる = 新しいタスクのタイトル
            if current:             # 前のタスクがあれば箱に入れる
                added.append(current)
            current = {"title": line[1:].strip(), "due": None}

        elif line.startswith("~") or line.startswith("～"):  # ~ で始まる = 期限
            if current:
                # 「2026/10/26」のような形を探す
                m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", line)
                if m:
                    y, mo, d = map(int, m.groups())
                    try:
                        current["due"] = date(y, mo, d).isoformat()
                    except ValueError:
                        pass        # 2026/02/31 のような存在しない日付は無視

    if current:                     # 最後のタスクも箱に入れる
        added.append(current)
    return added


def parse_finish(lines):
    """
    "fn" の書き込みから、完了したタスクのタイトルを取り出す。
    例)
      fn
      デモリール提出
    複数行書けば複数まとめて完了にできる
    """
    titles = []
    for line in lines[1:]:
        t = line.strip().lstrip("■").strip()  # 前後の空白と先頭の■を除去
        if t:
            titles.append(t)
    return titles


# ------------------------------------------------------------
# #task に表示する一覧メッセージを作る
# ------------------------------------------------------------
def build_task_message(tasks):
    if not tasks:
        return "📋 **タスク一覧**\n\n現在のタスクはありません 🎉"

    # 期限が近い順に並び替え(期限なしは一番後ろ)
    sorted_tasks = sorted(tasks, key=lambda t: (t["due"] is None, t["due"] or ""))

    today = datetime.now(JST).date()  # 今日の日付(日本時間)
    out = ["📋 **タスク一覧**\n"]

    for t in sorted_tasks:
        if t["due"]:
            d = date.fromisoformat(t["due"])
            diff = (d - today).days   # 期限まであと何日か
            if diff < 0:
                tag = f"⚠️ {abs(diff)}日超過"
            elif diff == 0:
                tag = "🔥 今日まで"
            else:
                tag = f"あと{diff}日"
            out.append(f"■ **{t['title']}**\n　~{d.strftime('%Y/%m/%d')}({tag})")
        else:
            out.append(f"■ **{t['title']}**\n　期限なし")

    out.append(f"\n_最終更新: {datetime.now(JST).strftime('%Y/%m/%d %H:%M')}_")
    return "\n".join(out)


async def refresh_task_channel():
    """#task の古いBotメッセージを消して、最新の一覧を投稿し直す"""
    channel = client.get_channel(TASK_CHANNEL_ID)
    if channel is None:   # チャンネルが見つからなければ何もしない
        return

    # 直近50件のうち、Bot自身が書いたメッセージを削除
    async for msg in channel.history(limit=50):
        if msg.author == client.user:
            await msg.delete()

    # 最新の一覧を投稿
    await channel.send(build_task_message(load_tasks()))


# ------------------------------------------------------------
# Discordからのイベント処理
# ------------------------------------------------------------
@client.event
async def on_ready():
    """Botが起動してログインできた時に1回だけ実行される"""
    print(f"ログイン成功: {client.user}")
    await refresh_task_channel()  # 起動時にも一覧を最新にする


@client.event
async def on_message(message):
    """メッセージが投稿されるたびに実行される"""

    # Bot自身の投稿、または #add 以外のチャンネルの投稿は無視
    if message.author.bot or message.channel.id != ADD_CHANNEL_ID:
        return

    lines = message.content.strip().split("\n")  # 行ごとに分割
    command = lines[0].strip().lower()           # 1行目(ad か fn か)
    tasks = load_tasks()                         # 現在のタスクを読み込み

    # ----- タスク追加 -----
    if command == "ad":
        new_tasks = parse_add(lines)
        if not new_tasks:                        # 読み取れなかった場合
            await message.add_reaction("❓")
            return
        tasks.extend(new_tasks)                  # タスクを追加
        save_tasks(tasks)                        # 保存
        await refresh_task_channel()             # #task を更新
        await message.add_reaction("✅")         # 成功の印

    # ----- タスク完了 -----
    elif command == "fn":
        titles = parse_finish(lines)
        before = len(tasks)
        # 完了タイトルと一致しないものだけ残す(=一致したものを削除)
        tasks = [t for t in tasks if t["title"] not in titles]
        if len(tasks) == before:                 # 1件も一致しなかった場合
            await message.add_reaction("❓")
            return
        save_tasks(tasks)
        await refresh_task_channel()
        await message.add_reaction("✅")


# ------------------------------------------------------------
# Botを起動
# ------------------------------------------------------------
client.run(TOKEN)
