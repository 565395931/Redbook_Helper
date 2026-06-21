from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "redbook.db"


def _dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT,
                cookie TEXT NOT NULL,
                login_method TEXT NOT NULL DEFAULT 'manual_cookie',
                login_status TEXT NOT NULL DEFAULT 'logged_in',
                is_current INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS competitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                name TEXT,
                profile_url TEXT NOT NULL,
                last_crawled_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                competitor_id INTEGER REFERENCES competitors(id) ON DELETE SET NULL,
                source TEXT NOT NULL DEFAULT 'competitor',
                platform_note_id TEXT,
                note_url TEXT,
                note_type TEXT,
                author_name TEXT,
                title TEXT,
                body TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                image_urls_json TEXT NOT NULL DEFAULT '[]',
                like_count INTEGER NOT NULL DEFAULT 0,
                collect_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                share_count INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                ai_score_json TEXT NOT NULL DEFAULT '{}',
                scoring_status TEXT NOT NULL DEFAULT 'idle',
                scoring_error TEXT,
                scoring_started_at TEXT,
                published_at TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_hidden INTEGER NOT NULL DEFAULT 0,
                UNIQUE(account_id, platform_note_id)
            );

            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                source_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
                combine_theme TEXT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                duplicate_segments_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS published_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                platform_note_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS brand_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id) ON DELETE CASCADE,
                main_theme TEXT,
                audience TEXT,
                tone TEXT,
                product_points TEXT,
                banned_words TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS image_references (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
                label TEXT,
                image_url TEXT,
                local_path TEXT,
                analysis TEXT,
                status TEXT NOT NULL DEFAULT 'reference',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS note_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                image_index INTEGER NOT NULL DEFAULT 0,
                remote_url TEXT NOT NULL,
                local_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(note_id, image_index)
            );

            CREATE TABLE IF NOT EXISTS crawl_usage (
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                usage_date TEXT NOT NULL,
                used_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_id, usage_date)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS copy_generations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                source_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
                generation_mode TEXT NOT NULL DEFAULT 'normal',
                differentiation_level TEXT,
                reference_title TEXT,
                reference_content TEXT,
                post_topic TEXT NOT NULL,
                post_type TEXT NOT NULL,
                post_goal TEXT NOT NULL,
                word_count TEXT,
                core_message TEXT,
                titles_json TEXT NOT NULL DEFAULT '[]',
                body TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        for statement in [
            "ALTER TABLE accounts ADD COLUMN phone TEXT",
            "ALTER TABLE accounts ADD COLUMN login_method TEXT NOT NULL DEFAULT 'manual_cookie'",
            "ALTER TABLE accounts ADD COLUMN login_status TEXT NOT NULL DEFAULT 'logged_in'",
            "ALTER TABLE notes ADD COLUMN author_name TEXT",
            "ALTER TABLE notes ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE notes ADD COLUMN ai_score_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE notes ADD COLUMN scoring_status TEXT NOT NULL DEFAULT 'idle'",
            "ALTER TABLE notes ADD COLUMN scoring_error TEXT",
            "ALTER TABLE notes ADD COLUMN scoring_started_at TEXT",
            "ALTER TABLE notes ADD COLUMN published_at TEXT",
            "ALTER TABLE app_settings ADD COLUMN created_at TEXT",
            "ALTER TABLE copy_generations ADD COLUMN word_count TEXT",
            "ALTER TABLE copy_generations ADD COLUMN generation_mode TEXT NOT NULL DEFAULT 'normal'",
            "ALTER TABLE copy_generations ADD COLUMN differentiation_level TEXT",
            "ALTER TABLE copy_generations ADD COLUMN reference_title TEXT",
            "ALTER TABLE copy_generations ADD COLUMN reference_content TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            """
            UPDATE app_settings
            SET created_at = COALESCE(NULLIF(created_at, ''), updated_at, CURRENT_TIMESTAMP)
            """
        )
        conn.execute(
            """
            UPDATE notes
            SET scoring_status = 'failed',
                scoring_error = '打分任务因服务重启而中断，请重新打分。'
            WHERE scoring_status = 'scoring'
            """
        )
        conn.execute(
            """
            UPDATE notes
            SET score = 0,
                summary = COALESCE(NULLIF(summary, ''), '未打分')
            WHERE COALESCE(ai_score_json, '{}') = '{}'
            """
        )


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
