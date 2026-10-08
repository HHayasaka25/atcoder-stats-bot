"""Difficulty utilities reused from the original bot and safe embed formatting."""
from datetime import datetime, timedelta, timezone
import math
import re
from urllib.parse import quote

JST = timezone(timedelta(hours=9))
COLORS = ['#808080', '#804000', '#008000', '#00C0C0', '#0000FF', '#C0C000', '#FF8000', '#FF0000']
EMOJIS = ['⚪', '🟤', '🟢', '💧', '🔵', '🟡', '🟠', '🔴']


def get_display_difficulty(raw_diff):
    if raw_diff is None or not isinstance(raw_diff, (int, float)) or not math.isfinite(raw_diff):
        return None
    if raw_diff >= 400:
        return int(raw_diff)
    # Equivalent to 400 / exp(1 - raw / 400), without overflow for very low values.
    return int(400 * math.exp(raw_diff / 400 - 1))


def band(diff):
    return min(7, max(0, diff // 400)) if diff is not None else 8


def get_atcoder_color(diff):
    return COLORS[band(diff)] if diff is not None else '#b0b0b0'


def get_emoji_for_diff(diff):
    return EMOJIS[band(diff)] if diff is not None else ''


def difficulty(pid, models):
    model = models.get(pid, {})
    raw = model.get('difficulty')
    if raw is None:
        raw = model.get('experimental_difficulty')
    return get_display_difficulty(raw)


def safe_label(value):
    # Resource strings never become mention/Markdown syntax.
    return re.sub(r'[^A-Za-z0-9 ._-]', '', str(value))[:80] or 'Problem'


def problem_label(row, problems):
    pid, cid = row['problem_id'], row['contest_id']
    info = problems.get(pid, {})
    index = info.get('problem_index')
    if index:
        return safe_label(cid.upper() + ' ' + str(index))
    suffix = pid[len(cid) + 1:] if pid.startswith(cid + '_') else None
    # Numeric suffixes in old ABCs are not always the displayed problem index.
    if suffix and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', suffix):
        return safe_label(cid.upper() + ' ' + suffix.upper())
    return safe_label(pid.upper())


def problem_line(row, models, problems):
    pid, cid = row['problem_id'], row['contest_id']
    url = f'https://atcoder.jp/contests/{quote(cid, safe="")}/tasks/{quote(pid, safe="")}'
    line = f'[{problem_label(row, problems)}]({url})'
    value = difficulty(pid, models)
    return line + (f' {get_emoji_for_diff(value)} {value}' if value is not None else '')


def utf16_length(text):
    return len(text.encode('utf-16-le')) // 2


def make_update(user, rows, models, problems):
    title = f'AC update — {user}（直近50件まで）'
    rows = sorted(rows, key=lambda r: (r['epoch_second'], r['submission_id']))[-50:]

    def body(selected):
        lines, previous = [], None
        for row in selected:
            day = datetime.fromtimestamp(row['epoch_second'], JST).date().isoformat()
            if day != previous:
                if lines:
                    lines.append('')
                lines.append(day)
                previous = day
            lines.append(problem_line(row, models, problems))
        return '\n'.join(lines)

    description = body(rows)
    while rows and (utf16_length(description) > 4096 or utf16_length(title + description) > 6000):
        rows = rows[1:]
        description = body(rows)
    return {'title': title, 'description': description}, rows
