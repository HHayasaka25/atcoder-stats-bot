import asyncio
from unittest.mock import AsyncMock
import aiohttp
import pytest
from atcoder_api import APIError, AtCoderAPI, normalize_id
from conftest import submission


class Clock:
    def __init__(self):
        self.value = 0
        self.sleeps = []

    def now(self):
        return self.value

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.value += delay
        await asyncio.sleep(0)


class Response:
    def __init__(self, body=None, status=200, headers=None):
        self.body, self.status, self.headers = body, status, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self.body


class Session:
    def __init__(self, clock, responses):
        self.clock, self.responses, self.starts = clock, iter(responses), []

    def get(self, *args, **kwargs):
        self.starts.append(self.clock.now())
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


async def test_pagination_repeats_boundary_second_and_deduplicates():
    api = AtCoderAPI(None)
    first = [submission(sid=i+1, second=i//2) for i in range(500)]
    second = [submission(sid=499, second=249), submission(sid=500, second=249), submission(sid=501, second=249), submission(sid=502, second=250)]
    api.get_json = AsyncMock(side_effect=[first, second])
    rows = await api.submissions('alice')
    assert len(rows) == 502
    assert api.get_json.call_args.args[1]['from_second'] == 249


async def test_saturated_second_raises_instead_of_dropping():
    api = AtCoderAPI(None)
    batch = [submission(sid=i+1, second=42) for i in range(500)]
    api.get_json = AsyncMock(side_effect=[batch, batch])
    with pytest.raises(APIError, match='同一秒'):
        await api.submissions('alice')


async def test_later_page_failure_never_returns_partial():
    api = AtCoderAPI(None)
    api.get_json = AsyncMock(side_effect=[[submission(sid=i+1, second=i) for i in range(500)], APIError('failure')])
    with pytest.raises(APIError):
        await api.submissions('alice')


async def test_concurrent_requests_shared_interval():
    clock = Clock()
    session = Session(clock, [Response([]) for _ in range(8)])
    api = AtCoderAPI(session, clock=clock.now, sleep=clock.sleep)
    await asyncio.gather(*(api.get_json('mock') for _ in range(8)))
    assert len(session.starts) == 8
    assert all(b-a >= 1.6-1e-9 for a, b in zip(session.starts, session.starts[1:]))


async def test_429_500_and_timeout_retry_bounded_backoff():
    clock = Clock()
    session = Session(clock, [Response(status=429, headers={'Retry-After': '5'}),
                              Response(status=503), asyncio.TimeoutError(), Response([])])
    api = AtCoderAPI(session, clock=clock.now, sleep=clock.sleep)
    assert await api.get_json('mock') == []
    assert len(session.starts) == 4
    assert clock.sleeps == [5, 4, 8]


async def test_retry_limit_and_nonretryable_http():
    clock = Clock()
    session = Session(clock, [Response(status=500) for _ in range(4)])
    api = AtCoderAPI(session, clock=clock.now, sleep=clock.sleep)
    with pytest.raises(APIError):
        await api.get_json('mock')
    assert len(session.starts) == 4
    session = Session(clock, [Response(status=404)])
    api = AtCoderAPI(session, clock=clock.now, sleep=clock.sleep)
    with pytest.raises(APIError, match='404'):
        await api.get_json('mock')
    assert len(session.starts) == 1


@pytest.mark.parametrize('body', [None, {}, [{'id': 1}], [submission(user='bob')], [submission(second=-1)]])
async def test_invalid_response_fails(body):
    api = AtCoderAPI(None)
    api.get_json = AsyncMock(return_value=body)
    with pytest.raises(APIError):
        await api.submissions('alice')


@pytest.mark.parametrize('body', [[submission(sid=1, second=2), submission(sid=2, second=1)],
                                [submission(sid=1, second=1), submission(sid=1, second=1)]])
async def test_invalid_order_and_duplicate_page_ids_fail(body):
    api = AtCoderAPI(None)
    api.get_json = AsyncMock(return_value=body)
    with pytest.raises(APIError, match='並び順'):
        await api.submissions('alice')


@pytest.mark.parametrize('value', ['@everyone', 'alice&user=bob', 'x'*33, '', '日本語'])
def test_input_validation(value):
    with pytest.raises(ValueError):
        normalize_id(value)
