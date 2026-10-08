from datetime import datetime
from unittest.mock import AsyncMock
import pytest
from database import Database
from formatting import JST
from service import ACService

NOW = int(datetime(2026, 10, 8, 12, tzinfo=JST).timestamp())


def submission(pid='abc100_a', second=NOW-100, sid=1, result='AC', contest='abc100', user='alice'):
    return dict(id=sid, epoch_second=second, problem_id=pid, contest_id=contest, result=result, user_id=user)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / 'ac.sqlite3')
    yield database
    database.close()


@pytest.fixture
def api():
    api = AsyncMock()
    api.submissions.return_value = []
    async def resource(name):
        if name == 'contests':
            return [dict(id='abc100', start_epoch_second=NOW-100000, duration_second=7200)]
        if name == 'problems':
            return [dict(id='abc100_a', problem_index='A')]
        return {'abc100_a': {'difficulty': 620}}
    api.resource.side_effect = resource
    return api


@pytest.fixture
def service(db, api):
    return ACService(db, api, now=lambda: NOW)
