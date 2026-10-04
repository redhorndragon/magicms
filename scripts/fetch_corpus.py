"""下载开源语料到 data/raw/。

数据源：https://github.com/zhenghaoyang24/english-vocabulary
支持本地缓存（已存在即跳过）与断点续传。

用法：
    .venv/bin/python scripts/fetch_corpus.py            # 缺失才下载
    .venv/bin/python scripts/fetch_corpus.py --force    # 强制重下
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import (  # noqa: E402
    CORPUS_BASE_URL,
    CORPUS_FILES,
    DOWNLOAD_RETRIES,
    DOWNLOAD_TIMEOUT,
    RAW_DIR,
)

CHUNK_SIZE = 1 << 18  # 256KB

# 服务端未返回 Content-Length 时的体积估算（字节），仅用于进度显示
ESTIMATED_SIZE = {
    "books": 1 * 1024,
    "voc_book": 2.2 * 1024 * 1024,
    "vocabulary": 52 * 1024 * 1024,
    "examples": 39 * 1024 * 1024,
}


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _log_size(path: Path) -> None:
    print(f"    实际大小 {_human(path.stat().st_size)}")


def download(key: str, filename: str, force: bool) -> bool:
    """下载单个文件，返回是否成功。"""
    dest = RAW_DIR / filename
    url = f"{CORPUS_BASE_URL}/{filename}"

    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"[{key}] 已存在，跳过：{dest.name}")
        _log_size(dest)
        return True

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    last_err: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            downloaded = tmp.stat().st_size if tmp.exists() else 0
            headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
            # 流式读取，(connect, read) 分别设超时，避免大文件被单次读超时掐断
            resp = requests.get(
                url, stream=True, timeout=(10, DOWNLOAD_TIMEOUT), headers=headers
            )
            # 服务端忽略 Range 时会返回 200，此时必须从头写
            if resp.status_code == 416:
                downloaded = 0
                tmp.unlink(missing_ok=True)
                resp.close()
                continue
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code}")

            if resp.status_code == 200:
                downloaded = 0
                mode = "wb"
            else:
                mode = "ab"

            # 服务端开启 gzip 时 Content-Length 是压缩后体积，不能用作总大小
            compressed = (resp.headers.get("Content-Encoding", "identity")) != "identity"
            total_raw = resp.headers.get("Content-Range")
            if total_raw and "/" in total_raw:
                total = int(total_raw.split("/")[-1])
            elif resp.headers.get("Content-Length") and not compressed:
                total = downloaded + int(resp.headers["Content-Length"])
            else:
                total = ESTIMATED_SIZE.get(key, 0)

            with open(tmp, mode) as fh:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = min(downloaded / total * 100, 100.0)
                        print(
                            f"\r[{key}] {pct:5.1f}%  {_human(downloaded)} / {_human(total)}",
                            end="",
                            flush=True,
                        )
                print(flush=True)
            resp.close()

            if downloaded == 0:
                raise RuntimeError("下载内容为空")
            tmp.replace(dest)
            print(f"[{key}] 完成 -> {dest.name}")
            _log_size(dest)
            return True

        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"\n[{key}] 第 {attempt} 次失败：{exc}", flush=True)
            if attempt < DOWNLOAD_RETRIES:
                time.sleep(2 * attempt)
        finally:
            pass

    print(f"[{key}] 下载失败（已重试 {DOWNLOAD_RETRIES} 次）：{last_err}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="下载英词语料")
    parser.add_argument("--force", action="store_true", help="忽略已存在的文件")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=list(CORPUS_FILES.keys()),
        help="只下载指定文件，便于分批执行",
    )
    args = parser.parse_args()

    print(f"目标目录：{RAW_DIR}")
    ok = True
    keys = args.only or ["books", "voc_book", "vocabulary", "examples"]
    for key in keys:
        if not download(key, CORPUS_FILES[key], args.force):
            ok = False
    if not ok:
        raise SystemExit("部分文件下载失败，请检查网络后重试")

    print("\n全部语料就绪")


if __name__ == "__main__":
    main()
