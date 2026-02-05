import discord
from discord.ext import commands, tasks
import re
import io
import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
from datetime import datetime, timedelta, timezone
from flask import Flask
from threading import Thread
import math

# ================= 設定エリア =================
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID")) if os.getenv("TARGET_CHANNEL_ID") else None
JST = timezone(timedelta(hours=9))
# =============================================

# --- Webサーバー ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server, daemon=True)
    t.start()

# --- Bot設定 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

PROBLEM_MODELS = {}

# 難易度補正
def get_display_difficulty(raw_diff):
    if raw_diff >= 400:
        return raw_diff
    else:
        return int(400 / math.exp(1.0 - raw_diff / 400))

def get_atcoder_color(diff):
    if diff < 400:  return '#808080' # 灰
    if diff < 800:  return '#804000' # 茶
    if diff < 1200: return '#008000' # 緑
    if diff < 1600: return '#00C0C0' # 水
    if diff < 2000: return '#0000FF' # 青
    if diff < 2400: return '#C0C000' # 黄
    if diff < 2800: return '#FF8000' # 橙
    return '#FF0000' # 赤

def fetch_api_data():
    global PROBLEM_MODELS
    try:
        res = requests.get("https://kenkoooo.com/atcoder/resources/problem-models.json", timeout=15)
        if res.status_code == 200:
            PROBLEM_MODELS = res.json()
        return True
    except Exception as e:
        print(f"API Error: {e}")
        return False

@tasks.loop(hours=24)
async def update_data_task():
    fetch_api_data()

# --- テキスト表作成ロジック (調整版) ---
def get_visual_width(s):
    width = 0
    for c in s:
        if ord(c) > 255: width += 2
        else: width += 1
    return width

def pad_str(s, width):
    w = get_visual_width(s)
    return s + " " * (width - w)

def create_text_table(stats, extra_stats, others_count, color_counts):
    # 幅設定
    cw = 8  # Category Width (典型90問=8文字幅に合わせる)
    dw = 3  # Data Width (3桁数字用)

    # ヘッダー作成
    # タイトルなし(スペースのみ) + データ列ヘッダー
    # A~Gはセンタリング風に " A "
    cols = [" A ", " B ", " C ", " D ", " E ", " F ", " G ", " Ex", "Oth", "Sum"]
    header = " " * cw + "|" + "|".join(cols)
    
    # セパレータ: --------+---+---+...
    line = "-" * cw + "+" + "+".join(["-" * dw] * 10)

    lines = []
    lines.append(header)
    lines.append(line)

    def make_row(name, vals, total):
        # 名前をcw文字幅で左詰め
        row = pad_str(name, cw) + "|"
        for v in vals:
            s_val = str(v)
            # 幅3で右詰め
            row += f"{s_val:>{dw}}" + "|"
        # 合計 (右端のパイプは無し)
        row += f"{total:>{dw}}" 
        return row

    labels = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    for cat in ["ABC", "ARC", "AGC"]:
        counts = [stats[cat].get(l, 0) for l in labels]
        total = sum(counts)
        lines.append(make_row(cat, counts, total))

    # ハイフン列の生成 "  -|" (3文字)
    hyphen_cell = f"{'-':>{dw}}|"
    hyphens_9 = hyphen_cell * 9

    for name in ["鉄則本", "典型90問"]:
        val = extra_stats.get(name, 0)
        row = pad_str(name, cw) + "|" + hyphens_9 + f"{val:>{dw}}"
        lines.append(row)

    others_val = others_count
    row = pad_str("Others", cw) + "|" + hyphens_9 + f"{others_val:>{dw}}"
    lines.append(row)

    color_order = ["🔴", "🟠", "🟡", "🟦", "🔵", "🟢", "🟤", "⚪"]
    color_line = " ".join([f"{emoji}{color_counts.get(emoji, 0)}" for emoji in color_order if color_counts.get(emoji, 0) > 0])

    text_table = "```text\n" + "\n".join(lines) + "\n```"
    if color_line:
        text_table += f"\nDifficulty: {color_line}"
    return text_table

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
    except:
        await ctx.send("日付形式エラー")
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
            if message.author != member: continue
            msg_date = message.created_at.astimezone(JST)
            if since and msg_date < since: continue
            if msg_date > until: continue

            for line in message.content.split('\n'):
                words = line.strip().split()
                if not words: continue
                first = words[0]
                d_key = msg_date.date()
                ac_count = 0
                
                match = CONTEST_HEAD_PATTERN.match(first)
                if match:
                    cat, num = match.group(1).upper(), match.group(2)
                    problems = words[1:]
                    ac_count = len(problems)
                    for p in problems:
                        label = p.upper()
                        pid = f"{cat.lower()}{num}_{label.lower()}"
                        model = PROBLEM_MODELS.get(pid)
                        if model and 'difficulty' in model:
                            dv = get_display_difficulty(model['difficulty'])
                            diff_values.append(dv)
                            
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
                elif "鉄則" in first:
                    cnt = max(1, len(words)-1); extra_stats["鉄則本"] += cnt; ac_count = cnt
                elif "典型" in first:
                    cnt = max(1, len(words)-1); extra_stats["典型90問"] += cnt; ac_count = cnt
                else:
                    others_total += 1; ac_count = 1
                
                if ac_count > 0: daily_ac[d_key] = daily_ac.get(d_key, 0) + ac_count

    if not daily_ac:
        await ctx.send("記録なし")
        return

    # --- グラフ描画 ---
    plt.style.use('ggplot')
    files = []
    
    df = pd.DataFrame(list(daily_ac.items()), columns=['date', 'count']).sort_values('date')
    df['date'] = pd.to_datetime(df['date'])
    df['cum'] = df['count'].cumsum()
    
    # [グラフ1: Activity]
    fig1, ax1 = plt.subplots(figsize=(10, 5))
    ax1.bar(df['date'], df['count'], color='#4682B4', alpha=0.9)
    ax1.set_ylabel('Daily AC')
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.set_ylim(bottom=0)
    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))

    ax1_t = ax1.twinx()
    ax1_t.plot(df['date'], df['cum'], color='#FF8C00', marker='o', linewidth=2)
    ax1_t.set_ylabel('Total AC')
    ax1_t.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1_t.set_ylim(bottom=0)
    ax1_t.grid(False)

    max_d = df['count'].max()
    max_c = df['cum'].max()
    ax1.set_ylim(0, math.ceil((max_d+0.1)/5)*5)
    ax1_t.set_ylim(0, math.ceil((max_c+0.1)/5)*5)
    ax1.set_title(f"Activity: {member.display_name}")
    
    plt.tight_layout()
    buf1 = io.BytesIO()
    plt.savefig(buf1, format='png', dpi=100)
    buf1.seek(0)
    files.append(discord.File(buf1, "activity.png"))
    plt.close(fig1)

    # [グラフ2: Diff分布]
    if diff_values:
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        bw = 100
        max_val = max(diff_values)
        upper_bound = (int(max_val) // bw + 1) * bw
        if upper_bound < 400: upper_bound = 400 

        bins = range(0, upper_bound + bw + bw, bw)
        
        out = pd.cut(diff_values, bins=bins, right=False)
        bc = out.value_counts().sort_index()
        xc = [e + bw/2 for e in bins[:-1]]
        cols = [get_atcoder_color(e) for e in bins[:-1]]
        
        ax2.bar(xc, bc.values, width=bw, color=cols, edgecolor='black')
        ax2.set_title("Difficulty Distribution")
        ax2.set_xlabel("Difficulty")
        ax2.set_ylabel("Count")
        ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax2.set_ylim(bottom=0)
        
        x_limit = upper_bound + bw
        ax2.set_xlim(left=0, right=x_limit)
        
        if x_limit <= 800: step = 100 
        elif x_limit <= 1600: step = 200
        else: step = 400
            
        ax2.set_xticks(range(0, x_limit + step, step))
        
        plt.tight_layout()
        buf2 = io.BytesIO()
        plt.savefig(buf2, format='png', dpi=100)
        buf2.seek(0)
        files.append(discord.File(buf2, "difficulty.png"))
        plt.close(fig2)
    
    await ctx.send(content=create_text_table(stats, extra_stats, others_total, color_counts), files=files)

@bot.event
async def on_ready():
    fetch_api_data()
    if not update_data_task.is_running():
        update_data_task.start()
    print(f'Logged in: {bot.user.name}')

if __name__ == "__main__":
    keep_alive()
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: TOKEN not found.")