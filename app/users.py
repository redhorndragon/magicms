"""多用户支持：用户名来自配置文件，无密码，通过会话切换身份。

配置文件为项目根目录的 ``users.json``，两种写法都支持::

    ["小明", "小红"]
    {"users": ["小明", "小红"]}

列表里的**第一个用户是默认用户**（也是历史进度迁移的归属）。
配置文件缺失或内容非法时，回退为单个 ``DEFAULT_USER``，保证应用仍可用。
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_USER, USERS_PATH

# 按 mtime 缓存，避免每次请求都读盘
_cache: tuple[float, list[str]] | None = None


def _parse(raw: object) -> list[str]:
    if isinstance(raw, dict):
        raw = raw.get("users", [])
    if not isinstance(raw, list):
        return []
    names = [str(item).strip() for item in raw]
    # 去重并保持原有顺序
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def load_users(path: Path | None = None) -> list[str]:
    """读取配置中的用户名列表，保证至少返回一个用户名。"""
    global _cache
    target = Path(path) if path else USERS_PATH

    try:
        mtime = target.stat().st_mtime
    except OSError:
        return [DEFAULT_USER]

    if path is None and _cache and _cache[0] == mtime:
        return list(_cache[1])

    try:
        users = _parse(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        users = []
    users = users or [DEFAULT_USER]

    if path is None:
        _cache = (mtime, users)
    return list(users)


def default_user(path: Path | None = None) -> str:
    """默认用户＝配置中的第一个，历史进度迁移也归到它名下。"""
    return load_users(path)[0]


def normalize_user(name: str | None, path: Path | None = None) -> str:
    """把任意输入（会话值、URL 参数）收敛为配置中存在的用户名。"""
    users = load_users(path)
    return name if name in users else users[0]
