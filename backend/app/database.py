from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from .config import DATABASE_PATH


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS rooms (
    session_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    owner_token_hash TEXT NOT NULL,
    owner_visitor_id TEXT NOT NULL,
    model TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'diverge',
    prep_checklist TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visitors (
    visitor_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    nickname TEXT NOT NULL,
    is_owner INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL,
    UNIQUE(session_id, nickname)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    visitor_id TEXT,
    sender_name TEXT NOT NULL,
    sender_type TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL,
    reply_to_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    image_path TEXT,
    is_recalled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session_created ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    mentioned_visitor_id TEXT NOT NULL REFERENCES visitors(visitor_id) ON DELETE CASCADE,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def database():
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with database() as connection:
        connection.executescript(SCHEMA)
