import sqlite3
import pytest
from database import Database
from scripts.backup_db import backup
from conftest import NOW, submission


def test_preserves_unrelated_existing_tables(tmp_path):
    path = tmp_path / 'existing.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE original_data (value TEXT)')
        conn.execute("INSERT INTO original_data VALUES ('preserve')")
    db = Database(path)
    assert db.conn.execute('SELECT value FROM original_data').fetchone()[0] == 'preserve'
    db.close()


def test_rejects_unknown_schema_and_keeps_data(tmp_path):
    path = tmp_path / 'existing.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('PRAGMA user_version=99')
        conn.execute('CREATE TABLE existing(value TEXT)')
        conn.execute("INSERT INTO existing VALUES ('safe')")
    with pytest.raises(RuntimeError):
        Database(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 99
        assert conn.execute('SELECT value FROM existing').fetchone()[0] == 'safe'


def test_live_wal_backup_and_no_overwrite(db, tmp_path):
    db.register(10, 123, 'alice', [submission()], NOW, NOW)
    destination = tmp_path / 'backup.sqlite3'
    backup(tmp_path / 'ac.sqlite3', destination)
    with sqlite3.connect(destination) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('SELECT COUNT(*) FROM ac_firsts').fetchone()[0] == 1
        assert conn.execute('SELECT atcoder_id FROM ac_registrations').fetchone()[0] == 'alice'
    with pytest.raises(FileExistsError):
        backup(tmp_path / 'ac.sqlite3', destination)
    with pytest.raises(FileNotFoundError):
        backup(tmp_path / 'missing.sqlite3', tmp_path / 'other.sqlite3')
    assert not (tmp_path / 'missing.sqlite3').exists()


def test_v1_migration_preserves_pending_batch_and_all_history(db, tmp_path):
    db.register(10, 123, 'alice', [], NOW, NOW)
    db.merge('alice', [submission()], NOW, NOW)
    reg = db.registration(10, 123)
    rows = db.candidates(reg)
    db.prepare(reg, rows, [], rows, {'title': 'old', 'description': 'retained'}, NOW)
    # Reconstruct the previous schema using only this test's temporary database.
    with db.transaction():
        db.conn.execute('DROP INDEX ac_one_pending_batch')
        db.conn.execute('ALTER TABLE ac_batches DROP COLUMN superseded_at')
        db.conn.execute('ALTER TABLE ac_registrations DROP COLUMN display_id')
        db.conn.execute("CREATE UNIQUE INDEX ac_one_pending_batch ON ac_batches(registration_id) WHERE state='pending'")
        db.conn.execute('PRAGMA user_version=1')
    migrated = Database(tmp_path / 'ac.sqlite3')
    assert migrated.conn.execute('PRAGMA user_version').fetchone()[0] == 3
    assert migrated.registration(10, 123)['display_id'] == 'alice'
    assert len(migrated.rows('alice')) == 1
    assert migrated.pending_batch(reg['registration_id'])['payload'] == '{"title": "old", "description": "retained"}'
    assert len(migrated.candidates(reg)) == 1
    assert migrated.conn.execute('SELECT state FROM ac_deliveries').fetchone()[0] == 'queued'
    migrated.close()


def test_v2_migration_preserves_history_and_pending_delivery(db, tmp_path):
    db.register(10, 123, 'alice', [], NOW, NOW)
    db.merge('alice', [submission()], NOW, NOW)
    reg = db.registration(10, 123)
    rows = db.candidates(reg)
    before = db.prepare(reg, rows, [], rows, {'title': 'old', 'description': 'retained'}, NOW)
    with db.transaction():
        db.conn.execute('ALTER TABLE ac_registrations DROP COLUMN display_id')
        db.conn.execute('PRAGMA user_version=2')
    migrated = Database(tmp_path / 'ac.sqlite3')
    assert migrated.conn.execute('PRAGMA user_version').fetchone()[0] == 3
    assert migrated.registration(10, 123)['display_id'] == 'alice'
    assert migrated.pending_batch(reg['registration_id']) == before
    assert len(migrated.rows('alice')) == len(migrated.candidates(reg)) == 1
    migrated.close()
