"""清洗开源语料并构建 SQLite 库。

流程：
  1. 读 tb_book / tb_voc_book，筛出雅思、托福两本书涉及的 wordid
  2. 读 tb_vocabulary，只保留目标词，拆分 paraphrase 为「词性 + 中文释义」后写 words
  3. 读 tb_voc_examples，只保留目标词的例句，按热度取前 N 条写 examples

幂等：每次执行先删表重建。

用法：
    .venv/bin/python scripts/build_db.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import (  # noqa: E402
    CORPUS_FILES,
    DB_PATH,
    EXAM_KEYWORDS,
    RAW_DIR,
)
from app.db import executemany, get_conn, init_schema, query  # noqa: E402

MAX_EXAMPLES_PER_WORD = 4
BATCH = 5000

# 匹配任意 CJK 字符，用于定位「词性 -> 中文释义」的分界
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def split_pos_meaning(paraphrase: str) -> tuple[str, str]:
    """把 'vt.& vi.撤回或撤消,缩回,缩进' 拆成 ('vt./vi.', '撤回或撤消,缩回,缩进')。

    语料里 paraphrase 是词性与中文释义的混合串，中文首字符即分界点。
    """
    text = (paraphrase or "").strip()
    if not text:
        return "", ""
    m = _CJK.search(text)
    if not m:
        # 没有中文释义，整串当作释义（诸如纯 "(pl.)" 之类兜底）
        return "", text
    pos, meaning = text[: m.start()], text[m.start() :]
    # 规范化：'vt.& vi.' -> 'vt./vi.'
    pos = pos.replace("&", "/").replace("，", ",")
    pos = re.sub(r"\s+", "", pos)
    pos = re.sub(r"/{2,}", "/", pos).strip("/· ")
    return pos, meaning.strip()


def load_json(name: str) -> Any:
    path = RAW_DIR / CORPUS_FILES[name]
    if not path.exists():
        raise SystemExit(
            f"缺少原始数据 {path}，请先执行：.venv/bin/python scripts/fetch_corpus.py"
        )
    t0 = time.time()
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    print(f"  载入 {path.name}（{time.time() - t0:.1f}s）")
    return data


def detect_exam(bookname: str) -> str | None:
    lower = (bookname or "").lower()
    for keyword, exam in EXAM_KEYWORDS:
        if keyword.lower() in lower:
            return exam
    return None


def pick_books(all_books: list[dict]) -> list[dict]:
    picked = []
    for b in all_books:
        exam = detect_exam(b.get("bookname", ""))
        if exam:
            picked.append(
                {
                    "id": b["bookid"],
                    "name": b["bookname"],
                    "exam_type": exam,
                    "word_count": b.get("voccount") or 0,
                }
            )
    return picked


def batched(items: list[tuple], size: int = BATCH) -> Iterable[list[tuple]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 SQLite 词库")
    parser.add_argument("--keep-progress", action="store_true", help="保留既有学习进度")
    args = parser.parse_args()

    t_start = time.time()

    if args.keep_progress:
        saved = None
        if DB_PATH.exists():
            saved = query("SELECT * FROM progress")
        init_schema(drop=True)
        if saved:
            executemany(
                """INSERT OR REPLACE INTO progress
                   (wordid, status, starred, note, updated_at) VALUES (?,?,?,?,?)""",
                [
                    (
                        r["wordid"],
                        r["status"],
                        r["starred"],
                        r["note"],
                        r["updated_at"],
                    )
                    for r in saved
                ],
            )
            print(f"  已保留 {len(saved)} 条学习进度")
    else:
        init_schema(drop=True)
    print("已重建表结构")

    # ---- 1. 单词书 -> 目标 wordid 集合 ----
    books = load_json("books")
    chosen_books = pick_books(books)
    if not chosen_books:
        raise SystemExit("未在书籍表中找到雅思/托福词书，请检查 EXAM_KEYWORDS 配置")
    for b in chosen_books:
        print(f"  命中词书：[{b['exam_type']}] {b['name']} (id={b['id']})")

    chosen_ids = {b["id"] for b in chosen_books}
    voc_book = load_json("voc_book")

    book_words: dict[int, set[int]] = defaultdict(set)
    for row in voc_book:
        if row["bookid"] in chosen_ids:
            book_words[row["bookid"]].add(row["wordid"])

    target_wordids: set[int] = set()
    for book_id, wordids in book_words.items():
        target_wordids |= wordids
    print(f"  目标单词数（去重）：{len(target_wordids)}")

    executemany(
        "INSERT INTO books (id, name, exam_type, word_count) VALUES (?,?,?,?)",
        [(b["id"], b["name"], b["exam_type"], b["word_count"]) for b in chosen_books],
    )

    # ---- 2. 词汇表（须先于词书关联写入，满足外键约束） ----
    vocabulary = load_json("vocabulary")
    word_rows: list[tuple] = []
    for row in vocabulary:
        wid = row["wordid"]
        if wid not in target_wordids:
            continue
        spelling = (row.get("spelling") or "").strip()
        if not spelling:
            continue
        pos, meaning = split_pos_meaning(row.get("paraphrase", ""))
        word_rows.append(
            (
                wid,
                spelling,
                (row.get("UKphonetic") or "").strip(),
                (row.get("USphonetic") or "").strip(),
                pos,
                meaning,
                float(row.get("frequency") or 0),
            )
        )
    del vocabulary

    for chunk in batched(word_rows):
        executemany(
            """INSERT OR REPLACE INTO words
               (wordid, spelling, uk_phonetic, us_phonetic, pos, meaning, frequency)
               VALUES (?,?,?,?,?,?,?)""",
            chunk,
        )
    print(f"  写入单词 {len(word_rows)} 条")
    inserted_ids = {r[0] for r in word_rows}
    del word_rows

    word_book_rows = [
        (w, book_id)
        for book_id, wordids in book_words.items()
        for w in wordids
        if w in inserted_ids
    ]
    for chunk in batched(word_book_rows):
        executemany(
            "INSERT OR IGNORE INTO word_books (wordid, book_id) VALUES (?,?)", chunk
        )
    missing = target_wordids - inserted_ids
    if missing:
        print(f"  注意：{len(missing)} 个 wordid 在词汇表中缺失，已跳过其词书关联")
    print(f"  写入词书关联 {len(word_book_rows)} 条")

    # ---- 3. 例句 ----
    examples = load_json("examples")
    per_word: dict[int, list[tuple]] = defaultdict(list)
    for row in examples:
        wid = row["wordid"]
        if wid not in target_wordids:
            continue
        en = (row.get("en") or "").strip()
        if not en:
            continue
        per_word[wid].append(
            (
                wid,
                en,
                (row.get("cn") or "").strip(),
                float(row.get("heat") or 0),
            )
        )
    del examples

    example_rows: list[tuple] = []
    counts: list[tuple] = []
    for wid, rows in per_word.items():
        rows.sort(key=lambda r: (-r[3], len(r[1])))
        top = rows[:MAX_EXAMPLES_PER_WORD]
        example_rows.extend(top)
        counts.append((len(top), wid))

    for chunk in batched(example_rows):
        executemany(
            "INSERT INTO examples (wordid, en, cn, heat) VALUES (?,?,?,?)", chunk
        )
    for chunk in batched(counts):
        executemany("UPDATE words SET example_count=? WHERE wordid=?", chunk)
    print(f"  写入例句 {len(example_rows)} 条，覆盖 {len(counts)} 个单词")

    with get_conn() as conn:
        conn.execute("ANALYZE")

    print(f"\n完成，耗时 {time.time() - t_start:.1f}s，数据库：{DB_PATH}")


if __name__ == "__main__":
    main()
