"""JST-first aggregation shared by stats and the lifetime histogram."""
from datetime import datetime, timedelta
import pandas as pd
from formatting import JST, band, difficulty


def aggregate(rows, models, period='weekly', aggregation='monthly', now=None):
    if period not in ('weekly', 'monthly', 'all') or aggregation not in ('daily', 'weekly', 'monthly'):
        raise ValueError('期間または集計単位が不正です。')
    now = now or datetime.now(JST)
    today = now.astimezone(JST).date()
    dated = [(datetime.fromtimestamp(r['epoch_second'], JST).date(), r) for r in rows
             if r['epoch_second'] <= now.timestamp()]
    if period == 'all':
        selected_start = min((d for d, _ in dated), default=today)
        start = selected_start
        if aggregation == 'weekly':
            start -= timedelta(days=start.weekday())
            end = today - timedelta(days=today.weekday())
            labels = list(pd.date_range(start, end, freq='7D').date)
            bucket = lambda d: d - timedelta(days=d.weekday())
            title = 'Weekly Effort'
        elif aggregation == 'monthly':
            start = start.replace(day=1)
            labels = list(pd.date_range(start, today.replace(day=1), freq='MS').date)
            bucket = lambda d: d.replace(day=1)
            title = 'Monthly Effort'
        else:
            labels = list(pd.date_range(start, today, freq='D').date)
            bucket = lambda d: d
            title = 'Daily Effort'
    else:
        start = today - timedelta(days=6 if period == 'weekly' else 29)
        selected_start = start
        labels = list(pd.date_range(start, today, freq='D').date)
        bucket = lambda d: d
        title = 'Daily Effort'
    values = [[0] * 9 for _ in labels]
    indices = {d: i for i, d in enumerate(labels)}
    total_labels = list(pd.date_range(selected_start, today, freq='D').date)
    daily_counts = [0] * len(total_labels)
    for day, row in dated:
        if selected_start <= day <= today:
            values[indices[bucket(day)]][band(difficulty(row['problem_id'], models))] += 1
            daily_counts[(day - selected_start).days] += 1
    counts = [sum(v) for v in values]
    cumulative, total = [0], 0
    for count in daily_counts:
        total += count
        cumulative.append(total)
    return {'labels': labels, 'values': values, 'counts': counts,
            'total_labels': total_labels, 'daily_counts': daily_counts,
            'cumulative': cumulative, 'total': total, 'title': title}


def histogram_values(rows, models):
    return [v for row in rows if (v := difficulty(row['problem_id'], models)) is not None]
