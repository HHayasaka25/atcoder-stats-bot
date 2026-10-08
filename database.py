"""Additive SQLite schema; first AC data and per-registration delivery are separate."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid


class RegistrationError(ValueError):
    pass


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {"ac_accounts", "ac_firsts", "ac_registrations", "ac_deliveries", "ac_batches", "ac_resources"}
        if version not in (0, 1, 2, 3) or (version == 0 and tables & expected):
            self.conn.close()
            raise RuntimeError("対応していない既存DBです。別のDATABASE_PATHを指定してください。")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS ac_accounts (
            atcoder_id TEXT PRIMARY KEY, cursor INTEGER NOT NULL,
            synced_at INTEGER NOT NULL, initial_complete INTEGER NOT NULL CHECK(initial_complete=1)
        );
        CREATE TABLE IF NOT EXISTS ac_firsts (
            atcoder_id TEXT NOT NULL REFERENCES ac_accounts(atcoder_id),
            problem_id TEXT NOT NULL, contest_id TEXT NOT NULL,
            submission_id INTEGER NOT NULL, epoch_second INTEGER NOT NULL,
            PRIMARY KEY(atcoder_id,problem_id)
        );
        CREATE TABLE IF NOT EXISTS ac_registrations (
            guild_id INTEGER NOT NULL, discord_id INTEGER NOT NULL,
            atcoder_id TEXT NOT NULL REFERENCES ac_accounts(atcoder_id),
            registration_id TEXT NOT NULL UNIQUE, registered_at INTEGER NOT NULL,
            PRIMARY KEY(guild_id,discord_id), UNIQUE(guild_id,atcoder_id)
        );
        CREATE TABLE IF NOT EXISTS ac_batches (
            batch_id TEXT PRIMARY KEY, registration_id TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','sent')),
            message_id INTEGER, created_at INTEGER NOT NULL, sent_at INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ac_one_pending_batch
            ON ac_batches(registration_id) WHERE state='pending';
        CREATE TABLE IF NOT EXISTS ac_deliveries (
            registration_id TEXT NOT NULL, problem_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('baseline','pending','held','excluded','queued','posted')),
            batch_id TEXT REFERENCES ac_batches(batch_id),
            PRIMARY KEY(registration_id,problem_id)
        );
        CREATE TABLE IF NOT EXISTS ac_resources (
            name TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER NOT NULL
        );
        """)
        if version < 2:
            # Preserve old payloads and delivery rows while allowing a rebuilt batch.
            with self.transaction():
                self.conn.execute("ALTER TABLE ac_batches ADD COLUMN superseded_at INTEGER")
                self.conn.execute("DROP INDEX ac_one_pending_batch")
                self.conn.execute("""CREATE UNIQUE INDEX ac_one_pending_batch
                    ON ac_batches(registration_id) WHERE state='pending' AND superseded_at IS NULL""")
                self.conn.execute("PRAGMA user_version=2")
        if version < 3:
            with self.transaction():
                self.conn.execute("ALTER TABLE ac_registrations ADD COLUMN display_id TEXT")
                self.conn.execute("UPDATE ac_registrations SET display_id=atcoder_id")
                self.conn.execute("PRAGMA user_version=3")

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()

    def registration(self, guild, discord_id):
        return self.conn.execute("SELECT * FROM ac_registrations WHERE guild_id=? AND discord_id=?",
                                 (guild, discord_id)).fetchone()

    def is_registered(self, user):
        return bool(self.conn.execute("SELECT 1 FROM ac_registrations WHERE atcoder_id=?", (user,)).fetchone())

    def check_registration(self, guild, discord_id, user, expected):
        current = self.registration(guild, discord_id)
        if (current["atcoder_id"] if current else None) != expected:
            raise RegistrationError("登録状態が変更されました。コマンドを再実行してください。")
        duplicate = self.conn.execute(
            "SELECT 1 FROM ac_registrations WHERE guild_id=? AND atcoder_id=? AND discord_id<>?",
            (guild, user, discord_id)).fetchone()
        if duplicate:
            raise RegistrationError("このAtCoder IDは同じサーバーの別ユーザーに登録済みです。")

    def account(self, user):
        return self.conn.execute("SELECT * FROM ac_accounts WHERE atcoder_id=?", (user,)).fetchone()

    def rows(self, user):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM ac_firsts WHERE atcoder_id=? ORDER BY epoch_second,submission_id", (user,))]

    def _merge(self, user, submissions, cursor, now):
        self.conn.execute("""INSERT INTO ac_accounts VALUES(?,?,?,1)
            ON CONFLICT(atcoder_id) DO UPDATE SET cursor=MAX(cursor,excluded.cursor),synced_at=excluded.synced_at""",
            (user, cursor, now))
        new = []
        for row in sorted(submissions, key=lambda r: (r["epoch_second"], r["id"])):
            if row["result"] != "AC" or row["contest_id"].lower().startswith("ahc"):
                continue
            existing = self.conn.execute("SELECT epoch_second,submission_id FROM ac_firsts WHERE atcoder_id=? AND problem_id=?",
                                         (user, row["problem_id"])).fetchone()
            if not existing:
                new.append(row["problem_id"])
            if not existing or (row["epoch_second"], row["id"]) < tuple(existing):
                self.conn.execute("""INSERT INTO ac_firsts VALUES(?,?,?,?,?)
                    ON CONFLICT(atcoder_id,problem_id) DO UPDATE SET
                    contest_id=excluded.contest_id,submission_id=excluded.submission_id,epoch_second=excluded.epoch_second""",
                    (user, row["problem_id"], row["contest_id"], row["id"], row["epoch_second"]))
        # Every active registration sees new ACs, even if another guild initiated the fetch.
        for reg in self.conn.execute("SELECT registration_id FROM ac_registrations WHERE atcoder_id=?", (user,)):
            for pid in new:
                self.conn.execute("INSERT OR IGNORE INTO ac_deliveries VALUES(?,?,'pending',NULL)", (reg[0], pid))
        return new

    def set_display_id(self, guild, discord_id, user, display_id):
        with self.transaction():
            self.check_registration(guild, discord_id, user, user)
            self.conn.execute("UPDATE ac_registrations SET display_id=? WHERE guild_id=? AND discord_id=?",
                              (display_id, guild, discord_id))

    def register(self, guild, discord_id, user, submissions, cursor, now, expected=None, display_id=None):
        with self.transaction():
            self.check_registration(guild, discord_id, user, expected)
            self._merge(user, submissions, cursor, now)
            generation = uuid.uuid4().hex
            self.conn.execute("""INSERT INTO ac_registrations
                (guild_id,discord_id,atcoder_id,registration_id,registered_at,display_id) VALUES(?,?,?,?,?,?)
                ON CONFLICT(guild_id,discord_id) DO UPDATE SET
                atcoder_id=excluded.atcoder_id,registration_id=excluded.registration_id,
                registered_at=excluded.registered_at,display_id=excluded.display_id""",
                (guild, discord_id, user, generation, now, display_id or user))
            self.conn.execute("""INSERT INTO ac_deliveries
                SELECT ?,problem_id,'baseline',NULL FROM ac_firsts WHERE atcoder_id=?""", (generation, user))

    def merge(self, user, submissions, cursor, now):
        with self.transaction():
            return self._merge(user, submissions, cursor, now)

    def resource(self, name):
        r = self.conn.execute("SELECT * FROM ac_resources WHERE name=?", (name,)).fetchone()
        return (json.loads(r["payload"]), r["fetched_at"]) if r else (None, 0)

    def save_resource(self, name, data, now):
        with self.transaction():
            self.conn.execute("INSERT OR REPLACE INTO ac_resources VALUES(?,?,?)", (name, json.dumps(data), now))

    def pending_batch(self, generation):
        row = self.conn.execute("""SELECT * FROM ac_batches WHERE registration_id=?
            AND state='pending' AND superseded_at IS NULL""", (generation,)).fetchone()
        return dict(row) if row else None

    def candidates(self, reg):
        return [dict(r) for r in self.conn.execute("""SELECT f.* FROM ac_firsts f JOIN ac_deliveries d
            ON f.problem_id=d.problem_id WHERE f.atcoder_id=? AND d.registration_id=? AND d.state IN ('pending','held','queued')
            ORDER BY f.epoch_second,f.submission_id""", (reg["atcoder_id"], reg["registration_id"]))]

    def prepare(self, reg, eligible, held, selected, payload, now):
        generation = reg["registration_id"]
        with self.transaction():
            self.conn.execute("""UPDATE ac_batches SET superseded_at=? WHERE registration_id=?
                AND state='pending' AND superseded_at IS NULL""", (now, generation))
            self.conn.execute("""UPDATE ac_deliveries SET state='pending',batch_id=NULL
                WHERE registration_id=? AND state='queued'""", (generation,))
            batch_id = uuid.uuid4().hex if selected else None
            if selected:
                self.conn.execute("""INSERT INTO ac_batches
                    (batch_id,registration_id,payload,state,message_id,created_at,sent_at)
                    VALUES(?,?,?,'pending',NULL,?,NULL)""",
                                  (batch_id, generation, json.dumps(payload, ensure_ascii=False), now))
            for row in held:
                self.conn.execute("UPDATE ac_deliveries SET state='held' WHERE registration_id=? AND problem_id=?",
                                  (generation, row["problem_id"]))
            ids = {r["problem_id"] for r in selected}
            for row in eligible:
                state = "queued" if row["problem_id"] in ids else "excluded"
                self.conn.execute("UPDATE ac_deliveries SET state=?,batch_id=? WHERE registration_id=? AND problem_id=?",
                                  (state, batch_id if state == "queued" else None, generation, row["problem_id"]))
        return self.pending_batch(generation)

    def mark_sent(self, batch_id, message_id, now):
        with self.transaction():
            updated = self.conn.execute("""UPDATE ac_batches SET state='sent',message_id=?,sent_at=?
                WHERE batch_id=? AND state='pending' AND superseded_at IS NULL""", (message_id, now, batch_id))
            if updated.rowcount != 1:
                return False
            self.conn.execute("UPDATE ac_deliveries SET state='posted' WHERE batch_id=? AND state='queued'", (batch_id,))
            return True
