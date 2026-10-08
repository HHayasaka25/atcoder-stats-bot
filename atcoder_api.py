"""Command-driven API client. One limiter covers resources, pages and retries."""
import asyncio
import re
import time

import aiohttp

BASE = "https://kenkoooo.com/atcoder"


class APIError(RuntimeError):
    pass


def normalize_id(value):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", value):
        raise ValueError("AtCoder IDは英数字・アンダースコアの1〜32文字で指定してください。")
    return value.lower()


class AtCoderAPI:
    def __init__(self, session, *, interval=1.6, retries=3,
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.session = session
        self.interval = max(1.6, interval)
        self.retries = retries
        self.clock, self.sleep = clock, sleep
        self.lock = asyncio.Lock()
        self.last_finished = None

    async def get_json(self, url, params=None):
        for attempt in range(self.retries + 1):
            retry_after = 0
            try:
                async with self.lock:
                    if self.last_finished is not None:
                        delay = self.interval - (self.clock() - self.last_finished)
                        if delay > 0:
                            await self.sleep(delay)
                    try:
                        async with self.session.get(
                            url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                        ) as response:
                            if response.status == 429 or response.status >= 500:
                                try:
                                    retry_after = min(120, max(0, float(response.headers.get("Retry-After", 0))))
                                except ValueError:
                                    pass
                                raise APIError(f"APIが一時的に利用できません (HTTP {response.status})。")
                            if response.status != 200:
                                raise ValueError(f"APIがエラーを返しました (HTTP {response.status})。")
                            return await response.json()
                    finally:
                        self.last_finished = self.clock()
            except ValueError as exc:
                raise APIError(str(exc)) from exc
            except (APIError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == self.retries:
                    raise APIError("API取得に失敗しました。時間をおいて再実行してください。") from exc
                await self.sleep(max(2 ** (attempt + 1), retry_after))

    async def submissions(self, user, from_second=0):
        """Inclusive second overlap; a saturated single second cannot be paginated."""
        user = normalize_id(user)
        cursor = from_second
        collected = {}
        while True:
            batch = await self.get_json(
                BASE + "/atcoder-api/v3/user/submissions",
                {"user": user, "from_second": cursor},
            )
            if not isinstance(batch, list) or len(batch) > 500:
                raise APIError("提出APIの応答形式が不正です。")
            for row in batch:
                if (not isinstance(row, dict)
                    or not isinstance(row.get("id"), int)
                    or not isinstance(row.get("epoch_second"), int)
                    or row["epoch_second"] < cursor
                    or not isinstance(row.get("result"), str)
                    or not isinstance(row.get("user_id"), str)
                    or row["user_id"].lower() != user
                    or not all(isinstance(row.get(k), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row[k])
                               for k in ("problem_id", "contest_id"))):
                    raise APIError("提出APIのレコードが不正です。同期を中止しました。")
                collected[row["id"]] = row
            seconds = [row["epoch_second"] for row in batch]
            if seconds != sorted(seconds) or len({row["id"] for row in batch}) != len(batch):
                raise APIError("提出APIの並び順・提出IDが不正です。安全な同期を中止しました。")
            if len(batch) < 500:
                return sorted(collected.values(), key=lambda r: (r["epoch_second"], r["id"]))
            boundary = max(row["epoch_second"] for row in batch)
            if boundary <= cursor:
                raise APIError("同一秒に500件以上の提出があり、安全にページ送りできません。同期を中止しました。")
            # Repeat the last second rather than incrementing and losing tied rows.
            cursor = boundary

    async def resource(self, name):
        return await self.get_json(BASE + f"/resources/{name}.json")
