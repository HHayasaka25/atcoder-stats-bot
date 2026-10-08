import asyncio
from unittest.mock import AsyncMock
import pytest
from atcoder_api import APIError
from database import Database, RegistrationError
from service import ACService
from conftest import NOW, submission


async def registered(service, api, rows=None):
    api.submissions.return_value = rows or []
    await service.register(10, 123, 'Alice')


async def test_registration_full_history_baseline_and_discord_ids(service, api, db):
    rows = [submission(), submission(sid=2, second=NOW-10),
            submission('abc100_b', sid=3, result='WA'),
            submission('ahc001_a', sid=4, contest='ahc001')]
    await registered(service, api, rows)
    api.submissions.assert_awaited_once_with('alice', 0)
    assert len(db.rows('alice')) == 1
    assert db.rows('alice')[0]['epoch_second'] == NOW-100
    reg = db.registration(10, 123)
    assert reg['atcoder_id'] == 'alice'
    assert db.registration(10, 124) is None
    assert db.candidates(reg) == []
    assert db.account('alice')['initial_complete'] == 1
    send = AsyncMock()
    result = await service.update(10, 123, send)
    assert result['new'] == 0
    send.assert_not_awaited()


async def test_incremental_reac_earlier_first_and_idempotency(service, api, db):
    await registered(service, api, [submission()])
    api.submissions.return_value = [submission(sid=2), submission('abc100_b', sid=3),
                                    submission(sid=4, second=NOW-200)]
    send = AsyncMock(return_value=99)
    result = await service.update(10, 123, send)
    assert result['new'] == 1
    assert len(db.rows('alice')) == 2
    assert db.rows('alice')[0]['epoch_second'] == NOW-200
    api.submissions.assert_awaited_with('alice', NOW-48*3600)
    assert send.await_count == 1
    result = await service.update(10, 123, send)
    assert result['new'] == 0
    assert len(db.rows('alice')) == 2
    assert send.await_count == 1


async def test_more_than_50_all_saved_and_no_carryover(service, api, db):
    await registered(service, api)
    api.submissions.return_value = [submission(f'abc100_p{i}', sid=i+1, second=NOW-1000+i) for i in range(80)]
    send = AsyncMock(return_value=12345)
    result = await service.update(10, 123, send)
    assert len(db.rows('alice')) == 80
    assert result['omitted'] >= 30
    body = send.call_args.args[0]['description']
    assert body.count('https://atcoder.jp/') <= 50
    assert 'ABC100 P0]' not in body
    assert 'ABC100 P79]' in body
    assert send.await_count == 1
    await service.update(10, 123, send)
    assert send.await_count == 1
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='excluded'").fetchone()[0] == result['omitted']


async def test_api_failure_does_not_advance_cursor_or_save_partial(service, api, db):
    await registered(service, api, [submission()])
    before = dict(db.account('alice'))
    service.now = lambda: NOW+1000
    api.submissions.side_effect = APIError('failed after a page')
    with pytest.raises(APIError):
        await service.update(10, 123, AsyncMock())
    assert dict(db.account('alice')) == before
    assert len(db.rows('alice')) == 1


async def test_failed_initial_registration_is_atomic(service, api, db):
    api.submissions.side_effect = APIError('incomplete')
    with pytest.raises(APIError):
        await service.register(10, 123, 'alice')
    assert db.registration(10, 123) is None
    assert db.account('alice') is None
    assert db.rows('alice') == []


async def test_transaction_rollback_on_storage_failure(service, api, db):
    db.conn.execute("CREATE TRIGGER reject_registration BEFORE INSERT ON ac_registrations BEGIN SELECT RAISE(ABORT,'test'); END")
    api.submissions.return_value = [submission()]
    with pytest.raises(Exception, match='test'):
        await service.register(10, 123, 'alice')
    assert db.account('alice') is None
    assert db.rows('alice') == []


async def test_duplicate_and_change_confirmation_required(service, api, db):
    await registered(service, api, [submission()])
    with pytest.raises(RegistrationError, match='別ユーザー'):
        await service.register(10, 456, 'ALICE')
    with pytest.raises(RegistrationError, match='登録状態'):
        await service.register(10, 123, 'bob')
    api.submissions.return_value = [submission('abc100_b', user='bob')]
    await service.register(10, 123, 'bob', expected='alice')
    assert db.registration(10, 123)['atcoder_id'] == 'bob'
    assert len(db.rows('alice')) == 1  # old records are retained
    assert db.candidates(db.registration(10, 123)) == []


async def test_duplicate_registration_race_is_atomic(service, api, db):
    api.submissions.return_value = [submission()]
    results = await asyncio.gather(service.register(10, 1, 'alice'), service.register(10, 2, 'alice'), return_exceptions=True)
    assert sum(isinstance(r, RegistrationError) for r in results) == 1
    assert db.conn.execute('SELECT COUNT(*) FROM ac_registrations').fetchone()[0] == 1


async def test_send_failure_rebuilds_durable_batch_after_restart(service, api, db, tmp_path):
    await registered(service, api)
    api.submissions.return_value = [submission()]
    send = AsyncMock(side_effect=RuntimeError('Discord failed'))
    with pytest.raises(RuntimeError):
        await service.update(10, 123, send)
    assert len(db.rows('alice')) == 1
    payload = send.call_args.args[0]
    calls = api.submissions.await_count
    restarted_db = Database(tmp_path / 'ac.sqlite3')
    restarted = ACService(restarted_db, api, now=lambda: NOW)
    success = AsyncMock(return_value=999)
    result = await restarted.update(10, 123, success)
    assert result['retry']
    assert success.call_args.args[0] == payload
    assert api.submissions.await_count == calls + 1
    assert restarted_db.pending_batch(restarted_db.registration(10, 123)['registration_id']) is None
    assert restarted_db.conn.execute("SELECT message_id FROM ac_batches WHERE state='sent'").fetchone()[0] == 999
    assert restarted_db.conn.execute('SELECT COUNT(*) FROM ac_batches WHERE superseded_at IS NOT NULL').fetchone()[0] == 1
    restarted_db.close()


async def test_retry_merges_failed_and_new_acs_in_one_message(service, api, db):
    await registered(service, api, [submission('abc100_baseline', sid=1, second=NOW-1000)])
    api.submissions.return_value = [submission('abc100_old', sid=2, second=NOW-500)]
    failed = AsyncMock(side_effect=RuntimeError('send failed'))
    with pytest.raises(RuntimeError):
        await service.update(10, 123, failed)
    reg = db.registration(10, 123)
    old_batch = db.pending_batch(reg['registration_id'])
    api.submissions.return_value = [submission('abc100_old', sid=2, second=NOW-500),
                                    submission('abc100_new', sid=3, second=NOW-100)]
    service.now = lambda: NOW+60
    success = AsyncMock(return_value=777)
    result = await service.update(10, 123, success)
    assert result == {'posted': True, 'retry': True, 'new': 1, 'held': 0, 'omitted': 0}
    assert api.submissions.await_count == 3  # Register and both updates fetch.
    success.assert_awaited_once()
    body = success.call_args.args[0]['description']
    assert body.index('ABC100 OLD]') < body.index('ABC100 NEW]')
    assert 'BASELINE' not in body
    assert len(db.rows('alice')) == 3
    assert db.account('alice')['cursor'] == NOW+60
    assert db.conn.execute('SELECT superseded_at FROM ac_batches WHERE batch_id=?',
                           (old_batch['batch_id'],)).fetchone()[0] == NOW+60
    assert not db.mark_sent(old_batch['batch_id'], 12345, NOW+61)  # Stale acknowledgement.
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='posted'").fetchone()[0] == 2
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='baseline'").fetchone()[0] == 1


async def test_retry_over_50_discards_old_failed_problems_permanently(service, api, db):
    await registered(service, api)
    rows = [submission(f'abc100_p{i}', sid=i+1, second=NOW-1000+i) for i in range(80)]
    api.submissions.return_value = rows[:20]
    failed = AsyncMock(side_effect=RuntimeError('send failed'))
    with pytest.raises(RuntimeError):
        await service.update(10, 123, failed)
    api.submissions.return_value = rows
    # Even a second failed send must permanently exclude the older 30.
    with pytest.raises(RuntimeError):
        await service.update(10, 123, failed)
    assert len(db.rows('alice')) == 80
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='excluded'").fetchone()[0] == 30
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='queued'").fetchone()[0] == 50
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='posted'").fetchone()[0] == 0
    assert failed.call_args.args[0]['description'].count('https://atcoder.jp/') == 50
    success = AsyncMock(return_value=888)
    result = await service.update(10, 123, success)
    assert result['new'] == result['omitted'] == 0
    body = success.call_args.args[0]['description']
    assert 'ABC100 P29]' not in body and 'ABC100 P30]' in body and 'ABC100 P79]' in body
    await service.update(10, 123, success)
    success.assert_awaited_once()
    assert len(db.rows('alice')) == 80
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='excluded'").fetchone()[0] == 30


async def test_api_failure_with_old_batch_does_not_send_or_change_it(service, api, db):
    await registered(service, api)
    api.submissions.return_value = [submission()]
    with pytest.raises(RuntimeError):
        await service.update(10, 123, AsyncMock(side_effect=RuntimeError('send failed')))
    reg = db.registration(10, 123)
    batch_before = db.pending_batch(reg['registration_id'])
    account_before = dict(db.account('alice'))
    deliveries_before = list(db.conn.execute('SELECT * FROM ac_deliveries'))
    service.now = lambda: NOW+60
    api.submissions.side_effect = APIError('failed')
    send = AsyncMock(return_value=123)
    with pytest.raises(APIError):
        await service.update(10, 123, send)
    send.assert_not_awaited()
    assert dict(db.account('alice')) == account_before
    assert db.pending_batch(reg['registration_id']) == batch_before
    assert list(db.conn.execute('SELECT * FROM ac_deliveries')) == deliveries_before


async def test_failed_candidates_recheck_contests_and_rebuild_after_release(service, api, db):
    await registered(service, api)
    api.submissions.return_value = [submission()]
    with pytest.raises(RuntimeError):
        await service.update(10, 123, AsyncMock(side_effect=RuntimeError('send failed')))
    db.save_resource('contests', [dict(id='abc100', start_epoch_second=NOW-100, duration_second=7200)], NOW)
    send = AsyncMock(return_value=123)
    result = await service.update(10, 123, send)
    assert result['held'] == 1 and not result['posted']
    send.assert_not_awaited()
    reg = db.registration(10, 123)
    assert db.pending_batch(reg['registration_id']) is None
    assert db.conn.execute('SELECT state,batch_id FROM ac_deliveries').fetchone()[:] == ('held', None)
    service.now = lambda: NOW+8000
    result = await service.update(10, 123, send)
    assert result['posted'] and result['held'] == 0
    send.assert_awaited_once()


async def test_unknown_send_result_keeps_candidates_and_successful_api_data(service, api, db):
    await registered(service, api)
    api.submissions.return_value = [submission()]
    service.now = lambda: NOW+60
    with pytest.raises(RuntimeError, match='送信結果'):
        await service.update(10, 123, AsyncMock(return_value=None))
    assert db.account('alice')['cursor'] == NOW+60
    assert len(db.rows('alice')) == 1
    assert db.conn.execute('SELECT state FROM ac_deliveries').fetchone()[0] == 'queued'
    api.submissions.return_value = [submission(), submission('abc100_b', sid=2, second=NOW+61)]
    service.now = lambda: NOW+120
    send = AsyncMock(return_value=456)
    await service.update(10, 123, send)
    assert send.call_args.args[0]['description'].count('https://atcoder.jp/') == 2
    assert db.conn.execute("SELECT COUNT(*) FROM ac_deliveries WHERE state='posted'").fetchone()[0] == 2


async def test_rebuild_transaction_failure_keeps_old_batch_and_new_history(service, api, db):
    await registered(service, api)
    api.submissions.return_value = [submission()]
    with pytest.raises(RuntimeError):
        await service.update(10, 123, AsyncMock(side_effect=RuntimeError('send failed')))
    reg = db.registration(10, 123)
    previous = db.pending_batch(reg['registration_id'])
    db.conn.execute("CREATE TRIGGER reject_batch BEFORE INSERT ON ac_batches BEGIN SELECT RAISE(ABORT,'rebuild failed'); END")
    service.now = lambda: NOW+60
    api.submissions.return_value = [submission(), submission('abc100_b', sid=2)]
    send = AsyncMock(return_value=456)
    with pytest.raises(Exception, match='rebuild failed'):
        await service.update(10, 123, send)
    send.assert_not_awaited()
    assert db.pending_batch(reg['registration_id']) == previous
    assert len(db.rows('alice')) == 2
    assert db.account('alice')['cursor'] == NOW+60
    assert {r['problem_id'] for r in db.candidates(reg)} == {'abc100_a', 'abc100_b'}


async def test_ongoing_unknown_contests_held_then_released(service, api, db):
    await registered(service, api)
    db.save_resource('contests', [dict(id='abc100', start_epoch_second=NOW-100, duration_second=7200)], NOW)
    api.submissions.return_value = [submission(), submission('missing_a', sid=2, contest='missing')]
    send = AsyncMock(return_value=88)
    result = await service.update(10, 123, send)
    assert result['held'] == 2
    assert len(db.rows('alice')) == 2
    send.assert_not_awaited()
    service.now = lambda: NOW+8000
    result = await service.update(10, 123, send)
    assert result['posted'] and result['held'] == 1
    assert send.await_count == 1
    assert 'missing' not in send.call_args.args[0]['description']


async def test_unreliable_contest_metadata_is_safe(service, api, db):
    await registered(service, api)
    service.now = lambda: NOW+500
    api.resource.side_effect = APIError('resource down')
    api.submissions.return_value = [submission()]
    send = AsyncMock()
    result = await service.update(10, 123, send)
    assert result['held'] == 1
    assert len(db.rows('alice')) == 1
    send.assert_not_awaited()


async def test_registered_statistics_never_fetch_api(service, api, db):
    await registered(service, api, [submission()])
    api.reset_mock()
    service.now = lambda: NOW+1000000
    for user in (None, 'ALICE'):
        name, rows, synced, models = await service.stats_data(10, 123, user)
        assert name == (user or 'Alice') and len(rows) == 1 and synced == NOW
    api.submissions.assert_not_awaited()
    api.resource.assert_not_awaited()


async def test_unregistered_stats_cache_without_binding(service, api, db):
    api.submissions.return_value = [submission()]
    await service.stats_data(10, 123, 'alice')
    await service.stats_data(10, 123, 'ALICE')
    assert api.submissions.await_count == 1
    assert db.registration(10, 123) is None
    service.now = lambda: NOW+3601
    await service.stats_data(10, 123, 'alice')
    assert api.submissions.await_count == 2


async def test_display_case_is_preserved_without_changing_account_identity(service, api, db):
    api.submissions.return_value = [submission()]
    await service.register(10, 123, 'ALIce')
    reg = dict(db.registration(10, 123))
    assert reg['atcoder_id'] == 'alice' and reg['display_id'] == 'ALIce'
    assert (await service.stats_data(10, 123))[0] == 'ALIce'
    assert (await service.stats_data(10, 123, 'aLiCe'))[0] == 'aLiCe'
    _, payload = await service.registration_preview('ALIce')
    assert payload['title'] == 'AC — ALIce'
    api.submissions.reset_mock()
    await service.register(10, 123, 'Alice', expected='alice')
    api.submissions.assert_not_awaited()
    current = dict(db.registration(10, 123))
    assert current == dict(reg, display_id='Alice')
    assert db.candidates(current) == []
    api.submissions.return_value = [submission('abc100_b', sid=2)]
    send = AsyncMock(return_value=12345)
    await service.update(10, 123, send)
    assert 'Alice' in send.call_args.args[0]['title']
    assert len(db.rows('alice')) == 2
    with pytest.raises(RegistrationError, match='別ユーザー'):
        await service.register(10, 456, 'ALICE')
    path = db.conn.execute('PRAGMA database_list').fetchone()[2]
    reopened = Database(path)
    assert reopened.registration(10, 123)['display_id'] == 'Alice'
    reopened.close()


async def test_same_member_concurrent_update_sends_once(service, api, db):
    await registered(service, api)
    api.submissions.return_value = [submission()]
    send = AsyncMock(return_value=1)
    await asyncio.gather(service.update(10, 123, send), service.update(10, 123, send))
    assert send.await_count == 1
    assert len(db.rows('alice')) == 1


async def test_other_guild_updates_are_delivered_independently(service, api, db):
    await registered(service, api)
    await service.register(20, 456, 'alice')
    api.submissions.return_value = [submission()]
    send_a, send_b = AsyncMock(return_value=1), AsyncMock(return_value=2)
    await service.update(10, 123, send_a)
    await service.update(20, 456, send_b)
    assert send_a.await_count == send_b.await_count == 1
    assert len(db.rows('alice')) == 1


async def test_missing_registration(service):
    with pytest.raises(RegistrationError):
        await service.update(10, 123, AsyncMock())
    with pytest.raises(RegistrationError):
        await service.stats_data(10, 123)
