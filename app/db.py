"""SQLite 连接封装与建表。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS roots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    root          TEXT    NOT NULL UNIQUE,
    type          TEXT    NOT NULL DEFAULT 'root',   -- prefix | suffix | root
    meaning       TEXT    NOT NULL DEFAULT '',
    origin        TEXT    NOT NULL DEFAULT '',       -- latin | greek | old-english ...
    description   TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS root_variants (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    root_id  INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    variant  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS words (
    wordid        INTEGER PRIMARY KEY,               -- 沿用语料库原始 id
    spelling      TEXT    NOT NULL,
    uk_phonetic   TEXT    NOT NULL DEFAULT '',
    us_phonetic   TEXT    NOT NULL DEFAULT '',
    pos           TEXT    NOT NULL DEFAULT '',       -- 由 paraphrase 拆出的词性
    meaning       TEXT    NOT NULL DEFAULT '',       -- 由 paraphrase 拆出的中文释义
    frequency     REAL    NOT NULL DEFAULT 0,
    root_id       INTEGER REFERENCES roots(id) ON DELETE SET NULL,
    root_source   TEXT    NOT NULL DEFAULT 'none',   -- auto | manual | none
    example_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS examples (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    wordid INTEGER NOT NULL REFERENCES words(wordid) ON DELETE CASCADE,
    en     TEXT    NOT NULL,
    cn     TEXT    NOT NULL DEFAULT '',
    heat   REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS books (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    exam_type  TEXT    NOT NULL,                     -- ielts | toefl
    word_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS word_books (
    wordid  INTEGER NOT NULL REFERENCES words(wordid) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    PRIMARY KEY (wordid, book_id)
);

-- 学习进度按用户隔离：同一单词每个用户各有一条记录
CREATE TABLE IF NOT EXISTS progress (
    wordid     INTEGER NOT NULL REFERENCES words(wordid) ON DELETE CASCADE,
    username   TEXT    NOT NULL DEFAULT '',           -- 归属用户，来自 users.json
    status     TEXT    NOT NULL DEFAULT 'unknown',   -- unknown | learning | mastered
    starred    INTEGER NOT NULL DEFAULT 0,
    note       TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (wordid, username)
);

CREATE INDEX IF NOT EXISTS idx_words_spelling   ON words(spelling);
CREATE INDEX IF NOT EXISTS idx_words_root       ON words(root_id);
CREATE INDEX IF NOT EXISTS idx_examples_wordid  ON examples(wordid);
CREATE INDEX IF NOT EXISTS idx_progress_user_status ON progress(username, status);
CREATE INDEX IF NOT EXISTS idx_word_books_book  ON word_books(book_id, wordid);
CREATE INDEX IF NOT EXISTS idx_word_books_word  ON word_books(wordid, book_id);
"""


@contextmanager
def get_conn(readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """每个请求一条短连接，避免 SQLite 对象跨线程使用。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(drop: bool = False) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        if drop:
            for table in (
                "progress",
                "word_books",
                "books",
                "examples",
                "words",
                "root_variants",
                "roots",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def ensure_user_schema(default_user: str) -> None:
    """把旧版 progress（主键仅 wordid）升级为按用户隔离的 (wordid, username)。

    已有进度会归入 ``default_user``（配置中的第一个用户），避免历史数据丢失。
    幂等：已经是新结构时只补齐索引等对象。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        # 迁移期间关闭外键：旧数据可能存在已不存在的 wordid，不应让迁移失败
        conn.execute("PRAGMA foreign_keys=OFF")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='progress'"
        ).fetchone()
        if not exists or _table_has_column(conn, "progress", "username"):
            conn.executescript(SCHEMA)
            return
        conn.executescript("ALTER TABLE progress RENAME TO progress_legacy;")
        conn.executescript(SCHEMA)
        conn.execute(
            """INSERT INTO progress (wordid, username, status, starred, note, updated_at)
               SELECT wordid, ?, status, starred, note, updated_at FROM progress_legacy""",
            (default_user,),
        )
        conn.execute("DROP TABLE progress_legacy")


def query(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with get_conn(readonly=True) as conn:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence[Any] = ()) -> None:
    with get_conn() as conn:
        conn.execute(sql, params)


def executemany(sql: str, params: Iterable[Sequence[Any]]) -> None:  # noqa: F821
    with get_conn() as conn:
        conn.executemany(sql, params)


def db_exists() -> bool:
    if not DB_PATH.exists():
        return False
    row = query_one("SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table'")
    return bool(row and row["c"])
