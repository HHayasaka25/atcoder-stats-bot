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
TOKEN = os.getenv("DISCORD_TOKEN")

try:
    target_id_raw = os.getenv("TARGET_CHANNEL_ID")
    TARGET_CHANNEL_ID = int(target_id_raw) if target_id_raw else None
except ValueError:
    TARGET_CHANNEL_ID = None
    print("Warning: TARGET_CHANNEL_ID is not a valid integer.")

JST = timezone(timedelta(hours=9))
# =============================================

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
    return s + " " * max(0, width - w)

def create_text_table(stats, extra_stats, others_count, color_counts, grand_total):
    WIDTH_CONFIG = {
        "Header":   7,
        "ABC":      7,
        "ARC":      7,
        "AGC":      7,
        "AWC":      7,
        "鉄則本":     8,
        "典型90問":   8,
        "Others":   7,
        "Sum":      7,
    }

    dw = 3
    hw = WIDTH_CONFIG.get("Header", 7)
    cols = [" A ", " B ", " C ", " D ", " E ", " F ", " G ", " Ex", "Oth", "Sum"]
    header = " " * hw + "|" + "|".join(cols)
    line = "-" * hw + "+" + "+".join(["-" * dw] * 10)
    lines = [header, line]

    def make_row(name, vals, total):
        w = WIDTH_CONFIG.get(name, hw)
        row = pad_str(name, w) + "|"
        for v in vals:
            row += f"{str(v):>{dw}}|"
        row += f"{total:>{dw}}" 
        return row

    labels = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    target_cats = ["ABC", "ARC", "AGC", "AWC"]
    for cat in target_cats:
        counts = [stats[cat].get(l, 0) for l in labels]
        total = sum(counts)
        lines.append(make_row(cat, counts, total))

    hyphen_cell = f"{'-':^{dw}}|"
    hyphens_9 = hyphen_cell * 9
    for name in ["鉄則本", "典型90問"]:
        val = extra_stats.get(name, 0)
        w = WIDTH_CONFIG.get(name, 8)
        row = pad_str(name, w) + "|" + hyphens_9 + f"{val:>{dw}}"
        lines.append(row)

    w_oth = WIDTH_CONFIG.get("Others", 7)
    row = pad_str("Others", w_oth) + "|" + hyphens_9 + f"{others_total:>{dw}}"
    lines.append(row)

    lines.append("-" * len(line))
    w_sum = WIDTH_CONFIG.get("Sum", 7)
    spacer_width = (dw + 1) * 9
    sum_row = pad_str("Sum", w_sum) + "|" + " " * spacer_width + f"{grand_total:>{dw}}"
    lines.append(sum_row)

    # 色順序の定義
    color_order = ["🔴", "🟠", "🟡", "🔵", "💧", "🟢", "🟤", "⚪"]
    color_line = " ".join([f"{emoji}{color_counts.get(emoji, 0)}" for emoji in color_order if color_counts.get(emoji, 0) > 0])
    
    text_table = "```text\n" + "\n".join(lines) + "\n```"
    if color_line: text_table += f"\nDifficulty: {color_line}"
    return text_table

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
                        elif dv < 1600: e = "💧" # 水色
                        elif dv < 2000: e = "🔵" # 青色
                        elif dv < 2400: e = "🟡"
                        elif dv < 2800: e = "🟠"
                        else: e = "🔴"
                        color_counts[e] = color_counts.get(e, 0) + 1
                    
                    if cat in stats:
                        if label in ["A","B","C","D","E","F","G"]: stats[cat][label] += 1
                        elif label == "EX": stats[cat]["EX"] += 1
                        else: stats[cat]["Other"] += 1
                    else:
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

    grand_total = sum(daily_ac.values())

    plt.style.use('ggplot')
    files = []
    df = pd.DataFrame(list(daily_ac.items()), columns=['date', 'count']).sort_values('date')
    df['date'] = pd.to_datetime(df['date'])
    df['cum'] = df['count'].cumsum()
    
    # [グラフ1: Activity]
    fig1, ax1 = plt.subplots(figsize=(10, 5))
    ax1.set_axisbelow(True)
    ax1.bar(df['date'], df['count'], color='#4682B4', alpha=0.9, label='Daily AC', zorder=2)
    ax1.set_ylabel('Daily AC')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(df)//10)))
    ax1.tick_params(axis='x', rotation=45) 
    ax1.tick_params(length=0)

    ax1_t = ax1.twinx()
    ax1_t.plot(df['date'], df['cum'], color='#FF8C00', marker='o', linewidth=2, label='Total AC', zorder=3)
    ax1_t.set_ylabel('Total AC')
    ax1_t.grid(False)
    ax1_t.tick_params(length=0)

    max_d = df['count'].max()
    max_c = df['cum'].max()
    if max_d == 0: max_d = 1
    if max_c == 0: max_c = 1
    max_d = max_d * 1.1
    max_c = max_c * 1.1

    DIVISIONS = 5
    def get_nice_max(value, divs):
        if value == 0: return divs
        return math.ceil(value / divs) * divs

    ylim_d = get_nice_max(max_d, DIVISIONS)
    ylim_c = get_nice_max(max_c, DIVISIONS)

    ax1.set_ylim(0, ylim_d)
    ax1_t.set_ylim(0, ylim_c)
    ax1.set_yticks([i * (ylim_d / DIVISIONS) for i in range(DIVISIONS + 1)])
    ax1_t.set_yticks([i * (ylim_c / DIVISIONS) for i in range(DIVISIONS + 1)])

    ax1.set_title(f"Activity: {member.display_name} (AC: {grand_total})")
    plt.tight_layout()
    buf1 = io.BytesIO()
    plt.savefig(buf1, format='png', dpi=100)
    buf1.seek(0)
    files.append(discord.File(buf1, "activity.png"))
    plt.close(fig1)

    # [グラフ2: Difficulty Distribution]
    if diff_values:
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        bw = 100
        upper_bound = max(400, (int(max(diff_values)) // bw + 1) * bw)
        bins = range(0, upper_bound + bw + bw, bw)
        out = pd.cut(diff_values, bins=bins, right=False)
        bc = out.value_counts().sort_index()
        xc = [e + bw/2 for e in bins[:-1]]
        cols = [get_atcoder_color(e) for e in bins[:-1]]
        
        RATE_COLORS = [
            (0, 400, '#808080'),
            (400, 800, '#804000'),
            (800, 1200, '#008000'),
            (1200, 1600, '#00C0C0'),
            (1600, 2000, '#0000FF'),
            (2000, 2400, '#C0C000'),
            (2400, 2800, '#FF8000'),
            (2800, 10000, '#FF0000'),
        ]
        
        x_limit = upper_bound + bw
        for low, high, col in RATE_COLORS:
            if low < x_limit:
                ax2.axvspan(max(0, low), min(x_limit, high), facecolor=col, alpha=0.15, zorder=1)

        ax2.bar(xc, bc.values, width=bw, color=cols, edgecolor='black', zorder=3)
        ax2.set_title("Difficulty Distribution")
        ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax2.set_xlim(left=0, right=x_limit)
        
        if len(bc) > 0:
            ax2.set_ylim(0, bc.max() * 1.1)
        else:
            ax2.set_ylim(bottom=0)
        
        ax2.set_axisbelow(True)
        ax2.grid(axis='x', visible=False) # 縦の目盛り線を消去
        ax2.tick_params(length=0)

        plt.tight_layout()
        buf2 = io.BytesIO()
        plt.savefig(buf2, format='png', dpi=100)
        buf2.seek(0)
        files.append(discord.File(buf2, "difficulty.png"))
        plt.close(fig2)
    
    await ctx.send(content=create_text_table(stats, extra_stats, others_total, color_counts, grand_total), files=files)

@bot.hybrid_command(name="diffhist", description="難易度分布（ヒストグラム）のみを表示します")
@app_commands.describe(member="集計するユーザー", period="期間 (all, week, range)", start_date="開始日", end_date="終了日")
async def diffhist(ctx, member: discord.Member = None, period: str = "all", start_date: str = None, end_date: str = None):
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
            
            match = CONTEST_HEAD_PATTERN.match(first)
            if match:
                cat, num = match.group(1).upper(), match.group(2)
                problems = words[1:]
                for p in problems:
                    label = p.upper()
                    pid = f"{cat.lower()}{num}_{label.lower()}"
                    model = PROBLEM_MODELS.get(pid)
                    if model and 'difficulty' in model:
                        dv = get_display_difficulty(model['difficulty'])
                        diff_values.append(dv)

    if not diff_values:
        await ctx.send("該当期間のDifficultyデータが見つかりませんでした。")
        return

    plt.style.use('ggplot')
    files = []
    
    fig, ax = plt.subplots(figsize=(10, 5))
    bw = 100
    upper_bound = max(400, (int(max(diff_values)) // bw + 1) * bw)
    bins = range(0, upper_bound + bw + bw, bw)
    out = pd.cut(diff_values, bins=bins, right=False)
    bc = out.value_counts().sort_index()
    xc = [e + bw/2 for e in bins[:-1]]
    cols = [get_atcoder_color(e) for e in bins[:-1]]
    
    RATE_COLORS = [
        (0, 400, '#808080'),
        (400, 800, '#804000'),
        (800, 1200, '#008000'),
        (1200, 1600, '#00C0C0'),
        (1600, 2000, '#0000FF'),
        (2000, 2400, '#C0C000'),
        (2400, 2800, '#FF8000'),
        (2800, 10000, '#FF0000'),
    ]
    
    x_limit = upper_bound + bw
    for low, high, col in RATE_COLORS:
        if low < x_limit:
            ax.axvspan(max(0, low), min(x_limit, high), facecolor=col, alpha=0.15, zorder=1)

    ax.bar(xc, bc.values, width=bw, color=cols, edgecolor='black', zorder=3)
    ax.set_title(f"Difficulty Distribution: {member.display_name} (AC: {len(diff_values)})")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlim(left=0, right=x_limit)
    
    if len(bc) > 0:
        ax.set_ylim(0, bc.max() * 1.1)
    else:
        ax.set_ylim(bottom=0)
    
    ax.set_axisbelow(True)
    ax.grid(axis='x', visible=False) # 縦の目盛り線を消去
    ax.tick_params(length=0)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    buf.seek(0)
    files.append(discord.File(buf, "difficulty.png"))
    plt.close(fig)
    
    await ctx.send(files=files)

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