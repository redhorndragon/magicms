"""导出某个用户「不认识」的单词，供发布成静态 HTML（如 GitHub Pages）。

生成的内容与网站单词列表的默认视图一致：状态为「不认识」的词
（含尚未标记的词，即 COALESCE(status,'unknown')='unknown'）。
每行一个单词，带音标与中文释义。

用法：
    # 默认：第一个配置用户 + 全部考试，输出 docs/index.html（GitHub Pages 可直接用 /docs）
    .venv/bin/python scripts/export_unknown.py

    # 指定用户与考试
    .venv/bin/python scripts/export_unknown.py --user 用户2 --exam ielts

    # 输出纯文本（制表符分隔：单词 / 音标 / 词性+释义）
    .venv/bin/python scripts/export_unknown.py --format txt --out vocab.txt

    # 按词频排序、只取前 500 个
    .venv/bin/python scripts/export_unknown.py --sort freq --limit 500
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.models import queries  # noqa: E402
from app.users import load_users  # noqa: E402

# 一次性取全部（词库量级约 6500，远小于此值）
PAGE_SIZE_ALL = 1_000_000

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>生词表 · {user}</title>
<style>
  :root {{ --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --accent:#4f46e5; }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{ margin:0; padding:32px 20px 64px; background:#f9fafb; color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
         counter-reset:w; }}
  .wrap {{ max-width:860px; margin:0 auto; }}
  h1 {{ font-size:26px; margin:0 0 6px; }}
  .meta {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
  .count {{ display:inline-block; background:#eef2ff; color:var(--accent);
            border-radius:999px; padding:3px 12px; font-weight:700; font-size:13px; }}
  /* 首字母索引条：吸顶，长列表里随时可跳转 */
  .az {{ position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:4px;
         padding:10px 0; margin-bottom:18px; background:rgba(249,250,251,.96);
         border-bottom:1px solid var(--line); }}
  .az a {{ text-decoration:none; color:var(--ink); font-weight:700; font-size:13px;
           min-width:28px; text-align:center; padding:4px 6px; border-radius:7px; }}
  .az a:hover {{ background:var(--accent); color:#fff; }}
  .az a i {{ font-style:normal; font-size:10px; font-weight:400;
             opacity:.6; margin-left:2px; font-variant-numeric:tabular-nums; }}
  .ltr {{ display:flex; align-items:center; gap:8px; font-size:18px;
          margin:26px 0 10px; scroll-margin-top:64px; }}
  .ltr span {{ font-size:12px; color:var(--muted); font-weight:400; }}
  .ltr .top {{ margin-left:auto; font-size:12px; color:var(--accent); text-decoration:none; }}
  ol {{ list-style:none; margin:0; padding:0; }}
  li {{ counter-increment:w; display:flex; gap:10px; align-items:baseline;
        background:#fff; border:1px solid var(--line); border-radius:12px;
        padding:12px 14px; margin-bottom:8px; }}
  li::before {{ content:counter(w); color:var(--muted); font-size:12px;
                min-width:38px; font-variant-numeric:tabular-nums; }}
  .word {{ font-weight:700; font-size:16px; min-width:0; }}
  .ph {{ color:var(--muted); font-size:13px; }}
  .collins {{ font-size:11px; color:#F59E0B; letter-spacing:1px; white-space:nowrap; }}
  .pos {{ color:var(--accent); font-size:12px; font-weight:600; }}
  .mean {{ color:var(--ink); font-size:14px; line-height:1.6; }}
  footer {{ margin-top:32px; color:var(--muted); font-size:12px; text-align:center; }}
  @media print {{
    body {{ background:#fff; padding:0; }}
    li {{ break-inside:avoid; border-color:#ddd; }}
    .az, .ltr .top {{ display:none; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <h1 id="top">生词表 · {user}</h1>
  <div class="meta">
    <span class="count">{total} 词</span>
    &nbsp;·&nbsp;{exam_label}&nbsp;·&nbsp;生成于 {generated}
  </div>
  <nav class="az">
{az_bar}
  </nav>
{sections}
  <footer>由 english-study 导出 · 数据来自开源词表，学习进度记录在本机</footer>
</div>
</body>
</html>
"""

ITEM_TEMPLATE = """    <li><span class="word">{word}</span>{collins}{phonetic}{pos}<span class="mean">{meaning}</span></li>"""

SECTION_TEMPLATE = """<h2 class="ltr" id="ltr-{anchor}">{letter}<span>{count} 词</span><a class="top" href="#top">↑ 回到顶部</a></h2>
<ol>
{items}
</ol>"""


_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    """释义/词性里可能含换行（会让一个单词占多行），统一折叠为单个空格。"""
    return _WS.sub(" ", (text or "").replace("\r", " ")).strip()


_SENSE_SEP = re.compile(r"[,，;；]")


def _truncate_senses(text: str, senses: int) -> str:
    """只保留前 senses 个义项（按 , ， ; ； 切分）；senses<=0 表示不截断。"""
    if senses <= 0:
        return text
    parts = [p.strip() for p in _SENSE_SEP.split(text) if p.strip()]
    if len(parts) <= senses:
        return text
    return "，".join(parts[:senses])


def _stars_of(row: dict) -> str:
    """柯林斯星级：只输出实心星（★★★），不补空心星。0 星返回空串。"""
    star = int(row.get("collins_star") or 0)
    if star <= 0:
        return ""
    return "★" * star


def _phonetic_of(row: dict) -> str:
    """英音/美音合并显示；两者相同则只显示一次。"""
    uk = (row.get("uk_phonetic") or "").strip()
    us = (row.get("us_phonetic") or "").strip()
    if uk and us:
        text = f"/{uk}/ /{us}/" if uk != us else f"/{uk}/"
    else:
        text = f"/{uk or us}/" if (uk or us) else ""
    return f'<span class="ph">{html.escape(text)}</span>' if text else ""


def _letter_of(spelling: str) -> str:
    """分组用的首字母；非字母归入 '#'。"""
    first = spelling[:1].upper()
    return first if first.isalpha() else "#"


def _anchor_of(letter: str) -> str:
    """'#' 不适合直接放进锚点，映射成 other。"""
    return "other" if letter == "#" else letter


def _render_item(row: dict) -> str:
    stars = _stars_of(row)
    return ITEM_TEMPLATE.format(
        word=html.escape(_clean(row["spelling"])),
        collins=f'<span class="collins">{stars}</span>' if stars else "",
        phonetic=_phonetic_of(row),
        pos=f'<span class="pos">{html.escape(_clean(row["pos"]))}</span>'
        if _clean(row.get("pos"))
        else "",
        meaning=html.escape(_clean(row.get("meaning"))),
    )


def render_html(rows: list[dict], user: str, exam: str) -> str:
    """按首字母分组输出：顶部吸顶索引条 + 带锚点的分段标题（编号跨段连续）。"""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(_letter_of(_clean(row["spelling"])), []).append(row)
    # '#' 组排在最后
    order = sorted(groups, key=lambda k: (k == "#", k))

    az_bar = "\n".join(
        f'    <a href="#ltr-{_anchor_of(k)}" title="{k}：{len(groups[k])} 词">'
        f"{k}<i>{len(groups[k])}</i></a>"
        for k in order
    )
    sections = "\n".join(
        SECTION_TEMPLATE.format(
            anchor=_anchor_of(k),
            letter=k,
            count=len(groups[k]),
            items="\n".join(_render_item(row) for row in groups[k]),
        )
        for k in order
    )
    return HTML_TEMPLATE.format(
        user=html.escape(user),
        total=len(rows),
        exam_label={"all": "全部", "ielts": "雅思", "toefl": "托福"}.get(exam, exam),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        az_bar=az_bar,
        sections=sections,
    )


def render_txt(rows: list[dict]) -> str:
    """一行一个单词：单词<TAB>音标<TAB>词性 + 释义。"""
    lines = []
    for row in rows:
        uk = (row.get("uk_phonetic") or "").strip()
        us = (row.get("us_phonetic") or "").strip()
        phonetic = f"/{uk}/ /{us}/" if (uk and us and uk != us) else f"/{uk or us}/"
        phonetic = "" if phonetic == "//" else phonetic
        # 词性本身已带句点（如 "vt."），不要再补一个
        pos = f"{_clean(row.get('pos'))} " if _clean(row.get("pos")) else ""
        word = _clean(row["spelling"])
        stars = _stars_of(row)
        head = f"{word} {stars}" if stars else word
        lines.append(f"{head}\t{phonetic}\t{pos}{_clean(row.get('meaning'))}")
    return "\n".join(lines) + "\n"


# 需要同时覆盖中文与 IPA 音标的字体（已验证 Arial Unicode 两者都全）
PDF_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
_PDF_FONT_NAME = "WordListUnicode"


def _register_pdf_font() -> str:
    """注册 PDF 用字体；重复调用安全。找不到时给出明确的补充指引。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.getFont(_PDF_FONT_NAME)
        return _PDF_FONT_NAME
    except Exception:
        pass
    for path in PDF_FONT_CANDIDATES:
        if Path(path).is_file():
            pdfmetrics.registerFont(TTFont(_PDF_FONT_NAME, path))
            return _PDF_FONT_NAME
    raise RuntimeError(
        "找不到支持中文与音标的字体。请在 scripts/export_unknown.py 的 "
        "PDF_FONT_CANDIDATES 中补充本机可用的字体路径（需同时含中文与 IPA 符号）。"
    )


def _render_pdf_line(row: dict, senses: int = 0) -> str:
    """一行：单词 /音标/ 词性 释义（释义按 senses 截断）。"""
    word = html.escape(_clean(row["spelling"]))
    uk = _clean(row.get("uk_phonetic"))
    us = _clean(row.get("us_phonetic"))
    if uk and us and uk != us:
        phonetic = f"/{uk}/ /{us}/"
    else:
        phonetic = f"/{uk or us}/" if (uk or us) else ""

    parts = [word]
    stars = _stars_of(row)
    if stars:
        parts.append(stars)
    if phonetic:
        parts.append(html.escape(phonetic))
    pos = _clean(row.get("pos"))
    if pos:
        parts.append(html.escape(pos))
    meaning = _clean(row.get("meaning"))
    if meaning:
        parts.append(html.escape(_truncate_senses(meaning, senses)))
    return " ".join(parts)


def render_pdf(
    rows: list[dict],
    user: str,
    exam: str,
    out_path: Path,
    columns: int = 3,
    senses: int = 2,
) -> None:
    """导出 PDF：每页 columns 栏，一行一个单词，带音标与释义（便于打印）。

    senses 控制每个词保留的义项数（默认 2，用于压缩页数；0 表示保留全部）。
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph

    font_name = _register_pdf_font()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"生词表 · {user}",
        author="english-study",
    )
    gutter = 4 * mm
    col_w = (doc.width - (columns - 1) * gutter) / columns
    frames = [
        Frame(
            doc.leftMargin + i * (col_w + gutter),
            doc.bottomMargin,
            col_w,
            doc.height,
            id=f"col{i}",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        for i in range(columns)
    ]

    # 「当前页/总页数」需要两遍绘制：每页先缓存状态，保存时才知道总页数，再回填
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_page_number(total)
                super().showPage()
            super().save()

        def _draw_page_number(self, total: int) -> None:
            self.saveState()
            self.setFont(font_name, 7)
            self.setFillColor(colors.HexColor("#6b7280"))
            self.drawCentredString(A4[0] / 2, 7 * mm, f"{self._pageNumber}/{total}")
            self.restoreState()

    doc.addPageTemplates([PageTemplate(id="cols", frames=frames)])

    head_style = ParagraphStyle(
        "head", fontName=font_name, fontSize=7.5, leading=10,
        textColor=colors.HexColor("#6b7280"), spaceAfter=2,
    )
    entry_style = ParagraphStyle(
        "entry", fontName=font_name, fontSize=8, leading=10.4,
        textColor=colors.HexColor("#111827"),
    )

    exam_label = {"all": "全部", "ielts": "雅思", "toefl": "托福"}.get(exam, exam)
    story = [Paragraph(f"生词表 · {user} · {exam_label} · 共 {len(rows)} 词", head_style)]
    story.extend(Paragraph(_render_pdf_line(row, senses), entry_style) for row in rows)
    doc.build(story, canvasmaker=NumberedCanvas)


def main() -> None:
    users = load_users()
    parser = argparse.ArgumentParser(description="导出「不认识」的单词列表")
    parser.add_argument("--user", default=users[0], help=f"用户名（默认 {users[0]}；可选：{'、'.join(users)}）")
    parser.add_argument("--exam", default="all", choices=["all", "ielts", "toefl"], help="考试词书（默认 all）")
    parser.add_argument("--sort", default="word", choices=["word", "freq"], help="排序（默认按字母）")
    parser.add_argument("--format", default="html", choices=["html", "txt", "pdf"], help="输出格式（默认 html）")
    parser.add_argument("--columns", type=int, default=3, help="PDF 每页栏数（默认 3，越少字越大）")
    parser.add_argument("--senses", type=int, default=2, help="PDF 里每个词保留的义项数（默认 2；0=保留全部）")
    parser.add_argument("--out", help="输出路径（默认 docs/index.html、unknown-<用户>.pdf/.txt）")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 个（默认全部）")
    args = parser.parse_args()

    if args.user not in users:
        sys.exit(f"用户名「{args.user}」不在 users.json 中，可选：{'、'.join(users)}")

    rows, total, _ = queries.list_words(
        exam=queries.normalize_exam(args.exam),
        root="all",
        status="unknown",
        q="",
        sort=args.sort,
        page=1,
        page_size=args.limit or PAGE_SIZE_ALL,
        username=args.user,
    )
    if args.limit:
        rows = rows[: args.limit]

    default_name = {
        "html": "index.html",
        "txt": f"unknown-{args.user}.txt",
        "pdf": f"unknown-{args.user}.pdf",
    }[args.format]
    out = Path(args.out) if args.out else BASE_DIR / "docs" / default_name
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "html":
        out.write_text(render_html(rows, args.user, args.exam), encoding="utf-8")
    elif args.format == "txt":
        out.write_text(render_txt(rows), encoding="utf-8")
    else:
        render_pdf(rows, args.user, args.exam, out, max(1, args.columns), args.senses)

    print(f"用户：{args.user}    考试：{args.exam}    导出：{len(rows)} / {total} 词")
    print(f"已写入：{out}")


if __name__ == "__main__":
    main()
