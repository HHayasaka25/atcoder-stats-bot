"""Transactions and command-scoped synchronization; no background tasks."""
import asyncio
import json
import logging
import time
from atcoder_api import APIError, normalize_id
from database import RegistrationError
from formatting import make_update

log = logging.getLogger(__name__)


class ACService:
    def __init__(self, db, api, *, cache_seconds=3600, now=time.time):
        self.db, self.api = db, api
        self.cache_seconds, self.now = cache_seconds, now
        self.account_locks, self.member_locks = {}, {}
        self.resource_lock = asyncio.Lock()

    def member_lock(self, guild, member):
        return self.member_locks.setdefault((guild, member), asyncio.Lock())

    def account_lock(self, user):
        return self.account_locks.setdefault(user, asyncio.Lock())

    async def resources(self, contests=False):
        async with self.resource_lock:
            for name, ttl in [('problem-models', 86400), ('problems', 86400)] + ([('contests', 300)] if contests else []):
                data, fetched = self.db.resource(name)
                if data is not None and self.now() - fetched < ttl:
                    continue
                try:
                    data = await self.api.resource(name)
                    if name == 'problem-models':
                        if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
                            raise APIError('Difficultyデータの形式が不正です。')
                    elif not isinstance(data, list) or any(not isinstance(v, dict) or not isinstance(v.get('id'), str) for v in data):
                        raise APIError('問題・コンテストデータの形式が不正です。')
                    self.db.save_resource(name, data, int(self.now()))
                except APIError:
                    # History remains authoritative; unknown metadata is not fabricated.
                    log.warning('Resource refresh failed: %s', name)

    def metadata(self):
        models, _ = self.db.resource('problem-models')
        problems, _ = self.db.resource('problems')
        return models or {}, {r['id']: r for r in (problems or [])}

    async def register(self, guild, member, user, expected=None):
        user = normalize_id(user)
        async with self.member_lock(guild, member):
            self.db.check_registration(guild, member, user, expected)
            if expected == user:
                return
            async with self.account_lock(user):
                started = int(self.now())
                rows = await self.api.submissions(user, 0)
                self.db.register(guild, member, user, rows, started, int(self.now()), expected)
            await self.resources()

    async def stats_data(self, guild, member, user=None):
        if user is None:
            reg = self.db.registration(guild, member)
            if not reg:
                raise RegistrationError('先に /ac register でAtCoder IDを登録してください。')
            user = reg['atcoder_id']
        user = normalize_id(user)
        async with self.account_lock(user):
            account = self.db.account(user)
            if not self.db.is_registered(user) and (account is None or self.now() - account['synced_at'] >= self.cache_seconds):
                started = int(self.now())
                rows = await self.api.submissions(user, 0)
                self.db.merge(user, rows, started, int(self.now()))
                await self.resources()
            account = self.db.account(user)
            return user, self.db.rows(user), account['synced_at'], self.metadata()[0]

    def contest_finished(self, cid, now):
        contests, fetched = self.db.resource('contests')
        if not contests:
            return False
        info = next((r for r in contests if r['id'] == cid), None)
        if not info:
            return False
        start, duration = info.get('start_epoch_second'), info.get('duration_second')
        if not isinstance(start, (int, float)) or not isinstance(duration, (int, float)) or start <= 0 or duration <= 0:
            return False
        end = start + duration
        # A previously confirmed ended contest remains safe if resource refresh fails.
        return end <= now and (now - fetched < 300 or end <= fetched)

    async def update(self, guild, member, send):
        async with self.member_lock(guild, member):
            reg = self.db.registration(guild, member)
            if not reg:
                raise RegistrationError('先に /ac register でAtCoder IDを登録してください。')
            retry = self.db.pending_batch(reg['registration_id']) is not None
            user = reg['atcoder_id']
            async with self.account_lock(user):
                account = self.db.account(user)
                started = int(self.now())
                rows = await self.api.submissions(user, max(0, account['cursor'] - 48 * 3600))
                new = self.db.merge(user, rows, started, int(self.now()))
            await self.resources(contests=True)
            now = int(self.now())
            eligible, held = [], []
            for row in self.db.candidates(reg):
                (eligible if self.contest_finished(row['contest_id'], now) else held).append(row)
            models, problems = self.metadata()
            payload, selected = make_update(user, eligible, models, problems)
            batch = self.db.prepare(reg, eligible, held, selected, payload, now)
            if batch:
                await self._send(batch, send)
            return {'posted': bool(batch), 'retry': retry, 'new': len(new),
                    'held': len(held), 'omitted': len(eligible) - len(selected)}

    async def _send(self, batch, send):
        message_id = await send(json.loads(batch['payload']))
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise RuntimeError('Discordの送信結果を確認できません。投稿候補を保持しています。')
        if not self.db.mark_sent(batch['batch_id'], message_id, int(self.now())):
            raise RuntimeError('送信バッチが変更されています。投稿状態を確認してください。')
