import discord
from discord.ext import commands, tasks
import re
import io
import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta, timezone
from flask import Flask
from threading import Thread

# ================= 設定エリア =================
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID")) if os.getenv("TARGET_CHANNEL_ID") else None
JST = timezone(timedelta(hours=9))
# =============================================

# --- 1. Koyeb ヘルスチェック用 Webサーバー ---
app = Flask('')

@app.route('/')
def home():
    return "AtCoder Bot is running!"

def run_web_server():
    # Koyebのデフォルトポート8000で待機
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    """Webサーバーを別スレッドで起動する"""
    t = Thread(target=run_web_server, daemon=True)
    t.start()

# --- 2. ボットの基本設定 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# キャッシュデータ
PROBLEM_MODELS = {}
PROBLEMS = {}

def get_atcoder_color(diff):
    """難易度に応じた16進数カラーコードを返す"""
    if diff < 400:  return '#808080' # 灰
    if diff < 800:  return '#804000' # 茶
    if diff < 1200: return '#008000' # 緑
    if diff < 1600: return '#00C0C0' # 水
    if diff < 2000: return '#0000FF' # 青
    if diff < 2400: return '#C0C000' # 黄
    if diff < 2800: return '#FF8000' # 橙
    return '#FF0000' # 赤

def fetch_api_data():
    """AtCoder Problems APIからデータをキャッシュする"""
    global PROBLEM_MODELS, PROBLEMS
    print(f"[{datetime.now(JST)}] Fetching API data...")
    try:
        models_url = "https://kenkoooo.com/atcoder/resources/problem-models.json"
        models_res = requests.get(models_url, timeout=10)
        if models_res.status_code == 200:
            PROBLEM_MODELS = models_res.json()

        problems_url = "https://kenkoooo.com/atcoder/resources/problems.json"
        problems_res = requests.get(problems_url, timeout=10)
        if problems_res.status_code == 200:
            raw_problems = problems_res.json()
            PROBLEMS = {p['id']: p for p in raw_problems}
        return True
    except Exception as e:
        print(f"API Fetch Error: {e}")
        return False

@tasks.loop(hours=24)
async def update_data_task():
    fetch_api_data()

def create_markdown_table(stats, extra_stats, others_count, color_counts):
    """詳細な集計テーブルを作成する"""
    labels = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    header = "| カテゴリ | A | B | C | D | E | F | G | Ex | 他 | 計 |"
    sep    = "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    
    rows = []
    for cat in ["ABC", "ARC", "AGC"]:
        counts = [stats[cat].get(l, 0) for l in labels]
        total = sum(counts)
        row_str = f"| **{cat}** | " + " | ".join(map(str, counts)) + f" | **{total}** |"
        rows.append(row_str)
    
    for name in ["鉄則本", "典型90問"]:
        val = extra_stats.get(name, 0)
        rows.append(f"| **{name}** | - | - | - | - | - | - | - | - | - | **{val}** |")
    
    rows.append(f"| **Others** | - | - | - | - | - | - | - | - | - | **{others_count}** |")
    
    color_order = ["🔴", "🟠", "🟡", "🟦", "🔵", "🟢", "🟤", "⚪"]
    color_line = " ".join([f"{emoji}{color_counts.get(emoji, 0)}" for emoji in color_order if color_counts.get(emoji, 0) > 0])
    
    table = f"```markdown\n{header}\n{sep}\n" + "\n".join(rows) + "\n```"
    if color_line:
        table += f"\n**難易度内訳:** {color_line}"
    return table

CONTEST_HEAD_PATTERN = re.compile(r'^(ABC|ARC|AGC)(\d+)$', re.IGNORECASE)

@bot.command(name="atcoder")
async def get_stats(ctx, member: discord.Member = None, period: str = "all", start_date: str = None, end_date: str = None):
    if TARGET_CHANNEL_ID and ctx.channel.id != TARGET_CHANNEL_ID:
        return 

    member = member or ctx.author
    now = datetime.now(JST)
    since, until = None, now

    try:
        if period == "week":
            since = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "range" and start_date:
            since = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=JST)
            if end_date:
                until = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=JST)
    except Exception:
        await ctx.send("日付形式が正しくありません (YYYY-MM-DD)。")
        return

    problem_keys = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    stats = {cat: {l: 0 for l in problem_keys} for cat in ["ABC", "ARC", "AGC"]}
    extra_stats = {"鉄則本": 0, "典型90問": 0}
    others_total = 0
    daily_ac = {}
    color_counts = {}
    diff_values = []

    async with ctx.typing():
        async for message in ctx.channel.history(limit=5000):
            msg_date = message.created_at.astimezone(JST)
            if message.author != member: continue
            if since and msg_date < since: continue
            if msg_date > until: continue

            for line in message.content.split('\n'):
                words = line.strip().split()
                if not words: continue
                first_word = words[0]
                d_key = msg_date.date()
                ac_count = 0

                match = CONTEST_HEAD_PATTERN.match(first_word)
                if match:
                    cat, num = match.group(1).upper(), match.group(2)
                    problems = words[1:]
                    ac_count = len(problems)
                    for p in problems:
                        label = p.upper()
                        pid = f"{cat.lower()}{num}_{label.lower()}"
                        model = PROBLEM_MODELS.get(pid)
                        if model and 'difficulty' in model:
                            dv = model['difficulty']
                            diff_values.append(dv)
                            # 色絵文字の集計
                            if dv < 400: e = "⚪"
                            elif dv < 800: e = "🟤"
                            elif dv < 1200: e = "🟢"
                            elif dv < 1600: e = "🔵"
                            elif dv < 2000: e = "🟦"
                            elif dv < 2400: e = "🟡"
                            elif dv < 2800: e = "🟠"
                            else: e = "🔴"
                            color_counts[e] = color_counts.get(e, 0) + 1
                        
                        if label in ["A","B","C","D","E","F","G"]: stats[cat][label] += 1
                        elif label == "EX": stats[cat]["EX"] += 1
                        else: stats[cat]["Other"] += 1
                elif "鉄則" in first_word:
                    cnt = max(1, len(words)-1); extra_stats["鉄則本"] += cnt; ac_count = cnt
                elif "典型" in first_word:
                    cnt = max(1, len(words)-1); extra_stats["典型90問"] += cnt; ac_count = cnt
                else:
                    others_total += 1; ac_count = 1
                
                if ac_count > 0: daily_ac[d_key] = daily_ac.get(d_key, 0) + ac_count

    if not daily_ac:
        await ctx.send(f"{member.display_name} さんの記録が見つかりませんでした。")
        return

    # グラフ描画
    plt.style.use('ggplot')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 10))
    df = pd.DataFrame(list(daily_ac.items()), columns=['date', 'count']).sort_values('date')
    df['date'] = pd.to_datetime(df['date'])
    df['cumulative'] = df['count'].cumsum()
    
    ax1.bar(df['date'], df['count'], color='#4682B4', alpha=0.7, label='Daily')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1_twin = ax1.twinx()
    ax1_twin.plot(df['date'], df['cumulative'], color='#FF8C00', marker='o', linewidth=2)
    ax1.set_title(f"Activity: {member.display_name}")

    if diff_values:
        bw = 100
        mi, ma = (min(diff_values)//bw)*bw, (max(diff_values)//bw+1)*bw
        bins = range(mi, ma + bw, bw)
        out = pd.cut(diff_values, bins=bins, right=False)
        bc = out.value_counts().sort_index()
        xc = [e + bw/2 for e in bins[:-1]]
        cols = [get_atcoder_color(e) for e in bins[:-1]]
        ax2.bar(xc, bc.values, width=bw*0.8, color=cols, edgecolor='black', alpha=0.8)
        ax2.set_title("Difficulty Distribution")
        ax2.set_xlabel("Difficulty")
    
    fig.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    
    table_str = create_markdown_table(stats, extra_stats, others_total, color_counts)
    await ctx.send(content=f"📊 **{member.display_name}** の集計結果\n{table_str}", file=discord.File(buf, "stats.png"))
    plt.close()

@bot.event
async def on_ready():
    fetch_api_data()
    if not update_data_task.is_running():
        update_data_task.start()
    print(f'Logged in: {bot.user.name}')

# プログラム実行
if __name__ == "__main__":
    keep_alive() # ヘルスチェック用Webサーバー起動
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: TOKEN not found.")