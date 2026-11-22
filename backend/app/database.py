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
    password_hash TEXT NOT NULL DEFAULT '',
    template_id TEXT NOT NULL DEFAULT 'general',
    model TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'diverge',
    prep_checklist TEXT NOT NULL DEFAULT '[]',
    closed_at TEXT,
    red_team_enabled INTEGER NOT NULL DEFAULT 1,
    red_team_count INTEGER NOT NULL DEFAULT 0,
    last_activity_at TEXT,
    cleanup_warning_sent_at TEXT,
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
CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    through_message_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, version)
);
CREATE INDEX IF NOT EXISTS summaries_session_version ON summaries(session_id, version DESC);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    strengths TEXT NOT NULL DEFAULT '',
    tradeoffs TEXT NOT NULL DEFAULT '',
    proposer_name TEXT NOT NULL DEFAULT '',
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'considering',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ideas_session_updated ON ideas(session_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    dimensions TEXT NOT NULL DEFAULT '{}',
    rationale TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluations_idea_created ON evaluations(idea_id, created_at DESC);
CREATE TABLE IF NOT EXISTS idea_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    visitor_id TEXT REFERENCES visitors(visitor_id) ON DELETE SET NULL,
    stance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(idea_id, visitor_id)
);
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    assignee TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    due_at TEXT,
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_estimate REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_session_model ON usage(session_id, model);
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    closes_at TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS vote_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id INTEGER NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    idea_id INTEGER REFERENCES ideas(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS vote_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id INTEGER NOT NULL REFERENCES votes(id) ON DELETE CASCADE,
    visitor_id TEXT NOT NULL REFERENCES visitors(visitor_id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES vote_options(id) ON DELETE CASCADE,
    updated_at TEXT NOT NULL,
    UNIQUE(vote_id, visitor_id)
);
CREATE INDEX IF NOT EXISTS vote_records_vote_option ON vote_records(vote_id, option_id);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    source_vote_id INTEGER REFERENCES votes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS room_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE REFERENCES rooms(session_id) ON DELETE CASCADE,
    minutes_markdown TEXT NOT NULL,
    retrospective_markdown TEXT NOT NULL,
    missing_dimensions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS room_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES rooms(session_id) ON DELETE CASCADE,
    visitor_id TEXT NOT NULL REFERENCES visitors(visitor_id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS room_documents_session_created ON room_documents(session_id, created_at DESC);
CREATE TABLE IF NOT EXISTS document_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES room_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    UNIQUE(document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS document_chunks_document_index ON document_chunks(document_id, chunk_index);
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
        todo_columns = {row[1] for row in connection.execute("PRAGMA table_info(todos)")}
        if "due_at" not in todo_columns:
            connection.execute("ALTER TABLE todos ADD COLUMN due_at TEXT")
        room_columns = {row[1] for row in connection.execute("PRAGMA table_info(rooms)")}
        if "closed_at" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN closed_at TEXT")
        if "red_team_enabled" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN red_team_enabled INTEGER NOT NULL DEFAULT 1")
        if "red_team_count" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN red_team_count INTEGER NOT NULL DEFAULT 0")
        if "password_hash" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
        if "template_id" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN template_id TEXT NOT NULL DEFAULT 'general'")
        if "last_activity_at" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN last_activity_at TEXT")
        if "cleanup_warning_sent_at" not in room_columns:
            connection.execute("ALTER TABLE rooms ADD COLUMN cleanup_warning_sent_at TEXT")
        connection.execute("UPDATE rooms SET last_activity_at = created_at WHERE last_activity_at IS NULL")
        idea_columns = {row[1] for row in connection.execute("PRAGMA table_info(ideas)")}
        if "strengths" not in idea_columns:
            connection.execute("ALTER TABLE ideas ADD COLUMN strengths TEXT NOT NULL DEFAULT ''")
        if "tradeoffs" not in idea_columns:
            connection.execute("ALTER TABLE ideas ADD COLUMN tradeoffs TEXT NOT NULL DEFAULT ''")
