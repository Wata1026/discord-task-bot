# ============================================================
# Discord タスク管理Bot(GitHub Actions版)
#  5分ごとに1回だけ実行され、以下を行って終了します
#   1. #add の投稿を古い順に読み、ad / fn を再現してタスク一覧を作る
#   2. #task の内容と違っていれば、古い投稿を消して新しく投稿する
#   3. 未処理の #add の投稿に ✅(成功) / ❓(失敗) を付ける
# ============================================================

import os
import re
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo
from urllib.parse import quote

import requests  # Discordと通信するための部品

# ------------------------------------------------------------
# 設定(GitHubのSecretsから読み込まれる)
# ------------------------------------------------------------
TOKEN = os.environ["DISCORD_TOKEN"]                    # Botのトークン
ADD_CHANNEL_ID = os.environ["ADD_CHANNEL_ID"].strip()  # #add のID
TASK_CHANNEL_ID = os.environ["TASK_CHANNEL_ID"].strip()  # #task のID

API = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {TOKEN}",
    "User-Agent": "DiscordBot (task-bot, 1.0)",
}
JST = ZoneInfo("Asia/Tokyo")  # 日本時間
MAX_HISTORY = 1000            # #add から読み込む投稿の最大数


# ------------------------------------------------------------
# Discordとの通信
# ------------------------------------------------------------
def call(method, path, **kwargs):
    """Discord APIを呼ぶ。「回数制限」と言われたら待って再試行する"""
    while True:
        r = requests.request(method, API + path, headers=HEADERS, timeout=30, **kwargs)
        if r.status_code == 429:  # 送りすぎ → 指定された秒数待つ
            time.sleep(r.json().get("retry_after", 1) + 0.5)
            continue
        return r


def get_messages(channel_id, max_count):
    """チャンネルの投稿を新しい順に最大max_count件取得する"""
    msgs = []
    before = None
    while len(msgs) < max_count:
        params = {"limit": 100}
        if before:
            params["before"] = before
        r = call("GET", f"/channels/{channel_id}/messages", params=params)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        msgs += batch
        before = batch[-1]["id"]  # 次はこのIDより古いものを取る
        if len(batch) < 100:
            break
    return msgs


# ------------------------------------------------------------
# メッセージの解析
# ------------------------------------------------------------
def parse_add(lines):
    """
    "ad" の投稿からタスクを取り出す。
      ad
      ■デモリール提出
      ~2026/10/26
    ■ で始まる行 = タイトル、~ で始まる行 = 期限
    """
    added = []
    current = None
    for line in lines[1:]:           # 1行目("ad")は飛ばす
        line = line.strip()
        if not line:
            continue
        if line.startswith("■"):     # 新しいタスク
            if current:
                added.append(current)
            current = {"title": line[1:].strip(), "due": None}
        elif line.startswith("~") or line.startswith("～"):  # 期限
            if current:
                m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", line)
                if m:
                    y, mo, d = map(int, m.groups())
                    try:
                        current["due"] = date(y, mo, d).isoformat()
                    except ValueError:
                        pass         # 存在しない日付は無視
    if current:
        added.append(current)
    return added


def parse_finish(lines):
    """"fn" の投稿から、完了したタスクのタイトルを取り出す"""
    titles = []
    for line in lines[1:]:
        t = line.strip().lstrip("■").strip()
        if t:
            titles.append(t)
    return titles


# ------------------------------------------------------------
# #task に投稿する一覧を作る(2000文字を超える場合は分割)
# ------------------------------------------------------------
def build_chunks(tasks):
    if not tasks:
        return ["📋 **タスク一覧**\n\n現在のタスクはありません 🎉"]

    # 期限が近い順に並び替え(期限なしは最後)
    tasks = sorted(tasks, key=lambda t: (t["due"] is None, t["due"] or ""))
    today = datetime.now(JST).date()

    entries = []
    for t in tasks:
        if t["due"]:
            d = date.fromisoformat(t["due"])
            diff = (d - today).days   # 期限まであと何日か
            if diff < 0:
                tag = f"⚠️ {abs(diff)}日超過"
            elif diff == 0:
                tag = "🔥 今日まで"
            else:
                tag = f"あと{diff}日"
            entries.append(f"■ **{t['title']}**\n　~{d.strftime('%Y/%m/%d')}({tag})")
        else:
            entries.append(f"■ **{t['title']}**\n　期限なし")

    # Discordは1投稿2000文字までなので、1800文字ごとに分ける
    chunks = []
    current = "📋 **タスク一覧**\n"
    for e in entries:
        if len(current) + len(e) + 1 > 1800:
            chunks.append(current)
            current = ""
        current += "\n" + e
    chunks.append(current)
    return chunks


# ------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------
def main():
    me = call("GET", "/users/@me").json()["id"]  # Bot自身のID

    # ---- #add の投稿を古い順に並べて、adとfnを順番に再現 ----
    add_msgs = list(reversed(get_messages(ADD_CHANNEL_ID, MAX_HISTORY)))
    tasks = []
    results = {}  # 投稿ID → 成功したか

    for m in add_msgs:
        if m["author"].get("bot"):   # Botの投稿は無視
            continue
        lines = m["content"].strip().split("\n")
        command = lines[0].strip().lower()

        if command == "ad":
            new = parse_add(lines)
            tasks.extend(new)
            results[m["id"]] = bool(new)
        elif command == "fn":
            titles = parse_finish(lines)
            before = len(tasks)
            tasks = [t for t in tasks if t["title"] not in titles]
            results[m["id"]] = len(tasks) < before

    print(f"現在のタスク数: {len(tasks)}")

    # ---- まだ反応が付いていない投稿に ✅ / ❓ を付ける ----
    for m in add_msgs:
        if m["id"] not in results:
            continue
        if any(r.get("me") for r in m.get("reactions", [])):
            continue                 # すでにBotが反応済み
        emoji = "✅" if results[m["id"]] else "❓"
        r = call("PUT", f"/channels/{ADD_CHANNEL_ID}/messages/{m['id']}"
                        f"/reactions/{quote(emoji)}/@me")
        if r.status_code == 403:     # リアクション権限がない → 諦める(続行はする)
            print("リアクション権限がありません(一覧更新は続行します)")
            break

    # ---- #task を更新 ----
    chunks = build_chunks(tasks)
    task_msgs = list(reversed(get_messages(TASK_CHANNEL_ID, 50)))
    mine = [m for m in task_msgs if m["author"]["id"] == me]  # Bot自身の投稿

    # すでに同じ内容なら何もしない(無駄な削除・再投稿を避ける)
    if [m["content"] for m in mine] == chunks:
        print("変更なし")
        return

    for m in mine:  # 古い投稿を削除
        call("DELETE", f"/channels/{TASK_CHANNEL_ID}/messages/{m['id']}")
    for c in chunks:  # 新しい一覧を投稿
        r = call("POST", f"/channels/{TASK_CHANNEL_ID}/messages", json={"content": c})
        r.raise_for_status()
    print("#task を更新しました")


main()
