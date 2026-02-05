import discord
from discord.ext import commands, tasks
import re
import io
import os
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from datetime import datetime, timedelta, timezone

# ================= 設定エリア =================
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = int(os.getenv("TARGET_CHANNEL_ID")) if os.getenv("TARGET_CHANNEL_ID") else None
JST = timezone(timedelta(hours=9))
# =============================================

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# キャッシュ変数
PROBLEM_MODELS = {}
PROBLEMS = {}

# AtCoderの色定義 (Difficulty -> Hex Color)
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
    """AtCoder Problems APIからデータを取得してキャッシュする"""
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

@bot.command(name="reload_data")
@commands.is_owner()
async def reload_data(ctx):
    success = fetch_api_data()
    if success:
        await ctx.send("✅ 最新の問題データを読み込みました。")
    else:
        await ctx.send("❌ データの取得に失敗しました。")

CONTEST_HEAD_PATTERN = re.compile(r'^(ABC|ARC|AGC)(\d+)$', re.IGNORECASE)

def create_markdown_table(stats, extra_stats, others_count, color_counts):
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
    except ValueError:
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

            lines = message.content.split('\n')
            for line in lines:
                words = line.strip().split()
                if not words: continue

                first_word = words[0]
                d_key = msg_date.date()
                ac_count = 0

                contest_match = CONTEST_HEAD_PATTERN.match(first_word)
                if contest_match:
                    cat = contest_match.group(1).upper()
                    contest_num = contest_match.group(2)
                    problems = words[1:]
                    ac_count = len(problems)
                    for p in problems:
                        p_label = p.upper()
                        p_id = f"{cat.lower()}{contest_num}_{p_label.lower()}"
                        
                        # 難易度取得
                        model = PROBLEM_MODELS.get(p_id)
                        if model and 'difficulty' in model:
                            d_val = model['difficulty']
                            diff_values.append(d_val)
                            
                            # 色絵文字の集計 (表用)
                            if d_val < 400: emoji = "⚪"
                            elif d_val < 800: emoji = "🟤"
                            elif d_val < 1200: emoji = "🟢"
                            elif d_val < 1600: emoji = "🔵"
                            elif d_val < 2000: emoji = "🟦"
                            elif d_val < 2400: emoji = "🟡"
                            elif d_val < 2800: emoji = "🟠"
                            else: emoji = "🔴"
                            color_counts[emoji] = color_counts.get(emoji, 0) + 1

                        if p_label in ["A", "B", "C", "D", "E", "F", "G"]:
                            stats[cat][p_label] += 1
                        elif p_label == "EX":
                            stats[cat]["EX"] += 1
                        else:
                            stats[cat]["Other"] += 1
                elif "鉄則" in first_word:
                    count = max(1, len(words) - 1)
                    extra_stats["鉄則本"] += count
                    ac_count = count
                elif "典型" in first_word:
                    count = max(1, len(words) - 1)
                    extra_stats["典型90問"] += count
                    ac_count = count
                else:
                    others_total += 1
                    ac_count = 1
                
                if ac_count > 0:
                    daily_ac[d_key] = daily_ac.get(d_key, 0) + ac_count

    if not daily_ac:
        await ctx.send(f"{member.display_name} さんの記録が見つかりませんでした。")
        return

    # --- グラフ描画 ---
    plt.style.use('ggplot')
    fig, (ax1, ax_diff) = plt.subplots(2, 1, figsize=(10, 10))
    
    # 上段: 時系列
    df = pd.DataFrame(list(daily_ac.items()), columns=['date', 'count']).sort_values('date')
    df['date'] = pd.to_datetime(df['date'])
    df['cumulative'] = df['count'].cumsum()
    
    ax1.bar(df['date'], df['count'], color='#4682B4', alpha=0.7, label='Daily AC')
    ax1.set_ylabel('Daily AC Count')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax1.set_title(f"Activity Over Time: {member.display_name}")
    
    ax1_twin = ax1.twinx()
    ax1_twin.plot(df['date'], df['cumulative'], color='#FF8C00', marker='o', linewidth=2, label='Total AC')
    ax1_twin.set_ylabel('Total AC Count')
    ax1_twin.grid(False)

    # 下段: 難易度分布
    if diff_values:
        bin_width = 100
        min_bin = (min(diff_values) // bin_width) * bin_width
        max_bin = (max(diff_values) // bin_width + 1) * bin_width
        bins = range(min_bin, max_bin + bin_width, bin_width)
        
        counts, edges = pd.cut(diff_values, bins=bins, right=False, retbins=True)
        bin_counts = counts.value_counts().sort_index()
        
        x_centers = [edge + bin_width/2 for edge in edges[:-1]]
        bar_colors = [get_atcoder_color(edge) for edge in edges[:-1]]
        
        ax_diff.bar(x_centers, bin_counts.values, width=bin_width*0.8, color=bar_colors, edgecolor='black', alpha=0.8)
        ax_diff.set_xlabel('Difficulty')
        ax_diff.set_ylabel('Number of Problems')
        ax_diff.set_title('Difficulty Distribution')
        ax_diff.set_xticks(range(min_bin, max_bin + bin_width, 400))
    else:
        ax_diff.text(0.5, 0.5, 'No Difficulty Data Available', ha='center', va='center')

    fig.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    file = discord.File(buf, filename="atcoder_stats.png")

    table_str = create_markdown_table(stats, extra_stats, others_total, color_counts)
    await ctx.send(content=f"📊 **{member.display_name}** の集計結果\n{table_str}", file=file)
    plt.close()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    fetch_api_data()
    if not update_data_task.is_running():
        update_data_task.start()

if TOKEN:
    bot.run(TOKEN)