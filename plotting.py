"""Independent Figures, rendered on Agg. Serialize matplotlib across worker threads."""
import io
import threading
import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from formatting import COLORS, get_atcoder_color

PLOT_LOCK = threading.Lock()


def figure(title):
    fig = Figure(figsize=(10, 5), dpi=130)
    ax = fig.add_axes([0.10, 0.22, 0.86, 0.66])
    ax.set_title(title, fontsize=19)
    ax.set_ylabel('First ACs', fontsize=15)
    ax.tick_params(labelsize=13)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True, min_n_ticks=2))
    ax.set_axisbelow(True)
    ax.grid(axis='y', alpha=.25)
    for spine in ax.spines.values():
        spine.set_visible(True)
    return fig, ax


def stats_figures(data):
    fig_a, ax_a = figure(data['title'])
    fig_b, ax_b = figure('Total Effort')
    x = list(range(len(data['labels'])))
    bottom = [0] * len(x)
    for i, col in enumerate(COLORS + ['#b0b0b0']):
        heights = [row[i] for row in data['values']]
        ax_a.bar(x, heights, bottom=bottom, color=col,
                 edgecolor='#666666' if i == 8 else 'none',
                 linewidth=0 if i != 8 else .4, hatch='///' if i == 8 else None)
        bottom = [b + h for b, h in zip(bottom, heights)]
    if len(x) <= 16:
        for index, count in zip(x, data['counts']):
            ax_a.text(index, count + .04, str(count), ha='center', va='bottom', fontsize=13)
    ax_a.set_ylim(0, max(1, max(bottom, default=0)) * 1.22 + .3)
    # A separate point before the first bucket makes the period baseline explicit.
    ax_b.plot(range(-1, len(x)), data['cumulative'], color='#FF8C00', marker='o', linewidth=2.5)
    ax_b.set_ylim(-max(1, data['total']) * .06, max(1, data['total']) * 1.15 + .3)
    ticks = list(range(0, max(1, data['total']) + 1, max(1, (data['total'] + 5) // 6)))
    ax_b.set_yticks(ticks)
    for ax in (ax_a, ax_b):
        stride = max(1, (len(x) + 7) // 8)
        positions = x[::stride]
        ax.set_xticks(positions, [data['labels'][i].isoformat() for i in positions], rotation=30, ha='right')
        ax.set_xlim(-1.4, max(.6, len(x) - .4))
    return fig_a, fig_b


def png(fig):
    output = io.BytesIO()
    fig.savefig(output, format='png', dpi=130)
    output.seek(0)
    return output


def render_stats(data):
    with PLOT_LOCK:
        return tuple(png(fig) for fig in stats_figures(data))


def histogram_figure(values, user):
    # Preserve the original 100-difficulty bins and pale rating-band background.
    fig, ax = figure(f'Difficulty Distribution ({user}, AC: {len(values)})')
    upper = max(400, (max(values, default=0) // 100 + 1) * 100)
    starts = list(range(0, upper, 100))
    counts = [0] * len(starts)
    for value in values:
        counts[value // 100] += 1
    for i, col in enumerate(COLORS):
        low = i * 400
        high = (i + 1) * 400 if i < 7 else upper
        if low < upper:
            ax.axvspan(low, min(high, upper), facecolor=col, alpha=.15, zorder=1)
    ax.bar([s + 50 for s in starts], counts, width=100,
           color=[get_atcoder_color(s) for s in starts], edgecolor='black', linewidth=.5, zorder=3)
    for s, count in zip(starts, counts):
        if count:
            ax.text(s + 50, count, str(count), ha='center', va='bottom', fontsize=11)
    ax.set_xlim(0, upper)
    ax.set_ylim(0, max(1, max(counts, default=0)) * 1.2 + .3)
    ax.set_xlabel('Difficulty', fontsize=15)
    return fig


def render_histogram(values, user):
    with PLOT_LOCK:
        return png(histogram_figure(values, user))
