"""词根总览路由。"""

from __future__ import annotations

from flask import Blueprint, g, render_template, request

from ..models import queries

roots_bp = Blueprint("roots", __name__, url_prefix="/roots")

TYPE_LABELS = {
    "root": "词根",
    "prefix": "前缀",
    "suffix": "后缀",
}


@roots_bp.route("/")
def overview():
    exam = queries.normalize_exam(request.args.get("exam"))
    root_type = request.args.get("type", "all")
    groups = queries.roots_by_type(exam, g.user)

    if root_type != "all":
        groups = [g for g in groups if g["key"] == root_type]

    return render_template(
        "roots.html",
        groups=groups,
        type_labels=TYPE_LABELS,
        root_type=root_type,
        total_morphemes=sum(len(g["items"]) for g in groups),
        unclassified=queries.unclassified_summary(exam, g.user),
        exam=exam,
        tabs=queries.exam_tabs(exam, g.user),
        stats=queries.stats(exam, g.user),
    )
