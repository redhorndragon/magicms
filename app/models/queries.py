"""查询层。"""

from __future__ import annotations

from typing import Any

from ..config import PAGE_SIZE, SEARCH_PAGE_SIZE
from ..db import execute, query, query_one

EXAMS = ("all", "ielts", "toefl")
STATUSES = ("unknown", "learning", "mastered")
SORTS = {
    "word": "w.spelling COLLATE NOCASE ASC",
    "freq": "w.frequency DESC, w.spelling ASC",
    "status": "COALESCE(p.status,'unknown') ASC, w.spelling ASC",
}


def normalize_exam(exam: str | None) -> str:
    return exam if exam in EXAMS else "all"


def normalize_status(status: str | None) -> str:
    """status 取值为 all/unknown/learning/mastered/starred 之一。"""
    if status in STATUSES or status == "starred":
        return status
    return "all"


def normalize_root_filter(root: str | None) -> str:
    """root 取值：数字 root_id，或 'none' 表示未归类词。"""
    if root in (None, "", "all"):
        return "all"
    if root == "none":
        return "none"
    try:
        int(root)
    except (TypeError, ValueError):
        return "all"
    return root


def _exam_clause(exam: str) -> tuple[str, dict[str, Any]]:
    """限制单词属于某个考试词书。统一用 w 作为 words 表别名。"""
    if exam == "all":
        return "", {}
    return (
        """AND EXISTS (
               SELECT 1 FROM word_books wb
               JOIN books b ON b.id = wb.book_id
               WHERE wb.wordid = w.wordid AND b.exam_type = :exam
           )""",
        {"exam": exam},
    )


# ---------------------------------------------------------------- 统计

def stats(exam: str = "all", username: str = "") -> dict[str, int]:
    """统计某个用户在该考试词书下的掌握情况。"""
    clause, params = _exam_clause(exam)
    params = {**params, "username": username}
    row = query_one(
        f"""SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN COALESCE(p.status,'unknown')='unknown'  THEN 1 ELSE 0 END) AS unknown,
              SUM(CASE WHEN COALESCE(p.status,'unknown')='learning' THEN 1 ELSE 0 END) AS learning,
              SUM(CASE WHEN COALESCE(p.status,'unknown')='mastered' THEN 1 ELSE 0 END) AS mastered,
              SUM(CASE WHEN p.starred=1 THEN 1 ELSE 0 END) AS starred,
              SUM(CASE WHEN p.wordid IS NOT NULL THEN 1 ELSE 0 END) AS touched
            FROM words w
            LEFT JOIN progress p ON p.wordid = w.wordid AND p.username = :username
            WHERE 1=1 {clause}""",
        params,
    )
    if not row:
        return {
            "total": 0,
            "unknown": 0,
            "learning": 0,
            "mastered": 0,
            "starred": 0,
            "touched": 0,
        }
    return {k: int(v or 0) for k, v in row.items()}


def exam_tabs(exam: str, username: str) -> list[dict[str, Any]]:
    tabs = [{"key": "all", "label": "全部"}, {"key": "ielts", "label": "雅思"}, {"key": "toefl", "label": "托福"}]
    for tab in tabs:
        tab["count"] = stats(tab["key"], username)["total"]
        tab["active"] = tab["key"] == exam
    return tabs


# ---------------------------------------------------------------- 词根

def root_overview(exam: str = "all", username: str = "") -> list[dict[str, Any]]:
    """词根总览：一次性聚合，避免逐条 COUNT 造成 N+1。进度按用户统计。"""
    clause, params = _exam_clause(exam)
    params = {**params, "username": username}
    return query(
        f"""SELECT r.id, r.root, r.type, r.meaning, r.origin,
                   COUNT(w.wordid) AS word_count,
                   SUM(CASE WHEN COALESCE(p.status,'unknown')='mastered' THEN 1 ELSE 0 END) AS mastered_count,
                   SUM(CASE WHEN p.starred=1 THEN 1 ELSE 0 END) AS starred_count
            FROM words w
            JOIN roots r ON r.id = w.root_id
            LEFT JOIN progress p ON p.wordid = w.wordid AND p.username = :username
            WHERE 1=1 {clause}
            GROUP BY r.id, r.root, r.type, r.meaning, r.origin
            ORDER BY word_count DESC, r.root ASC""",
        params,
    )


def unclassified_summary(exam: str = "all", username: str = "") -> dict[str, int]:
    clause, params = _exam_clause(exam)
    params = {**params, "username": username}
    row = query_one(
        f"""SELECT COUNT(*) AS word_count,
                   SUM(CASE WHEN COALESCE(p.status,'unknown')='mastered' THEN 1 ELSE 0 END) AS mastered_count
            FROM words w
            LEFT JOIN progress p ON p.wordid = w.wordid AND p.username = :username
            WHERE w.root_id IS NULL {clause}""",
        params,
    )
    return {
        "word_count": int(row["word_count"] or 0) if row else 0,
        "mastered_count": int(row["mastered_count"] or 0) if row else 0,
    }


def roots_by_type(exam: str = "all", username: str = "") -> list[dict[str, Any]]:
    """按前缀/词根/后缀分组，供侧栏树使用。"""
    groups: dict[str, list[dict[str, Any]]] = {"prefix": [], "root": [], "suffix": []}
    for row in root_overview(exam, username):
        groups.setdefault(row["type"], []).append(row)
    return [
        {"key": "root", "label": "词根", "desc": "决定单词核心含义", "items": groups.get("root", [])},
        {"key": "prefix", "label": "前缀", "desc": "改变方向、程度或否定", "items": groups.get("prefix", [])},
        {"key": "suffix", "label": "后缀", "desc": "决定词性与句式功能", "items": groups.get("suffix", [])},
    ]


def get_root(root_id: int) -> dict[str, Any] | None:
    return query_one("SELECT * FROM roots WHERE id=?", (root_id,))


def root_variants(root_id: int) -> list[str]:
    rows = query("SELECT variant FROM root_variants WHERE root_id=? ORDER BY variant", (root_id,))
    return [r["variant"] for r in rows]


# ---------------------------------------------------------------- 单词

def _list_where(
    exam: str,
    root: str,
    status: str,
    q: str,
) -> tuple[str, dict[str, Any]]:
    clause, params = _exam_clause(exam)

    if root == "none":
        clause += " AND w.root_id IS NULL"
    elif root != "all":
        clause += " AND w.root_id = :root_id"
        params["root_id"] = int(root)

    if status in STATUSES:
        clause += " AND COALESCE(p.status,'unknown') = :status"
        params["status"] = status
    elif status == "starred":
        clause += " AND p.starred = 1"

    if q:
        # 拼写用 LIKE；中文释义命中时同样支持
        clause += " AND (LOWER(w.spelling) LIKE :q OR w.meaning LIKE :q)"
        params["q"] = f"%{q.lower()}%"

    return clause, params


def list_words(
    exam: str = "all",
    root: str = "all",
    status: str = "all",
    q: str = "",
    sort: str = "word",
    page: int = 1,
    page_size: int = PAGE_SIZE,
    username: str = "",
) -> tuple[list[dict[str, Any]], int, int]:
    clause, params = _list_where(exam, root, status, q)
    params = {**params, "username": username}
    total_row = query_one(
        f"""SELECT COUNT(*) AS c FROM words w
            LEFT JOIN progress p ON p.wordid = w.wordid AND p.username = :username
            WHERE 1=1 {clause}""",
        params,
    )
    total = int(total_row["c"] if total_row else 0)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, pages))

    order = SORTS.get(sort, SORTS["word"])
    rows = query(
        f"""SELECT w.wordid, w.spelling, w.uk_phonetic, w.us_phonetic,
                   w.pos, w.meaning, w.example_count, w.frequency,
                   w.root_id, w.root_source,
                   r.root AS root_name, r.meaning AS root_meaning, r.type AS root_type,
                   COALESCE(p.status,'unknown') AS status,
                   COALESCE(p.starred,0) AS starred
            FROM words w
            LEFT JOIN roots r ON r.id = w.root_id
            LEFT JOIN progress p ON p.wordid = w.wordid AND p.username = :username
            WHERE 1=1 {clause}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset""",
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    return rows, total, pages


def search_words(
    q: str, exam: str = "all", page: int = 1, username: str = ""
) -> tuple[list[dict[str, Any]], int, int]:
    return list_words(
        exam=exam, q=q, sort="word", page=page, page_size=SEARCH_PAGE_SIZE, username=username
    )


def get_word(wordid: int, username: str = "") -> dict[str, Any] | None:
    return query_one(
        """SELECT w.*, r.root AS root_name, r.type AS root_type,
                  r.meaning AS root_meaning, r.origin AS root_origin,
                  COALESCE(p.status,'unknown') AS status,
                  COALESCE(p.starred,0) AS starred,
                  COALESCE(p.note,'') AS note
           FROM words w
           LEFT JOIN roots r ON r.id = w.root_id
           LEFT JOIN progress p ON p.wordid = w.wordid AND p.username = ?
           WHERE w.wordid = ?""",
        (username, wordid),
    )


def get_examples(wordid: int) -> list[dict[str, Any]]:
    return query(
        "SELECT id, en, cn, heat FROM examples WHERE wordid=? ORDER BY heat DESC, LENGTH(en) ASC",
        (wordid,),
    )


def get_books(wordid: int) -> list[dict[str, Any]]:
    return query(
        """SELECT b.id, b.name, b.exam_type
           FROM word_books wb JOIN books b ON b.id = wb.book_id
           WHERE wb.wordid=? ORDER BY b.id""",
        (wordid,),
    )


def sibling_words(
    wordid: int, root_id: int | None, limit: int = 12, username: str = ""
) -> list[dict[str, Any]]:
    """同词根的相邻单词，用于详情页串记。"""
    if not root_id:
        return []
    return query(
        """SELECT w.wordid, w.spelling, w.meaning, COALESCE(p.status,'unknown') AS status
           FROM words w LEFT JOIN progress p ON p.wordid=w.wordid AND p.username=?
           WHERE w.root_id=? AND w.wordid<>?
           ORDER BY p.status IS NOT NULL, w.spelling
           LIMIT ?""",
        (username, root_id, wordid, limit),
    )


def neighbours(wordid: int) -> tuple[int | None, int | None]:
    """按拼写顺序取上一个/下一个单词，便于详情页翻页。"""
    prev_row = query_one(
        """SELECT wordid FROM words
           WHERE (spelling COLLATE NOCASE) < (SELECT spelling FROM words WHERE wordid=?)
           ORDER BY spelling COLLATE NOCASE DESC LIMIT 1""",
        (wordid,),
    )
    next_row = query_one(
        """SELECT wordid FROM words
           WHERE (spelling COLLATE NOCASE) > (SELECT spelling FROM words WHERE wordid=?)
           ORDER BY spelling COLLATE NOCASE ASC LIMIT 1""",
        (wordid,),
    )
    return (
        prev_row["wordid"] if prev_row else None,
        next_row["wordid"] if next_row else None,
    )


def review_words(exam: str = "all", limit: int = 40, username: str = "") -> list[dict[str, Any]]:
    """生词本：某用户明确标记为「不认识」或「学习中」的词。"""
    clause, params = _exam_clause(exam)
    return query(
        f"""SELECT w.wordid, w.spelling, w.uk_phonetic, w.us_phonetic,
                   w.pos, w.meaning, w.example_count,
                   r.root AS root_name, r.meaning AS root_meaning,
                   COALESCE(p.status,'unknown') AS status,
                   COALESCE(p.starred,0) AS starred
            FROM words w
            JOIN progress p ON p.wordid = w.wordid AND p.username = :username
            LEFT JOIN roots r ON r.id = w.root_id
            WHERE p.status IN ('unknown','learning') {clause}
            ORDER BY p.status='unknown' DESC, p.updated_at DESC, w.spelling
            LIMIT :limit""",
        {**params, "username": username, "limit": limit},
    )


# ---------------------------------------------------------------- 学习进度

def upsert_progress(
    wordid: int,
    username: str = "",
    status: str | None = None,
    starred: bool | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    """单条 UPSERT（按 用户+单词 唯一定位），返回更新后的完整状态；单词不存在时返回 None。"""
    if not query_one("SELECT 1 FROM words WHERE wordid=?", (wordid,)):
        # 提前拦截外键冲突，让调用方能区分「单词不存在」而不是 500
        return None
    existing = query_one(
        "SELECT * FROM progress WHERE wordid=? AND username=?", (wordid, username)
    )
    next_status = status if status in STATUSES else (existing["status"] if existing else "unknown")
    next_starred = (
        (1 if starred else 0)
        if starred is not None
        else (existing["starred"] if existing else 0)
    )
    next_note = note if note is not None else (existing["note"] if existing else "")

    execute(
        """INSERT INTO progress (wordid, username, status, starred, note, updated_at)
           VALUES (?,?,?,?,?, datetime('now','localtime'))
           ON CONFLICT(wordid, username) DO UPDATE SET
             status=excluded.status, starred=excluded.starred,
             note=excluded.note, updated_at=excluded.updated_at""",
        (wordid, username, next_status, next_starred, next_note),
    )
    row = query_one(
        """SELECT wordid, username, status, starred, note, updated_at
           FROM progress WHERE wordid=? AND username=?""",
        (wordid, username),
    )
    return row


def cycle_status(wordid: int, username: str = "") -> dict[str, Any] | None:
    """三态循环：unknown -> learning -> mastered -> unknown。"""
    if not query_one("SELECT 1 FROM words WHERE wordid=?", (wordid,)):
        return None
    row = query_one("SELECT status FROM progress WHERE wordid=? AND username=?", (wordid, username))
    current = row["status"] if row else "unknown"
    order = ("unknown", "learning", "mastered")
    nxt = order[(order.index(current) + 1) % len(order)]
    return upsert_progress(wordid, username, status=nxt)


def toggle_star(wordid: int, username: str = "") -> dict[str, Any] | None:
    if not query_one("SELECT 1 FROM words WHERE wordid=?", (wordid,)):
        return None
    row = query_one("SELECT starred FROM progress WHERE wordid=? AND username=?", (wordid, username))
    current = int(row["starred"]) if row else 0
    return upsert_progress(wordid, username, starred=(current == 0))


def reset_progress(status: str | None = None, username: str = "") -> int:
    """清空某个用户的进度；传 status 时只清该状态。"""
    if status in STATUSES:
        row_before = query_one(
            "SELECT COUNT(*) AS c FROM progress WHERE status=? AND username=?", (status, username)
        )
        execute("DELETE FROM progress WHERE status=? AND username=?", (status, username))
    else:
        row_before = query_one("SELECT COUNT(*) AS c FROM progress WHERE username=?", (username,))
        execute("DELETE FROM progress WHERE username=?", (username,))
    return int(row_before["c"] if row_before else 0)
