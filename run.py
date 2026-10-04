"""服务入口：python run.py"""

from __future__ import annotations

import argparse
import webbrowser

from app import create_app
from app.db import db_exists


def main() -> None:
    parser = argparse.ArgumentParser(description="英语词根学习站")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    # 本地开发默认开启调试器 + 热重载（改动 .py 后进程自动重启），--no-debug 可关闭
    parser.add_argument("--no-debug", dest="debug", action="store_false",
                        help="关闭调试模式与热重载（默认开启）")
    parser.set_defaults(debug=True)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not db_exists():
        raise SystemExit(
            "数据库不存在，请先执行：\n"
            "  .venv/bin/python scripts/fetch_corpus.py\n"
            "  .venv/bin/python scripts/build_db.py\n"
            "  .venv/bin/python scripts/classify_roots.py"
        )

    app = create_app()
    if args.debug:
        # 开发期禁用静态资源缓存：改完 app.js / app.css 不必再强制刷新浏览器
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    url = f"http://{args.host}:{args.port}"
    print(f"已就绪，访问 {url}  （Ctrl+C 退出）")
    if args.debug:
        print("热重载已开启：修改 Python / 模板 / 静态资源后自动生效，无需手动重启")
    if not args.no_browser:
        # 调试模式下 werkzeug 会另起子进程执行本脚本，避免重复弹出浏览器
        from werkzeug.serving import is_running_from_reloader

        if not args.debug or is_running_from_reloader():
            try:
                webbrowser.open(url)
            except Exception:
                pass
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
