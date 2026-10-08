"""Discord entry point. All history access is explicitly initiated by /ac."""
import asyncio
import fcntl
import logging
import os
from pathlib import Path
from typing import Literal

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ac_statistics import aggregate, histogram_values
from atcoder_api import APIError, AtCoderAPI, normalize_id
from database import Database, RegistrationError
from plotting import render_stats
from service import ACService

log = logging.getLogger(__name__)


def help_embed():
    embed = discord.Embed(title='AtCoder 初AC管理 — /ac help', color=0xFF8C00)
    descriptions = [
        ('/ac register', 'Discord IDにAtCoder IDを登録し、直近50ACを自分だけに表示します。開催中は表示を保留します。変更時は確認が必要です。\n引数: atcoder_id（必須）\n例: `/ac register atcoder_id:tourist`'),
        ('/ac update', '登録IDの履歴全体を最新化します（取得は48時間重複を含む差分）。全初ACを保存し、公開投稿は直近50問まで・1メッセージです。古い省略分は持ち越しません。開催中は終了まで保留します。\n引数: なし\n例: `/ac update`'),
        ('/ac stats', '自分の登録IDの初AC数・期間内累計・生涯Difficulty分布を3枚の画像で表示します。保存済み履歴を使用します。Total Effortは常に日別、Difficulty分布は100刻みで不明を除外します。\n引数: period=weekly/monthly/all（初期値all）, aggregation=daily/weekly/monthly（棒グラフのallのみ、初期値daily）\n例: `/ac stats`'),
        ('/ac stats_id', '指定したAtCoder IDの統計を表示します。未登録IDだけ全履歴を取得しキャッシュします。Discordとの登録は作りません。\n引数: atcoder_id（必須）, period・aggregation（statsと同じ）\n例: `/ac stats_id atcoder_id:tourist period:all aggregation:monthly`'),
        ('/ac help', 'このヘルプを表示します。\n引数: なし\n例: `/ac help`'),
    ]
    for name, description in descriptions:
        embed.add_field(name=name, value=description, inline=False)
    embed.set_footer(text='自動同期・自動投稿なし。AtCoder所有者の確認は行いません。API反映には遅延があります。')
    return embed


async def private_error(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def show_registration(interaction, service, user, already_registered=False):
    total, payload = await service.registration_preview(user)
    await interaction.followup.send(
        f'{"登録済み" if already_registered else "登録しました"}。AC {total}',
        embed=discord.Embed(**payload, color=0xFF8C00) if payload['description'] else None,
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def report_error(interaction, error):
    original = getattr(error, 'original', error)
    if isinstance(original, (APIError, ValueError)):
        text = str(original)
    elif isinstance(original, discord.HTTPException):
        text = 'Discordへの送信に失敗しました。/ac update の投稿は保存されているため再試行できます。'
    else:
        # Do not log user-controlled content, credentials or HTTP response bodies.
        log.error('Command failed: %s', type(original).__name__)
        text = '処理に失敗しました。ログと設定を確認し、再実行してください。'
    try:
        await private_error(interaction, text)
    except discord.HTTPException:
        log.warning('Interaction response unavailable; command data is retained.')


class RegisterConfirmation(discord.ui.View):
    def __init__(self, service, guild, member, user, expected):
        super().__init__(timeout=120)
        self.service, self.guild, self.member = service, guild, member
        self.user, self.expected = user, expected

    async def interaction_check(self, interaction):
        if interaction.user.id != self.member or interaction.guild_id != self.guild:
            await private_error(interaction, 'この確認はコマンド実行者専用です。')
            return False
        return True

    @discord.ui.button(label='AtCoder IDを変更', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        await interaction.edit_original_response(content='登録変更を処理しています。', view=self)
        self.stop()
        await self.service.register(self.guild, self.member, self.user, self.expected)
        await show_registration(interaction, self.service, self.user)

    @discord.ui.button(label='キャンセル', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content='登録変更をキャンセルしました。', view=None)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)


class ACCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name='ac', description='AtCoder 初ACの登録・更新・統計', guild_only=True)
        self.bot = bot

    @property
    def service(self):
        return self.bot.service

    async def on_error(self, interaction, error):
        await report_error(interaction, error)

    @app_commands.command(name='register', description='AtCoder IDを登録し、直近50ACを自分だけに表示します')
    @app_commands.describe(atcoder_id='登録するAtCoder ID')
    async def register(self, interaction: discord.Interaction, atcoder_id: str):
        user = normalize_id(atcoder_id)
        reg = self.service.db.registration(interaction.guild_id, interaction.user.id)
        expected = reg['atcoder_id'] if reg else None
        self.service.db.check_registration(interaction.guild_id, interaction.user.id, user, expected)
        if expected == user:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.service.register(interaction.guild_id, interaction.user.id, atcoder_id, expected)
            await show_registration(interaction, self.service, atcoder_id, already_registered=True)
            return
        if expected:
            view = RegisterConfirmation(self.service, interaction.guild_id, interaction.user.id, atcoder_id, expected)
            await interaction.response.send_message(
                f'登録IDを `{reg["display_id"]}` から `{atcoder_id}` に変更します。新しいIDの全履歴を取得し、過去ACは投稿しません。変更しますか？',
                view=view, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.service.register(interaction.guild_id, interaction.user.id, atcoder_id)
        await show_registration(interaction, self.service, atcoder_id)

    async def target_channel(self, interaction):
        if not self.bot.target_channel_id:
            raise ValueError('管理者が TARGET_CHANNEL_ID を設定する必要があります。')
        channel = self.bot.get_channel(self.bot.target_channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(self.bot.target_channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
            raise ValueError('このサーバーの精進チャンネルが設定されていません。管理者に確認してください。')
        actor_permissions = channel.permissions_for(interaction.user)
        own_permissions = channel.permissions_for(channel.guild.me)
        if not actor_permissions.view_channel:
            raise ValueError('精進チャンネルを閲覧する権限が必要です。')
        if not (own_permissions.view_channel and own_permissions.send_messages and own_permissions.embed_links):
            raise ValueError('Botに精進チャンネルの閲覧・メッセージ送信・Embedリンク権限が必要です。')
        return channel

    @app_commands.command(name='update', description='履歴を差分更新し、新規初ACを精進チャンネルに直近50問まで投稿します')
    async def update(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await self.target_channel(interaction)

        async def send(payload):
            message = await channel.send(embed=discord.Embed(**payload, color=0xFF8C00),
                                         allowed_mentions=discord.AllowedMentions.none())
            return message.id

        result = await self.service.update(interaction.guild_id, interaction.user.id, send)
        if result['posted']:
            prefix = '前回の送信失敗分を含め、' if result['retry'] else ''
            text = f'{prefix}精進チャンネルに投稿しました。新規初AC {result["new"]}問を保存、投稿省略 {result["omitted"]}問、保留 {result["held"]}問。'
        else:
            text = f'投稿可能な新規初ACはありません。新規保存 {result["new"]}問、開催中・開催情報不明の保留 {result["held"]}問。'
        await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name='stats', description='自分の初AC数・日別累計・生涯Difficulty分布を表示します')
    @app_commands.describe(period='weekly=7日、monthly=30日、all=全期間（初期値all）', aggregation='all時の棒グラフ集計: daily/weekly/monthly（初期値daily）')
    async def stats(self, interaction: discord.Interaction,
                    period: Literal['weekly', 'monthly', 'all'] = 'all',
                    aggregation: Literal['daily', 'weekly', 'monthly'] = 'daily'):
        await self._show_stats(interaction, period, aggregation)

    @app_commands.command(name='stats_id', description='指定IDの初AC数・日別累計・生涯Difficulty分布を表示します')
    @app_commands.describe(atcoder_id='統計を見るAtCoder ID（必須）', period='weekly=7日、monthly=30日、all=全期間（初期値all）', aggregation='all時の棒グラフ集計: daily/weekly/monthly（初期値daily）')
    async def stats_id(self, interaction: discord.Interaction, atcoder_id: str,
                       period: Literal['weekly', 'monthly', 'all'] = 'all',
                       aggregation: Literal['daily', 'weekly', 'monthly'] = 'daily'):
        await self._show_stats(interaction, period, aggregation, atcoder_id)

    async def _show_stats(self, interaction, period, aggregation, atcoder_id=None):
        await interaction.response.defer(thinking=True)
        user, rows, _synced, models = await self.service.stats_data(interaction.guild_id, interaction.user.id, atcoder_id)
        data = aggregate(rows, models, period, aggregation)
        values = histogram_values(rows, models)
        a, b, histogram = await asyncio.to_thread(render_stats, data, values, user)
        await interaction.followup.send(f'AC {data["total"]}',
                                        files=[discord.File(a, 'effort.png'), discord.File(b, 'total_effort.png'),
                                               discord.File(histogram, 'diff_hist.png')],
                                        allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name='help', description='登録・更新・統計の使い方を日本語で表示します')
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=help_embed(), allowed_mentions=discord.AllowedMentions.none())


class AtCoderBot(commands.Bot):
    def __init__(self, database_path, target_channel_id, cache_seconds=3600):
        super().__init__(command_prefix='/', intents=discord.Intents.default(),
                         help_command=None,
                         allowed_mentions=discord.AllowedMentions.none())
        self.database_path, self.target_channel_id = database_path, target_channel_id
        self.cache_seconds = cache_seconds
        self.session = self.db = None
        self.process_lock = None
        self.tree.add_command(ACCommands(self))

    async def setup_hook(self):
        # A second bot process would violate global rate limiting and outbox ownership.
        lock_path = Path(self.database_path).resolve()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.process_lock = open(str(lock_path) + '.lock', 'a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            self.process_lock = None
            raise RuntimeError('同じDATABASE_PATHのBotが既に起動しています。') from None
        self.db = Database(self.database_path)
        self.session = aiohttp.ClientSession()
        self.service = ACService(self.db, AtCoderAPI(self.session), cache_seconds=self.cache_seconds)
        await self.tree.sync()

    async def on_ready(self):
        log.info('Bot ready')

    async def close(self):
        try:
            await super().close()
        finally:
            if self.session:
                await self.session.close()
            if self.db:
                self.db.close()
                self.db = None
            if self.process_lock:
                self.process_lock.close()
                self.process_lock = None


def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise SystemExit('DISCORD_TOKEN が未設定です。')
    try:
        target = int(os.getenv('TARGET_CHANNEL_ID', '0')) or None
        cache = int(os.getenv('STATS_CACHE_SECONDS', '3600'))
        if (target is not None and target <= 0) or cache <= 0:
            raise ValueError
    except ValueError:
        raise SystemExit('TARGET_CHANNEL_ID と STATS_CACHE_SECONDS は正の整数で指定してください。')
    bot = AtCoderBot(os.getenv('DATABASE_PATH', 'data/ac.sqlite3'), target, cache)
    bot.run(token, log_handler=None)


if __name__ == '__main__':
    main()
