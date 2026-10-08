from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import discord
import pytest
from ac_statistics import aggregate, histogram_values
from atcoder_bot import AtCoderBot, help_embed, RegisterConfirmation
from formatting import (JST, COLORS, difficulty, get_display_difficulty, make_update,
                        problem_label, problem_line, utf16_length)
from plotting import stats_figures, render_stats, histogram_figure
from conftest import NOW

TODAY = datetime(2026, 10, 8, 12, tzinfo=JST)


def row(pid='abc100_a', date=TODAY, sid=1, contest='abc100'):
    return dict(problem_id=pid, contest_id=contest, submission_id=sid, epoch_second=int(date.timestamp()))


def test_weekly_zero_days_period_cumulative_and_unknown():
    rows = [row(date=TODAY-timedelta(days=10)), row(date=TODAY-timedelta(days=6), sid=2),
            row('abc100_b', sid=3)]
    data = aggregate(rows, {'abc100_a': {'difficulty': 620}}, now=TODAY)
    assert len(data['labels']) == 7
    assert data['counts'] == [1, 0, 0, 0, 0, 0, 1]
    assert data['total'] == 2
    assert data['cumulative'] == [0, 1, 1, 1, 1, 1, 1, 2]
    assert data['values'][-1][8] == 1
    assert data['title'] == 'Daily Effort'


def test_monthly_exact_30_days_and_jst_boundary():
    rows = [row(date=TODAY-timedelta(days=30)), row(date=TODAY-timedelta(days=29))]
    rows.append(row(date=datetime(2026, 10, 7, 15, 1, tzinfo=JST)-timedelta(hours=9)))
    data = aggregate(rows, {}, period='monthly', now=TODAY)
    assert len(data['labels']) == 30 and data['total'] == 2
    utc_last_evening = datetime.fromisoformat('2026-10-07T15:00:00+00:00')
    data = aggregate([row(date=utc_last_evening)], {}, now=TODAY)
    assert data['counts'][-1] == 1


def test_all_weekly_monday_and_empty_weeks():
    rows = [row(date=datetime(2026, 9, 20, 23, tzinfo=JST)), row(date=TODAY)]
    data = aggregate(rows, {}, period='all', aggregation='weekly', now=TODAY)
    assert all(d.weekday() == 0 for d in data['labels'])
    assert data['counts'] == [1, 0, 0, 1]
    assert data['title'] == 'Weekly Effort'
    assert data['cumulative'] == [0, 1, 1, 1, 2]


def test_all_defaults_monthly_and_empty_months():
    rows = [row(date=datetime(2026, 7, 31, 23, tzinfo=JST)), row(date=TODAY)]
    data = aggregate(rows, {}, period='all', now=TODAY)
    assert data['counts'] == [1, 0, 0, 1]
    assert data['title'] == 'Monthly Effort'


@pytest.mark.parametrize('period,aggregation', [('weekly', 'monthly'), ('monthly', 'monthly'), ('all', 'weekly'), ('all', 'monthly')])
def test_empty_history(period, aggregation):
    data = aggregate([], {}, period, aggregation, now=TODAY)
    assert data['total'] == 0 and all(c == 0 for c in data['cumulative'])
    assert len(data['labels']) >= 1


def test_correction_and_histogram_unknown():
    assert get_display_difficulty(-400) == 54
    assert get_display_difficulty(0) == 147
    assert get_display_difficulty(400) == 400
    assert get_display_difficulty(-1000000) == 0
    assert get_display_difficulty(None) is None
    assert get_display_difficulty(float('nan')) is None
    assert difficulty('x', {'x': {'difficulty': None, 'experimental_difficulty': 800}}) == 800
    assert histogram_values([row(), row('unknown')], {'abc100_a': {'difficulty': 620}}) == [620]


def test_graphs_are_independent_and_pngs_small():
    data = aggregate([row()], {'abc100_a': {'difficulty': 620}}, now=TODAY)
    a, b = stats_figures(data)
    assert a is not b
    assert len(a.axes) == len(b.axes) == 1
    assert a.axes[0].get_title() == 'Daily Effort'
    assert b.axes[0].get_title() == 'Total Effort'
    assert b.axes[0].lines[0].get_color() == '#FF8C00'
    assert b.axes[0].lines[0].get_ydata()[0] == 0
    assert a.axes[0].get_ylim()[0] == 0
    assert b.axes[0].get_ylim()[0] < 0
    assert all(s.get_visible() for ax in (a.axes[0], b.axes[0]) for s in ax.spines.values())
    assert a.axes[0].get_legend() is None
    for image in render_stats(data):
        assert image.getvalue().startswith(b'\x89PNG')
        assert len(image.getvalue()) < 1024*1024


def test_graph_annotations_only_16_or_less_and_unknown_hatch():
    short = aggregate([row()], {}, now=TODAY)
    fig, _ = stats_figures(short)
    assert len(fig.axes[0].texts) == 7
    assert any(p.get_hatch() == '///' for p in fig.axes[0].patches)
    long = aggregate([row()], {}, period='monthly', now=TODAY)
    fig, _ = stats_figures(long)
    assert len(fig.axes[0].texts) == 0


def test_large_total_baseline_has_visible_margin():
    data = aggregate([row(str(i)) for i in range(1000)], {}, now=TODAY)
    _, fig = stats_figures(data)
    bottom, top = fig.axes[0].get_ylim()
    assert -bottom / (top-bottom) > .04
    assert all(t >= 0 and int(t) == t for t in fig.axes[0].get_yticks())


def test_histogram_100_bins_and_original_background():
    fig = histogram_figure([0, 99, 100, 620, 3100], 'alice')
    bars = [p for p in fig.axes[0].patches if p.get_alpha() != .15]
    assert all(p.get_width() == 100 for p in bars)
    assert [p.get_height() for p in bars[:2]] == [2, 1]
    assert 'AC: 5' in fig.axes[0].get_title()


def test_update_order_unknown_and_jst_grouping():
    rows = [row(sid=2), row('abc100_b', date=TODAY-timedelta(days=1), sid=1)]
    payload, selected = make_update('alice', rows, {'abc100_a': {'difficulty': 620}}, {})
    assert payload['title'] == 'AC update — alice（直近50件まで）'
    lines = payload['description'].splitlines()
    assert lines[0] == '2026-10-07'
    assert lines[-1].endswith(' 🟤 620')
    assert '[ABC100 A](https://atcoder.jp/contests/abc100/tasks/abc100_a) 🟤 620' == lines[-1]
    assert lines[1].endswith(')')  # Unknown has neither number nor emoji.
    assert not any(line.startswith(('-', '•')) for line in lines)
    assert [r['submission_id'] for r in selected] == [1, 2]


def test_embed_limits_trim_oldest_not_newest():
    rows = [row('abc100_' + 'a'*115 + str(i), sid=i, date=TODAY-timedelta(seconds=50-i)) for i in range(50)]
    payload, selected = make_update('alice', rows, {}, {})
    assert 0 < len(selected) < 50
    assert selected[-1]['submission_id'] == 49
    assert utf16_length(payload['description']) <= 4096
    embed = discord.Embed(**payload)
    assert len(embed) <= 6000
    assert payload['description'].count('https://atcoder.jp/') == len(selected)


def test_problem_labels_metadata_and_safe_fallback():
    assert problem_label(row('abc001_1', contest='abc001'), {'abc001_1': {'problem_index': 'A'}}) == 'ABC001 A'
    assert problem_label(row('typical90_a', contest='typical90'), {'typical90_a': {'problem_index': '001'}}) == 'TYPICAL90 001'
    assert problem_label(row('tessoku_book_a', contest='tessoku-book'), {}) == 'TESSOKU_BOOK_A'
    label = problem_label(row(), {'abc100_a': {'problem_index': '@everyone [x](bad)'}})
    assert '@' not in label and '[' not in label


def test_command_surface_and_help_no_automatic_sync(tmp_path):
    bot = AtCoderBot(str(tmp_path / 'unused.db'), 42)
    commands = bot.tree.get_commands()
    assert [c.name for c in commands] == ['ac']
    group = commands[0]
    assert {c.name for c in group.commands} == {'register', 'update', 'stats', 'diffhist', 'help'}
    stats = group.get_command('stats')
    assert {p.name for p in stats.parameters} == {'atcoder_id', 'period', 'aggregation'}
    assert stats.get_parameter('period').default == 'weekly'
    assert stats.get_parameter('aggregation').default == 'monthly'
    assert len(help_embed().fields) == 5 and len(help_embed()) < 6000
    for name in ('atcoder_bot.py', 'service.py'):
        source = Path(name).read_text()
        assert 'tasks.loop' not in source and 'create_task(' not in source
    assert bot.intents.message_content is False
    assert bot.session is None and bot.db is None  # Construction does not fetch history.


async def test_update_channel_and_guild_validation(tmp_path):
    bot = AtCoderBot(str(tmp_path / 'unused.db'), 42)
    group = bot.tree.get_command('ac')
    interaction = MagicMock(guild_id=10)
    channel = MagicMock(spec=discord.TextChannel)
    channel.guild.id = 10
    bot.get_channel = MagicMock(return_value=channel)
    channel.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    assert await group.target_channel(interaction) is channel
    channel.guild.id = 20
    with pytest.raises(ValueError, match='このサーバー'):
        await group.target_channel(interaction)
    channel.guild.id = 10
    channel.permissions_for.return_value.embed_links = False
    with pytest.raises(ValueError, match='権限'):
        await group.target_channel(interaction)


async def test_confirmation_rejects_other_user():
    view = RegisterConfirmation(MagicMock(), 10, 123, 'bob', 'alice')
    interaction = MagicMock(guild_id=10)
    interaction.user.id = 456
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    assert not await view.interaction_check(interaction)
    interaction.response.send_message.assert_awaited_once()
    interaction.user.id = 123
    assert await view.interaction_check(interaction)


async def test_startup_has_no_history_fetch_and_rejects_second_process(tmp_path):
    path = str(tmp_path / 'singleton.sqlite3')
    first, second = AtCoderBot(path, 42), AtCoderBot(path, 42)
    first.tree.sync, second.tree.sync = AsyncMock(), AsyncMock()
    async with first, second:
        await first.setup_hook()
        assert first.service.db.conn.execute('SELECT COUNT(*) FROM ac_accounts').fetchone()[0] == 0
        first.tree.sync.assert_awaited_once()
        with pytest.raises(RuntimeError, match='既に起動'):
            await second.setup_hook()
        second.tree.sync.assert_not_awaited()
