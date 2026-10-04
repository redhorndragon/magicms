"""异步接口：掌握状态标记、收藏、统计刷新。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from ..models import queries
from ..services import progress_service

api_bp = Blueprint("api", __name__, url_prefix="/api")

# :root/affix 之前或之后的 {{\s}}
def _respond(row: dict | None) -> tuple[Any, int]:
    """构造统一响应。

    注意：新版 Flask 的 jsonify(*args) 会把多余位置参数当作待合并的字典，
    因此这里不能写成 jsonify(*payload)，必须显式拆包后再返回状态码。
    """
    if not row:
        return jsonify({"ok": False, "error": "单词不存在"}), 404
    return (
        jsonify(
            {
                "ok": True,
                "wordid": row["wordid"],
                "status": row["status"],
                "starred": int(row["starred"]),
                "note": row.get("note", ""),
                "updated_at": row.get("updated_at", ""),
            }
        ),
        200,
    )


@api_bp.post("/word/<int:wordid>/cycle")
def cycle(wordid: int):
    """三态循环切换：不认识 -> 学习中 -> 已掌握（作用于当前用户）。"""
    return _respond(progress_service.cycle(wordid, g.user))


@api_bp.post("/word/<int:wordid>/status")
def set_status(wordid: int):
    body = request.get_json(silent=True) or {}
    try:
        row = progress_service.set_status(wordid, g.user, body.get("status", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return _respond(row)


@api_bp.post("/word/<int:wordid>/star")
def star(wordid: int):
    return _respond(progress_service.toggle_star(wordid, g.user))


@api_bp.post("/word/<int:wordid>/note")
def note(wordid: int):
    body = request.get_json(silent=True) or {}
    row = progress_service.update_note(wordid, g.user, str(body.get("note", ""))[:500])
    return _respond(row)


@api_bp.get("/stats")
def api_stats():
    exam = queries.normalize_exam(request.args.get("exam"))
    return jsonify({"ok": True, "exam": exam, "stats": queries.stats(exam, g.user)}), 200


@api_bp.post("/progress/reset")
def reset():
    """清空当前用户的学习进度（传 status 时只清该状态）。"""
    body = request.get_json(silent=True) or {}
    status = body.get("status") or None
    removed = progress_service.reset(
        status if status in queries.STATUSES else None, g.user
    )
    return jsonify({"ok": True, "removed": removed}), 200
