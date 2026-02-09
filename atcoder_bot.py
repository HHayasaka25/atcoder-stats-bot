import discord
from discord.ext import commands, tasks
from discord import app_commands
import re
import io
import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
from datetime import datetime, timedelta, timezone
import math

# ================= 設定エリア =================
# systemdのEnvironment設定から取得されます
TOKEN = os.getenv("DISCORD_TOKEN")

try:
    target_id_raw = os.getenv("TARGET_CHANNEL_ID")
    TARGET_CHANNEL_ID = int(target_id_raw) if target_id_raw else None
except ValueError:
    TARGET_CHANNEL_ID = None
    print("Warning: TARGET_CHANNEL_ID is not a valid integer.")

JST = timezone(timedelta(hours=9))
# =============================================

# --- Bot設定 (Slash Command対応) ---
class AtCoderBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()

intents = discord.Intents.default()
intents.message_content = True
bot = AtCoderBot(command_prefix="/", intents=intents)

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

# --- テキスト表作成ロジック ---
def get_visual_width(s):
    width = 0
    for c in s:
        if ord(c) > 255: width += 2
        else: width += 1
    return width

def pad_str(s, width):
    w = get_visual_width(s)
    # 幅が足りない場合のみスペースを追加
    return s + " " * max(0, width - w)

def create_text_table(stats, extra_stats, others_count, color_counts):
    # ==========================================
    # ▼ 直感的な幅指定エリア
    # キー: コンテスト名, 値: 全体の表示幅(文字数)
    # ※テーブルの縦線 '|' を揃えたい場合は数値を調整してください
    WIDTH_CONFIG = {
        "Header":   7,  # ヘッダー(1行目)の左端の幅
        "ABC":      7,
        "ARC":      7,
        "AGC":      7,
        "AWC":      7,  # 新しく追加
        "鉄則本":     8,  # 日本語など幅がズレやすいものは個別に広げる
        "典型90問":   8,
        "Others":   7,
    }
    # ==========================================

    dw = 3 # データ部分の幅 (3桁まで対応)
    
    # ヘッダー作成
    hw = WIDTH_CONFIG.get("Header", 7)
    cols = [" A ", " B ", " C ", " D ", " E ", " F ", " G ", " Ex", "Oth", "Sum"]
    header = " " * hw + "|" + "|".join(cols)
    line = "-" * hw + "+" + "+".join(["-" * dw] * 10)
    lines = [header, line]

    def make_row(name, vals, total):
        # 設定から幅を取得。なければHeaderと同じにする
        w = WIDTH_CONFIG.get(name, hw)
        row = pad_str(name, w) + "|"
        for v in vals:
            row += f"{str(v):>{dw}}|"
        row += f"{total:>{dw}}" 
        return row

    labels = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    
    # 標準コンテスト (AWCを追加)
    target_cats = ["ABC", "ARC", "AGC", "AWC"]
    for cat in target_cats:
        counts = [stats[cat].get(l, 0) for l in labels]
        total = sum(counts)
        lines.append(make_row(cat, counts, total))

    hyphen_cell = f"{'-':^{dw}}|"
    hyphens_9 = hyphen_cell * 9
    
    # その他日本語系コンテスト
    for name in ["鉄則本", "典型90問"]:
        val = extra_stats.get(name, 0)
        w = WIDTH_CONFIG.get(name, 8)
        # 日本語行は詳細データがないためハイフンで埋める
        row = pad_str(name, w) + "|" + hyphens_9 + f"{val:>{dw}}"
        lines.append(row)

    # Others
    w_oth = WIDTH_CONFIG.get("Others", 7)
    row = pad_str("Others", w_oth) + "|" + hyphens_9 + f"{others_total:>{dw}}"
    lines.append(row)

    # 色文字の生成
    color_order = ["🔴", "🟠", "🟡", "🟦", "🔵", "🟢", "🟤", "⚪"]
    color_line = " ".join([f"{emoji}{color_counts.get(emoji, 0)}" for emoji in color_order if color_counts.get(emoji, 0) > 0])
    
    text_table = "```text\n" + "\n".join(lines) + "\n```"
    if color_line: text_table += f"\nDifficulty: {color_line}"
    return text_table

# 正規表現にAWCを追加
CONTEST_HEAD_PATTERN = re.compile(r'^(ABC|ARC|AGC|AWC)(\d+)$', re.IGNORECASE)

@bot.hybrid_command(name="atcoder", description="精進記録を集計してグラフを表示します")
@app_commands.describe(member="集計するユーザー", period="期間 (all, week, range)", start_date="開始日", end_date="終了日")
async def get_stats(ctx, member: discord.Member = None, period: str = "all", start_date: str = None, end_date: str = None):
    if TARGET_CHANNEL_ID and ctx.channel.id != TARGET_CHANNEL_ID:
        if ctx.interaction: await ctx.send("このチャンネルでは使用できません。", ephemeral=True)
        return 
    await ctx.defer()
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
        await ctx.send("日付形式エラー: YYYY-MM-DD で指定してください")
        return

    problem_keys = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    # 統計用辞書にAWCを追加
    target_cats = ["ABC", "ARC", "AGC", "AWC"]
    stats = {cat: {l: 0 for l in problem_keys} for cat in target_cats}
    
    extra_stats = {"鉄則本": 0, "典型90問": 0}
    global others_total
    others_total = 0
    daily_ac = {}
    color_counts = {}
    diff_values = []

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
                        elif dv < 1600: e = "💧"
                        elif dv < 2000: e = "🔵"
                        elif dv < 2400: e = "🟡"
                        elif dv < 2800: e = "🟠"
                        else: e = "🔴"
                        color_counts[e] = color_counts.get(e, 0) + 1
                    
                    # 統計辞書にあるカテゴリのみカウント (ABC, ARC, AGC, AWC)
                    if cat in stats:
                        if label in ["A","B","C","D","E","F","G"]: stats[cat][label] += 1
                        elif label == "EX": stats[cat]["EX"] += 1
                        else: stats[cat]["Other"] += 1
                    else:
                        # 万が一辞書にないカテゴリがマッチした場合(基本ないはずだが安全策)
                        others_total += 1
                        
            elif "鉄則" in first:
                cnt = max(1, len(words)-1); extra_stats["鉄則本"] += cnt; ac_count = cnt
            elif "典型" in first:
                cnt = max(1, len(words)-1); extra_stats["典型90問"] += cnt; ac_count = cnt
            else:
                others_total += 1; ac_count = 1
            if ac_count > 0: daily_ac[d_key] = daily_ac.get(d_key, 0) + ac_count

    if not daily_ac:
        await ctx.send("該当期間の記録が見つかりませんでした。")
        return

    # --- グラフ描画 ---
    plt.style.use('ggplot')
    files = []
    df = pd.DataFrame(list(daily_ac.items()), columns=['date', 'count']).sort_values('date')
    df['date'] = pd.to_datetime(df['date'])
    df['cum'] = df['count'].cumsum()
    
    fig1, ax1 = plt.subplots(figsize=(10, 5))
    ax1.bar(df['date'], df['count'], color='#4682B4', alpha=0.9)
    ax1.set_ylabel('Daily AC')
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1_t = ax1.twinx()
    ax1_t.plot(df['date'], df['cum'], color='#FF8C00', marker='o', linewidth=2)
    ax1_t.set_ylabel('Total AC')
    ax1_t.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.set_title(f"Activity: {member.display_name}")
    plt.tight_layout()
    buf1 = io.BytesIO()
    plt.savefig(buf1, format='png', dpi=100)
    buf1.seek(0)
    files.append(discord.File(buf1, "activity.png"))
    plt.close(fig1)

    if diff_values:
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        bw = 100
        upper_bound = max(400, (int(max(diff_values)) // bw + 1) * bw)
        bins = range(0, upper_bound + bw + bw, bw)
        out = pd.cut(diff_values, bins=bins, right=False)
        bc = out.value_counts().sort_index()
        xc = [e + bw/2 for e in bins[:-1]]
        cols = [get_atcoder_color(e) for e in bins[:-1]]
        ax2.bar(xc, bc.values, width=bw, color=cols, edgecolor='black')
        ax2.set_title("Difficulty Distribution")
        ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
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
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: TOKEN not found.")