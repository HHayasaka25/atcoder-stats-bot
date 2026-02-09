import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import pandas as pd
import matplotlib
# バックエンドをAggに設定（GUIのないサーバーでのエラー回避）
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import numpy as np
import io
import os
import re
import math
from datetime import datetime, timedelta, timezone

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

# --- Bot設定 ---
class AtCoderBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()

intents = discord.Intents.default()
intents.message_content = True
bot = AtCoderBot(command_prefix="/", intents=intents)

# 問題モデルデータ
PROBLEM_MODELS = {}

# --- 難易度・色関連のユーティリティ ---
def get_display_difficulty(raw_diff):
    if raw_diff is None: return 0
    if raw_diff >= 400:
        return int(raw_diff)
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

def get_emoji_for_diff(dv):
    if dv < 400: return "⚪"
    elif dv < 800: return "🟤"
    elif dv < 1200: return "🟢"
    elif dv < 1600: return "💧" # 水色 (変更)
    elif dv < 2000: return "🔵" # 青色 (変更)
    elif dv < 2400: return "🟡"
    elif dv < 2800: return "🟠"
    else: return "🔴"

# --- データ取得ロジック (aiohttp) ---
async def fetch_api_data():
    """起動時および定期実行用: 問題情報を取得"""
    global PROBLEM_MODELS
    url = "https://kenkoooo.com/atcoder/resources/problem-models.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as res:
                if res.status == 200:
                    PROBLEM_MODELS = await res.json()
                    print(f"✅ Data fetched: {len(PROBLEM_MODELS)} problems.")
                    return True
                else:
                    print(f"⚠️ Failed to load models: {res.status}")
                    return False
    except Exception as e:
        print(f"❌ Error loading models: {e}")
        return False

@tasks.loop(hours=24)
async def update_data_task():
    await fetch_api_data()

async def fetch_user_submissions(user_id):
    """ユーザーの全提出データを取得 (ページネーション対応)"""
    submissions = []
    current_second = 0
    base_url = "https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions"
    
    async with aiohttp.ClientSession() as session:
        while True:
            url = f"{base_url}?user={user_id}&from_second={current_second}"
            try:
                async with session.get(url) as res:
                    if res.status != 200:
                        print(f"API Error: {res.status}")
                        break
                    
                    batch = await res.json()
                    if not batch:
                        break
                    
                    submissions.extend(batch)
                    current_second = batch[-1]['epoch_second'] + 1
                    await asyncio.sleep(0.5) # API負荷軽減
                    
                    if len(batch) < 500:
                        break
            except Exception as e:
                print(f"Fetch error: {e}")
                break
    return submissions

# ==========================================
# コマンド1: /update_diff (手動更新)
# ==========================================
@bot.hybrid_command(name="update_diff", description="AtCoder Problemsの難易度データを手動更新します")
async def update_diff(ctx):
    await ctx.defer()
    success = await fetch_api_data()
    if success:
        await ctx.send(f"✅ Difficultyデータを更新しました！ (取得数: {len(PROBLEM_MODELS)}件)")
    else:
        await ctx.send("❌ データの取得に失敗しました。")

# ==========================================
# コマンド2: /diffhist (APIベース全期間ヒストグラム)
# ==========================================
@bot.hybrid_command(name="diffhist", description="指定したユーザーの全期間Diff分布を表示します(AtCoder API使用)")
@app_commands.describe(user_id="AtCoder ID")
async def diffhist(ctx, user_id: str):
    if TARGET_CHANNEL_ID and ctx.channel.id != TARGET_CHANNEL_ID:
        if ctx.interaction: await ctx.send("このチャンネルでは使用できません。", ephemeral=True)
        return
    await ctx.defer()
    
    # データ取得
    raw_subs = await fetch_user_submissions(user_id)
    if not raw_subs:
        await ctx.send(f"❌ ユーザー `{user_id}` のデータが見つかりませんでした。")
        return

    df_sub = pd.DataFrame(raw_subs)
    if 'result' not in df_sub.columns:
        await ctx.send(f"❌ データ形式エラー")
        return
        
    df_ac = df_sub[df_sub['result'] == 'AC'].copy()
    if df_ac.empty:
        await ctx.send(f"ℹ️ `{user_id}` はまだACしていません。")
        return

    # AHC除外 & 重複除外
    df_ac = df_ac[~df_ac['contest_id'].astype(str).str.startswith('ahc')]
    df_ac = df_ac.drop_duplicates(subset=['problem_id'])
    
    # 難易度情報付与
    diff_values = []
    for pid in df_ac['problem_id']:
        model = PROBLEM_MODELS.get(pid)
        val = None
        if model:
            if model.get('difficulty') is not None:
                val = model['difficulty']
            elif model.get('experimental_difficulty') is not None:
                val = model['experimental_difficulty']
        
        if val is not None:
            diff_values.append(get_display_difficulty(val))
    
    if not diff_values:
        await ctx.send(f"ℹ️ `{user_id}` のDifficulty付きACデータがありません。")
        return

    # グラフ描画 (最新デザイン適用)
    plt.style.use('ggplot')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    bw = 100
    upper_bound = max(400, (int(max(diff_values)) // bw + 1) * bw)
    bins = range(0, upper_bound + bw + bw, bw)
    
    out = pd.cut(diff_values, bins=bins, right=False)
    bc = out.value_counts().sort_index()
    xc = [e + bw/2 for e in bins[:-1]]
    cols = [get_atcoder_color(e) for e in bins[:-1]]
    
    # 背景色 (レート帯)
    RATE_COLORS = [
        (0, 400, '#808080'), (400, 800, '#804000'), (800, 1200, '#008000'),
        (1200, 1600, '#00C0C0'), (1600, 2000, '#0000FF'), (2000, 2400, '#C0C000'),
        (2400, 2800, '#FF8000'), (2800, 10000, '#FF0000')
    ]
    
    x_limit = upper_bound + bw
    for low, high, col in RATE_COLORS:
        if low < x_limit:
            ax.axvspan(max(0, low), min(x_limit, high), facecolor=col, alpha=0.15, zorder=1)

    ax.bar(xc, bc.values, width=bw, color=cols, edgecolor='black', zorder=3)
    ax.set_title(f"Difficulty Distribution (User: {user_id}, AC: {len(diff_values)})")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlim(left=0, right=x_limit)
    
    # 上部に余白
    if len(bc) > 0:
        ax.set_ylim(0, bc.max() * 1.1)
    else:
        ax.set_ylim(bottom=0)
    
    ax.set_axisbelow(True)
    ax.grid(axis='x', visible=False) # 縦線なし
    ax.tick_params(length=0)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png')
    buf.seek(0)
    files = [discord.File(buf, "diff_hist.png")]
    plt.close(fig)
    
    await ctx.send(files=files)


# ==========================================
# コマンド3: /atcoder (チャット履歴集計)
# ==========================================
# テキスト整形用
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
        "Header":   7, "ABC": 7, "ARC": 7, "AGC": 7, "AWC": 7,
        "鉄則本": 8, "典型90問": 8, "Others": 7, "Sum": 7,
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
        for v in vals: row += f"{str(v):>{dw}}|"
        row += f"{total:>{dw}}" 
        return row

    labels = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    # AWCを追加
    for cat in ["ABC", "ARC", "AGC", "AWC"]:
        counts = [stats[cat].get(l, 0) for l in labels]
        lines.append(make_row(cat, counts, sum(counts)))

    hyphen_cell = f"{'-':^{dw}}|"
    hyphens_9 = hyphen_cell * 9
    for name in ["鉄則本", "典型90問"]:
        val = extra_stats.get(name, 0)
        w = WIDTH_CONFIG.get(name, 8)
        lines.append(pad_str(name, w) + "|" + hyphens_9 + f"{val:>{dw}}")

    w_oth = WIDTH_CONFIG.get("Others", 7)
    lines.append(pad_str("Others", w_oth) + "|" + hyphens_9 + f"{others_count:>{dw}}")

    lines.append("-" * len(line))
    w_sum = WIDTH_CONFIG.get("Sum", 7)
    spacer_width = (dw + 1) * 9
    lines.append(pad_str("Sum", w_sum) + "|" + " " * spacer_width + f"{grand_total:>{dw}}")

    # 色順序 (水色💧、青🔵)
    color_order = ["🔴", "🟠", "🟡", "🔵", "💧", "🟢", "🟤", "⚪"]
    color_parts = [f"{emoji}{color_counts.get(emoji,0)}" for emoji in color_order if color_counts.get(emoji,0) > 0]
    
    text_table = "```text\n" + "\n".join(lines) + "\n```"
    if color_parts: text_table += f"\nDifficulty: {' '.join(color_parts)}"
    return text_table

# AWCを追加した正規表現
CONTEST_HEAD_PATTERN = re.compile(r'^(ABC|ARC|AGC|AWC)(\d+)$', re.IGNORECASE)

@bot.hybrid_command(name="atcoder", description="チャット履歴から精進を集計します")
@app_commands.describe(member="ユーザー", period="期間(all/week/range)", start_date="開始日", end_date="終了日")
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
            if end_date: until = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=JST)
    except ValueError:
        await ctx.send("日付形式エラー: YYYY-MM-DD")
        return

    problem_keys = ["A", "B", "C", "D", "E", "F", "G", "EX", "Other"]
    # 統計用辞書にAWC追加
    target_cats = ["ABC", "ARC", "AGC", "AWC"]
    stats = {cat: {l: 0 for l in problem_keys} for cat in target_cats}
    extra_stats = {"鉄則本": 0, "典型90問": 0}
    others_total = 0
    daily_ac = {}
    color_counts = {}
    diff_values = []

    async for message in ctx.channel.history(limit=5000):
        if message.author != member: continue
        msg_date = message.created_at.astimezone(JST) if message.created_at.tzinfo else message.created_at.replace(tzinfo=timezone.utc).astimezone(JST)
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
                        e = get_emoji_for_diff(dv)
                        color_counts[e] = color_counts.get(e, 0) + 1
                    
                    if cat in stats:
                        if label in problem_keys: stats[cat][label] += 1
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

    if not daily_ac and not diff_values:
        await ctx.send("該当期間の記録が見つかりませんでした。")
        return

    grand_total = sum(daily_ac.values())
    files = []
    
    # [グラフ1: Activity]
    if daily_ac:
        plt.style.use('ggplot')
        df = pd.DataFrame(list(daily_ac.items()), columns=['date', 'count']).sort_values('date')
        df['date'] = pd.to_datetime(df['date'])
        df['cum'] = df['count'].cumsum()
        
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

        # 軸同期
        max_d = df['count'].max()
        max_c = df['cum'].max()
        if max_d == 0: max_d = 1
        if max_c == 0: max_c = 1
        max_d *= 1.1
        max_c *= 1.1
        
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
        plt.savefig(buf1, format='png')
        buf1.seek(0)
        files.append(discord.File(buf1, "activity.png"))
        plt.close(fig1)

    # [グラフ2: Diff分布] (最新デザイン)
    if diff_values:
        fig2, ax2 = plt.subplots(figsize=(10, 5))
        bw = 100
        upper_bound = max(400, (int(max(diff_values)) // bw + 1) * bw)
        bins = range(0, upper_bound + bw + bw, bw)
        
        out = pd.cut(diff_values, bins=bins, right=False)
        bc = out.value_counts().sort_index()
        xc = [e + bw/2 for e in bins[:-1]]
        cols = [get_atcoder_color(e) for e in bins[:-1]]
        
        # 背景色
        RATE_COLORS = [
            (0, 400, '#808080'), (400, 800, '#804000'), (800, 1200, '#008000'),
            (1200, 1600, '#00C0C0'), (1600, 2000, '#0000FF'), (2000, 2400, '#C0C000'),
            (2400, 2800, '#FF8000'), (2800, 10000, '#FF0000')
        ]
        
        x_limit = upper_bound + bw
        for low, high, col in RATE_COLORS:
            if low < x_limit:
                ax2.axvspan(max(0, low), min(x_limit, high), facecolor=col, alpha=0.15, zorder=1)

        ax2.bar(xc, bc.values, width=bw, color=cols, edgecolor='black', zorder=3)
        ax2.set_title(f"Difficulty Distribution (AC: {len(diff_values)})")
        ax2.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax2.set_xlim(left=0, right=x_limit)
        
        if len(bc) > 0:
            ax2.set_ylim(0, bc.max() * 1.1)
        else:
            ax2.set_ylim(bottom=0)
        
        ax2.set_axisbelow(True)
        ax2.grid(axis='x', visible=False) # 縦線なし
        ax2.tick_params(length=0)

        plt.tight_layout()
        buf2 = io.BytesIO()
        plt.savefig(buf2, format='png')
        buf2.seek(0)
        files.append(discord.File(buf2, "difficulty.png"))
        plt.close(fig2)

    await ctx.send(content=create_text_table(stats, extra_stats, others_total, color_counts, grand_total), files=files)

# --- 起動処理 ---
@bot.event
async def on_ready():
    print(f'Logged in: {bot.user.name}')
    await fetch_api_data()
    if not update_data_task.is_running():
        update_data_task.start()

if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("ERROR: DISCORD_TOKEN not found.")