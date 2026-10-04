#!/bin/bash
# 启动脚本：绕过损坏的系统 python（python=2.7 / python3=Xcode 报错），
# 始终使用项目内的虚拟环境解释器。
set -e
cd "$(dirname "$0")"

PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "未找到虚拟环境 $PY，正在创建…"
  /Users/gengcy/.workbuddy/binaries/python/versions/3.11.9/bin/python3 -m venv .venv
  .venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
fi

if ! .venv/bin/python -c "from app.db import db_exists; import sys; sys.exit(0 if db_exists() else 1)"; then
  echo "数据库缺失，正在导入数据（首次约需 1 分钟）…"
  .venv/bin/python scripts/fetch_corpus.py
  .venv/bin/python scripts/build_db.py
  .venv/bin/python scripts/classify_roots.py
fi

exec .venv/bin/python run.py "$@"
