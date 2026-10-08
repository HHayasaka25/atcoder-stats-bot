"""Independent Figures, rendered on Agg. Serialize matplotlib across worker threads."""
import io
import threading
import matplotlib
matplotlib.use('Agg')
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from formatting import COLORS, get_atcoder_color

PLOT_LOCK = threading.Lock()


def figure(title, *, rectangle=(0.10, 0.22, 0.86, 0.66)):
    fig = Figure(figsize=(10, 5), dpi=130)
    ax = fig.add_axes(rectangle)
    ax.set_title(title, fontsize=19)
    ax.set_ylabel('AC count', fontsize=15)
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
    # Total Effort always has a daily axis, regardless of the bar aggregation.
    total_x = list(range(len(data['total_labels'])))
    ax_b.plot(range(-1, len(total_x)), data['cumulative'], color='#FF8C00', marker='o', linewidth=2.5)
    upper = max(1, data['total']) * 1.15 + .3
    ax_b.set_ylim(-max(1, data['total']) * .06, upper)
    # Continue the same tick/grid spacing through the headroom above the data.
    step = max(1, (data['total'] + 5) // 6)
    ticks = list(range(0, int(upper) + 1, step))
    ax_b.set_yticks(ticks)
    for ax, positions_all, labels in ((ax_a, x, data['labels']), (ax_b, total_x, data['total_labels'])):
        stride = max(1, (len(positions_all) + 7) // 8)
        positions = positions_all[::stride]
        ax.set_xticks(positions, [labels[i].isoformat() for i in positions], rotation=30, ha='right')
        # Keep endpoint markers clear of the frame even for years of daily points.
        padding = len(positions_all) * .015
        ax.set_xlim(-1 - max(.4, padding), len(positions_all) - 1 + max(.6, padding))
    return fig_a, fig_b


def png(fig):
    output = io.BytesIO()
    fig.savefig(output, format='png', dpi=130)
    output.seek(0)
    return output


def render_stats(data, values, user):
    with PLOT_LOCK:
        figures = (*stats_figures(data), histogram_figure(values, user))
        return tuple(png(fig) for fig in figures)


def histogram_figure(values, user):
    # Preserve the original 100-difficulty bins and pale rating-band background.
    # Histogram ticks need less bottom space than the rotated date labels.
    # A second title line keeps long IDs inside the image.
    fig, ax = figure(f'Difficulty Distribution\n{user} | AC: {len(values)}',
                     rectangle=(0.10, 0.14, 0.86, 0.70))
    upper = max(400, (max(values, default=0) // 100 + 1) * 100)
    x_limit = upper + 100
    starts = list(range(0, upper, 100))
    counts = [0] * len(starts)
    for value in values:
        counts[value // 100] += 1
    for i, col in enumerate(COLORS):
        low = i * 400
        high = (i + 1) * 400 if i < 7 else x_limit
        if low < x_limit:
            ax.axvspan(low, min(high, x_limit), facecolor=col, alpha=.15, zorder=1)
    ax.bar([s + 50 for s in starts], counts, width=100,
           color=[get_atcoder_color(s) for s in starts], edgecolor='black', linewidth=.5, zorder=3)
    for s, count in zip(starts, counts):
        if count:
            ax.text(s + 50, count, str(count), ha='center', va='bottom', fontsize=11)
    ax.set_xlim(0, x_limit)
    ax.set_ylim(0, max(1, max(counts, default=0) * 1.15 + .15))
    ax.set_xlabel('Difficulty', fontsize=15)
    return fig


def render_histogram(values, user):
    with PLOT_LOCK:
        return png(histogram_figure(values, user))
