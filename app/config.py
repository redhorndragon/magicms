"""集中管理路径与外部资源地址。"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "vocab.db"

# 多用户：用户名写在 users.json 里（无密码，通过会话切换身份）
USERS_PATH = BASE_DIR / "users.json"
DEFAULT_USER = "默认用户"
# Flask 会话签名密钥（用于记住当前用户）；本机单机使用，固定值即可
SECRET_KEY = "rootvocab-local-secret"

ROOTS_SEED_PATH = DATA_DIR / "roots_seed.json"
# 人工纠正表：单词 -> 词根名（或 null 表示不属于任何词根）
OVERRIDES_PATH = DATA_DIR / "root_overrides.json"

CORPUS_BASE_URL = (
    "https://raw.githubusercontent.com/zhenghaoyang24/english-vocabulary/master"
)

# 从 README 确认存在的单词书名称，脚本据此把单词划分到雅思/托福
CORPUS_FILES = {
    "books": "tb_book.json",
    "vocabulary": "tb_vocabulary.json",
    "examples": "tb_voc_examples.json",
    "voc_book": "tb_voc_book.json",
}

# 书名关键字 -> 考试类型
EXAM_KEYWORDS: list[tuple[str, str]] = [
    ("雅思", "ielts"),
    ("ielts", "ielts"),
    ("托福", "toefl"),
    ("toefl", "toefl"),
]

DOWNLOAD_TIMEOUT = 60
DOWNLOAD_RETRIES = 3

PAGE_SIZE = 48
SEARCH_PAGE_SIZE = 60
