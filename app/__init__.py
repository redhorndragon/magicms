"""Flask 应用工厂。"""

from __future__ import annotations

import re

from flask import Flask, g, render_template, session
from markupsafe import Markup, escape

from .config import DB_PATH, SECRET_KEY


def create_app() -> Flask:
    # 蓝图在工厂内导入：脚本只需 app.config 时不会被未就绪的路由拖累
    from .routes.api import api_bp
    from .routes.main import main_bp
    from .routes.roots import roots_bp

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["DB_PATH"] = str(DB_PATH)
    app.config["JSON_AS_ASCII"] = False
    # 会话用于记住当前选择的用户（无密码，仅在本机区分身份）
    app.config["SECRET_KEY"] = SECRET_KEY

    # 把旧版 progress 表升级为按用户隔离的结构（已有进度归入第一个配置用户）
    if DB_PATH.exists():
        from .db import ensure_collins_column, ensure_user_schema
        from .users import default_user

        ensure_user_schema(default_user())
        ensure_collins_column()

    @app.before_request
    def _bind_current_user() -> None:
        """每个请求确定当前用户：取自会话，非法则回落到第一个配置用户。"""
        from .users import load_users

        users = load_users()
        g.users = users
        g.user = session.get("rv_user")
        if g.user not in users:
            g.user = users[0]
        session.permanent = True
        session["rv_user"] = g.user

    @app.context_processor
    def _inject_user_context() -> dict:
        """让所有模板都能拿到用户列表与当前用户，无需逐个视图传参。"""
        from .users import load_users

        users = getattr(g, "users", None) or load_users()
        return {"users": users, "current_user": getattr(g, "user", None) or users[0]}

    app.register_blueprint(main_bp)
    app.register_blueprint(roots_bp)
    app.register_blueprint(api_bp)

    @app.template_filter("split_meanings")
    def _split_meanings(value: str) -> list[str]:
        """把 '撤回或撤消,缩回,缩进' 切成多个义项标签。"""
        if not value:
            return []
        parts = [p.strip() for p in re.split(r"[,，;；]", value) if p.strip()]
        return parts[:8] or [value]

    @app.template_filter("highlight")
    def _highlight(example: str, word: str) -> str:
        """在例句中高亮目标单词（含常见屈折变化），返回可安全插入的 HTML。"""
        if not example:
            return ""
        escaped = escape(example)
        if not word:
            return escaped
        base = re.escape(str(escape(word)))
        # 覆盖 s/es/d/ed/ing 等变化形式，避免 "abandon" 匹配不到 "abandoned"
        pattern = re.compile(rf"\b({base})(?:s|es|ed|d|ing|ment)?\b", re.IGNORECASE)
        # 必须在纯 str 上做替换：Markup 会把插入的标签再次转义
        return Markup(pattern.sub(r'<mark class="hl">\1</mark>', str(escaped)))

    @app.template_filter("collins_stars")
    def _collins_stars(value: int) -> str:
        """把星级渲染成实心星（★★★）；不显示空心星。0 星（未标注）返回空串。"""
        star = int(value or 0)
        if star <= 0:
            return ""
        return "★" * star

    @app.template_filter("origin_label")
    def _origin_label(value: str) -> str:
        return {
            "latin": "拉丁语",
            "greek": "希腊语",
            "old-english": "古英语",
        }.get(value or "", value or "—")

    @app.errorhandler(404)
    def _not_found(_err):  # noqa: ANN001, ANN202
        # 复用 base.html 需要 tabs/stats 上下文，这里补齐，避免模板取空值报错
        from .models import queries

        return (
            render_template(
                "404.html",
                exam="all",
                tabs=queries.exam_tabs("all", g.get("user", "")),
                stats=queries.stats("all", g.get("user", "")),
                q="",
            ),
            404,
        )

    app.jinja_env.auto_reload = True
    return app
