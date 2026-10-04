"""把单词按词根/词缀归类写回 words.root_id。

策略是规则匹配（无现成开源词根数据集可采用）：
  前缀型：spelling 以 variant 开头，且剩余部分 >= 2 个字符
  后缀型：spelling 以 variant 结尾，且剩余部分 >= 3 个字符
  词根型：spelling 包含 variant，且 variant >= 3 个字符、剩余 >= 2 个字符

多个命中时打分择优选主词根：词根型 > 前缀型 > 后缀型，其次匹配片段越长越优先。
root_source 记录归类方式（auto/manual），manual 的结果不会被本脚本覆盖。

用法：
    .venv/bin/python scripts/classify_roots.py              # 只补空缺
    .venv/bin/python scripts/classify_roots.py --force      # 重算全量
    .venv/bin/python scripts/classify_roots.py --audit      # 抽样打印归类结果
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import OVERRIDES_PATH, ROOTS_SEED_PATH  # noqa: E402
from app.db import executemany, get_conn, query  # noqa: E402

# 各类匹配的打分权重与最小长度约束。
# 短词缀极易误伤基础词（read→re-、dead→de-），故前缀要求剩余主干 >= 4 个字符。
TYPE_WEIGHT = {"root": 3000, "prefix": 2000, "suffix": 1000}
MIN_VARIANT = {"root": 3, "prefix": 2, "suffix": 3}
MIN_REMAINDER = {"root": 3, "prefix": 4, "suffix": 3}


def load_seed() -> list[dict]:
    with open(ROOTS_SEED_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_overrides() -> dict[str, str | None]:
    """人工纠正表 {单词: 词根名 或 None}，用于修正规则匹配的误判。

    None 表示「明确不属于任何词根」，避免规则再次把它匹配上去。
    已存在的 override 会同步标记为 manual，脚本重跑不会将其覆盖。
    """
    if not OVERRIDES_PATH.exists():
        return {}
    with open(OVERRIDES_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k.lower(): v for k, v in raw.items()}


def sync_roots(seed: list[dict]) -> dict[str, int]:
    """写入 roots / root_variants，返回 root 标识 -> id 映射。"""
    root_ids: dict[str, int] = {}
    variant_rows: list[tuple] = []
    with get_conn() as conn:
        for item in seed:
            cur = conn.execute(
                """INSERT INTO roots (root, type, meaning, origin, description)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(root) DO UPDATE SET
                     type=excluded.type, meaning=excluded.meaning,
                     origin=excluded.origin, description=excluded.description
                   RETURNING id""",
                (
                    item["root"],
                    item.get("type", "root"),
                    item.get("meaning", ""),
                    item.get("origin", ""),
                    item.get("description", ""),
                ),
            )
            rid = cur.fetchone()[0]
            root_ids[item["root"]] = rid
            conn.execute("DELETE FROM root_variants WHERE root_id=?", (rid,))
            for v in {item["root"].strip("-"), *(item.get("variants") or [])}:
                v = v.strip().lower()
                if v:
                    variant_rows.append((rid, v))
        conn.executemany(
            "INSERT INTO root_variants (root_id, variant) VALUES (?,?)", variant_rows
        )
    return root_ids


def build_variant_index(seed: list[dict]) -> list[tuple[str, str, str, int]]:
    """展开成 (variant, root_key, type, min_remainder) 列表，长片段优先排序。

    词根名本身只有在 >=4 字符时才自动作为变体，否则短名（ver/min/nat）
    会绕过人工收窄的 variants 列表，制造大量误命中。
    """
    index: list[tuple[str, str, str, int]] = []
    for item in seed:
        rtype = item.get("type", "root")
        variants = set(item.get("variants") or [])
        if len(item["root"].strip("-")) >= 4:
            variants.add(item["root"].strip("-"))
        for v in variants:
            v = v.strip().lower()
            if len(v) >= MIN_VARIANT[rtype]:
                index.append((v, item["root"], rtype, MIN_REMAINDER[rtype]))
    index.sort(key=lambda t: -len(t[0]))
    return index


def match_root(spelling: str, index: list[tuple[str, str, str, int]]) -> str | None:
    word = spelling.lower()
    best: tuple[int, str] | None = None
    for variant, root_key, rtype, min_rem in index:
        if len(word) - len(variant) < min_rem:
            continue
        if rtype == "prefix":
            hit = word.startswith(variant)
            pos = 0
        elif rtype == "suffix":
            hit = word.endswith(variant)
            pos = len(word) - len(variant)
        else:
            pos = word.find(variant)
            hit = pos >= 0
        if not hit:
            continue
        score = TYPE_WEIGHT[rtype] + len(variant) * 10 - min(pos, 8)
        if best is None or score > best[0]:
            best = (score, root_key)
    return best[1] if best else None


def main() -> None:
    parser = argparse.ArgumentParser(description="按词根归类单词")
    parser.add_argument("--force", action="store_true", help="重算所有单词（含已有归类）")
    parser.add_argument("--audit", action="store_true", help="抽样打印归类详情")
    parser.add_argument("--audit-top", type=int, default=30, help="抽样覆盖的词根数量")
    parser.add_argument("--audit-each", type=int, default=8, help="每词根抽样条数")
    args = parser.parse_args()

    seed = load_seed()
    root_ids = sync_roots(seed)
    index = build_variant_index(seed)
    print(f"词根种子 {len(seed)} 条，可用匹配片段 {len(index)} 个")

    where = "" if args.force else "WHERE w.root_source <> 'manual'"
    words = query(f"SELECT wordid, spelling FROM words w {where}")
    print(f"待处理单词 {len(words)} 条")

    overrides = load_overrides()
    updates: list[tuple] = []
    counter: Counter[str] = Counter()
    for row in words:
        spelling = row["spelling"].lower()
        if spelling in overrides:
            key = overrides[spelling]
            updates.append((root_ids.get(key) if key else None, "manual", row["wordid"]))
            counter[key or "(明确无词根)"] += 1
            continue
        key = match_root(spelling, index)
        if key:
            counter[key] += 1
            updates.append((root_ids[key], "auto", row["wordid"]))
        else:
            updates.append((None, "none", row["wordid"]))

    executemany(
        "UPDATE words SET root_id=?, root_source=? WHERE wordid=?", updates
    )

    counter.pop("(明确无词根)", None)
    classified = sum(counter.values())
    total = query("SELECT COUNT(*) c FROM words")[0]["c"]
    print(
        f"归类完成：命中 {classified} / {len(words)} 条，"
        f"覆盖率 {classified / total * 100:.1f}%（全库 {total} 词）"
    )

    print("\n词量 Top 20 词根：")
    for root_key, cnt in counter.most_common(20):
        print(f"  {root_key:<12} {cnt:>5}")

    if args.audit:
        top = [k for k, _ in counter.most_common(args.audit_top)]
        print("\n抽样复核：")
        for root_key in top:
            samples = query(
                """SELECT w.spelling, w.meaning
                   FROM words w JOIN roots r ON r.id = w.root_id
                   WHERE r.root = ? ORDER BY w.frequency LIMIT ?""",
                (root_key, args.audit_each),
            )
            joined = ", ".join(f"{s['spelling']}" for s in samples)
            print(f"  [{root_key:<10}] {joined}")

    unclassified = query(
        "SELECT COUNT(*) c FROM words WHERE root_source='none'"
    )[0]["c"]
    print(f"\n未归类（散落到「其他」）：{unclassified} 词")


if __name__ == "__main__":
    main()
