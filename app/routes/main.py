"""主页面路由：首页、单词列表、单词详情、搜索、复习。"""

from __future__ import annotations

import subprocess
import sys

from flask import (
    Blueprint,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from ..config import BASE_DIR, PAGE_SIZE
from ..models import queries
from ..services import progress_service

main_bp = Blueprint("main", __name__)

STATUS_FILTERS = [
    {"key": "all", "label": "全部"},
    {"key": "unknown", "label": "不认识"},
    {"key": "learning", "label": "学习中"},
    {"key": "mastered", "label": "已掌握"},
    {"key": "starred", "label": "收藏"},
]

SORT_FILTERS = [
    {"key": "word", "label": "按字母"},
    {"key": "freq", "label": "按词频"},
    {"key": "status", "label": "按掌握度"},
]


def _common_context(exam: str) -> dict:
    """各页面共用的导航上下文（统计按当前用户）。"""
    return {
        "exam": exam,
        "tabs": queries.exam_tabs(exam, g.user),
        "stats": queries.stats(exam, g.user),
    }


@main_bp.route("/docs")
@main_bp.route("/docs/<path:filename>")
def docs(filename: str = "index.html"):
    """预览 scripts/export_unknown.py 生成的静态页面（用于发布到 GitHub Pages）。"""
    docs_dir = BASE_DIR / "docs"
    if not (docs_dir / filename).is_file():
        return (
            render_template(
                "docs_missing.html",
                docs_dir=str(docs_dir),
                **_common_context(queries.normalize_exam(request.args.get("exam"))),
            ),
            404,
        )
    # send_from_directory 内部做了 safe_join，可防目录穿越
    return send_from_directory(docs_dir, filename)


def _run_export(extra_args: list[str]) -> tuple[bool, str]:
    """执行导出脚本，返回 (是否成功, 说明)。

    仅本机单机使用：命令与参数固定（用户名取自会话且已校验），不使用 shell。
    """
    script = BASE_DIR / "scripts" / "export_unknown.py"
    if not script.is_file():
        return False, f"找不到脚本：{script}"
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--user", g.user, *extra_args],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "导出超时（超过 180 秒）"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "").strip()[-500:] or "导出失败"
    return True, proc.stdout.strip()


@main_bp.route("/api/export", methods=["POST"])
def export_docs():
    """为当前用户生成/更新静态生词表（HTML）。"""
    ok, message = _run_export([])
    if not ok:
        return jsonify({"ok": False, "error": message}), 500
    return jsonify({"ok": True, "message": message}), 200


@main_bp.route("/docs/generate-pdf")
def generate_pdf():
    """生成 PDF 生词表（每页 3 栏）后跳转到该文件，便于直接查看或打印。"""
    filename = f"unknown-{g.user}.pdf"
    target = BASE_DIR / "docs" / filename
    ok, message = _run_export(["--format", "pdf", "--out", str(target)])
    if not ok or not target.is_file():
        return (
            render_template(
                "docs_missing.html",
                docs_dir=str(BASE_DIR / "docs"),
                error=message,
                **_common_context(queries.normalize_exam(request.args.get("exam"))),
            ),
            500,
        )
    return redirect(url_for("main.docs", filename=filename))


@main_bp.route("/user/<path:name>")
def switch_user(name: str):
    """切换当前用户（无密码，用户名必须存在于 users.json）。"""
    from ..users import load_users

    if name in load_users():
        session["rv_user"] = name
        session.permanent = True
    # 回到切换前所在页面；无来源时回首页
    return redirect(request.referrer or url_for("main.index"))


@main_bp.route("/")
def index():
    exam = queries.normalize_exam(request.args.get("exam"))
    overview = queries.root_overview(exam, g.user)[:24]
    unclassified = queries.unclassified_summary(exam, g.user)
    ctx = _common_context(exam)
    return render_template(
        "index.html",
        overview=overview,
        unclassified=unclassified,
        **ctx,
    )


@main_bp.route("/words")
def words():
    exam = queries.normalize_exam(request.args.get("exam"))
    root = queries.normalize_root_filter(request.args.get("root"))
    q = (request.args.get("q") or "").strip()
    raw_status = request.args.get("status")
    if raw_status:
        status = queries.normalize_status(raw_status)
    else:
        # 默认只展示「不认识」的词（含尚未标记的词），打开即进入待学列表，
        # 标记后会从列表里消失。但主动搜索时不过滤，否则搜不到已掌握的词。
        status = "all" if q else "unknown"
    sort = request.args.get("sort", "word")
    if sort not in queries.SORTS:
        sort = "word"
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1

    rows, total, pages = queries.list_words(
        exam=exam, root=root, status=status, q=q, sort=sort, page=page, username=g.user
    )

    current_root = queries.get_root(int(root)) if root not in ("all", "none") else None
    variants = queries.root_variants(int(root)) if current_root else []

    ctx = dict(
        words=rows,
        total=total,
        pages=pages,
        page=max(1, min(page, pages)),
        root=root,
        current_root=current_root,
        variants=variants,
        status=status,
        status_filters=STATUS_FILTERS,
        sort=sort,
        sort_filters=SORT_FILTERS,
        q=q,
        page_size=PAGE_SIZE,
        root_tree=queries.roots_by_type(exam, g.user),
        **_common_context(exam),
    )
    # 局部刷新：只返回右侧内容片段，左侧词根列表不重建，滚动位置与展开状态得以保留
    if request.args.get("partial") == "1":
        return render_template("_words_body.html", **ctx)
    return render_template("words.html", **ctx)


@main_bp.route("/word/<int:wordid>")
def word_detail(wordid: int):
    word = queries.get_word(wordid, g.user)
    if not word:
        abort(404)
    examples = queries.get_examples(wordid)
    prev_id, next_id = queries.neighbours(wordid)
    return render_template(
        "word_detail.html",
        word=word,
        examples=examples,
        examples_count=len(examples),
        books=queries.get_books(wordid),
        siblings=queries.sibling_words(wordid, word["root_id"], username=g.user),
        root_variants=queries.root_variants(word["root_id"]) if word["root_id"] else [],
        prev_id=prev_id,
        next_id=next_id,
        status_labels=progress_service.STATUS_LABELS,
        **_common_context(queries.normalize_exam(request.args.get("exam"))),
    )


@main_bp.route("/review")
def review():
    exam = queries.normalize_exam(request.args.get("exam"))
    rows = queries.review_words(exam, limit=60, username=g.user)
    sample_examples: dict[int, list] = {}
    for row in rows:
        sample_examples[row["wordid"]] = queries.get_examples(row["wordid"])[:2]
    return render_template(
        "review.html",
        words=rows,
        sample_examples=sample_examples,
        status_labels=progress_service.STATUS_LABELS,
        **_common_context(exam),
    )


@main_bp.route("/search")
def search():
    """独立于 /words 的入口，参数统一落到单词列表页处理。"""
    return redirect(
        url_for(
            "main.words",
            q=(request.args.get("q") or "").strip(),
            exam=queries.normalize_exam(request.args.get("exam")),
        )
    )
