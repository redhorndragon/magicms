"""环境自检：解释器、依赖、语料、数据库是否就绪。

用法：.venv/bin/python scripts/check_env.py
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import (  # noqa: E402
    CORPUS_FILES,
    DB_PATH,
    RAW_DIR,
    ROOTS_SEED_PATH,
)

REQUIRED_PACKAGES = ("flask", "requests")
REQUIRED_TABLES = ("roots", "words", "examples", "books", "word_books", "progress")

ok = True


def check(label: str, passed: bool, detail: str = "") -> None:
    global ok
    ok = ok and passed
    print(f"  [{'OK' if passed else '!!'}] {label}{('：' + detail) if detail else ''}")


print("Python:", sys.version.split()[0], sys.executable)

print("依赖：")
for pkg in REQUIRED_PACKAGES:
    try:
        mod = importlib.import_module(pkg)
        version = importlib.metadata.version(pkg)
        check(pkg, True, version)
    except ImportError as exc:
        check(pkg, False, str(exc))

print("语料：")
for key, name in CORPUS_FILES.items():
    path = RAW_DIR / name
    state = path.exists() and path.stat().st_size > 0
    size = f"{path.stat().st_size / 1024 / 1024:.1f}MB" if state else "缺失"
    check(f"{key} ({name})", state, size)

print("词根种子：")
check("roots_seed.json", ROOTS_SEED_PATH.exists(), str(ROOTS_SEED_PATH.name))

print("数据库：")
if not DB_PATH.exists():
    check("vocab.db", False, "不存在，请运行 scripts/build_db.py")
else:
    check("vocab.db", True, f"{DB_PATH.stat().st_size / 1024 / 1024:.1f}MB")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in REQUIRED_TABLES:
            check(f"表 {table}", table in existing)
        if "words" in existing:
            words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
            examples = conn.execute("SELECT COUNT(*) FROM examples").fetchone()[0]
            classified = conn.execute(
                "SELECT COUNT(*) FROM words WHERE root_id IS NOT NULL"
            ).fetchone()[0]
            check("单词数", words > 0, f"{words} 词 / {examples} 例句 / 已归类 {classified}")
    finally:
        conn.close()

print("\n结论：", "环境就绪，可运行 .venv/bin/python run.py" if ok else "上面标记为 !! 的项目需要处理")
raise SystemExit(0 if ok else 1)
