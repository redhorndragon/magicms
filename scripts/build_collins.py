"""按全语料词频为单词标注柯林斯星级（1–5 星）。

背景：本项目的开源语料（tb_vocabulary）**不含**柯林斯官方标注，只有归一化词频
frequency。这里依据柯林斯公布的分档规模，在「全语料词频排名」上切分，得到与之
等价的星级，用来标示单词的重要程度：

    5 星：累计前 700 词
    4 星：累计前 1800 词
    3 星：累计前 4000 词
    2 星：累计前 9300 词
    1 星：累计前 14700 词
    低于 1 星阈值：0（未标注）

注意：这是**基于词频的等价换算**，不是柯林斯官方授权数据。若后续拿到真实的
柯林斯词表，只需把结果写进 words.collins_star 即可，展示层无需改动。

幂等：每次执行都重新计算并全量写回。

用法：
    .venv/bin/python scripts/build_collins.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import RAW_DIR  # noqa: E402
from app.db import ensure_collins_column, executemany, query  # noqa: E402

# （累计词数, 对应星级）——按柯林斯公布的分档规模
COLLINS_BANDS: tuple[tuple[int, int], ...] = (
    (700, 5),
    (1800, 4),
    (4000, 3),
    (9300, 2),
    (14700, 1),
)


def main() -> None:
    corpus = RAW_DIR / "tb_vocabulary.json"
    if not corpus.is_file():
        sys.exit(f"找不到语料：{corpus}")

    with open(corpus, encoding="utf-8") as fh:
        raw = json.load(fh)

    ranked = sorted(
        ((int(item["wordid"]), float(item.get("frequency") or 0)) for item in raw),
        key=lambda kv: kv[1],
        reverse=True,
    )
    # 每档的 frequency 下限（从 5 星到 1 星，阈值递减）
    thresholds = [(ranked[min(cut, len(ranked)) - 1][1], star) for cut, star in COLLINS_BANDS]
    freq_of = dict(ranked)

    ensure_collins_column()
    counts = {star: 0 for star in (0, 1, 2, 3, 4, 5)}
    updates: list[tuple[int, int]] = []
    for row in query("SELECT wordid FROM words"):
        wordid = int(row["wordid"])
        freq = freq_of.get(wordid, 0.0)
        star = 0
        for threshold, value in thresholds:
            if freq >= threshold:
                star = value
                break
        counts[star] += 1
        updates.append((star, wordid))

    executemany("UPDATE words SET collins_star=? WHERE wordid=?", updates)

    print("柯林斯星级写入完成（按全语料词频分档，非柯林斯官方数据）")
    for star in (5, 4, 3, 2, 1, 0):
        print(f"  {star} 星: {counts[star]} 词" if star else f"  未标注: {counts[star]} 词")


if __name__ == "__main__":
    main()
