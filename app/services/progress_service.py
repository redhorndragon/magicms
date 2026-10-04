"""掌握状态的业务规则层：路由只需调用这里，不直接写 SQL。"""

from __future__ import annotations

from typing import Any

from ..models import queries

STATUS_LABELS = {
    "unknown": "不认识",
    "learning": "学习中",
    "mastered": "已掌握",
    "starred": "已收藏",
}

STATUS_ORDER = ("unknown", "learning", "mastered")


def cycle(wordid: int, username: str = "") -> dict[str, Any] | None:
    return queries.cycle_status(wordid, username)


def set_status(wordid: int, username: str, status: str) -> dict[str, Any] | None:
    if status not in STATUS_ORDER:
        raise ValueError(f"未知状态：{status}")
    return queries.upsert_progress(wordid, username, status=status)


def toggle_star(wordid: int, username: str = "") -> dict[str, Any] | None:
    return queries.toggle_star(wordid, username)


def update_note(wordid: int, username: str, note: str) -> dict[str, Any] | None:
    return queries.upsert_progress(wordid, username, note=note.strip())


def reset(status: str | None = None, username: str = "") -> int:
    return queries.reset_progress(status, username)
