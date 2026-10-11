"""将 Markdown 文档转换成 PDF（支持标题、段落、行内样式与表格）。

专为《柯林斯分级单词表》(books/collins.md) 这类「标题 + 段落 + 大表格」的
文档设计：词表用 reportlab 的 Table 排版，释义列自动换行，每页重复表头，
页码样式为「当前页/总页数」，字体复用项目里已验证可同时覆盖中文与 IPA 音标的
Arial Unicode。

用法：
    # 默认：把 books/collins.md 转成 books/collins.pdf
    .venv/bin/python scripts/md2pdf.py

    # 指定输入/输出
    .venv/bin/python scripts/md2pdf.py --in books/collins.md --out books/collins.pdf

说明：
    - 词表首列是冗余的行号（"#"），打印时意义不大，会自动丢弃，
      仅保留「单词 / 音标 / 释义 / 词频」。
    - 其余内容（标题、段落、总览表等）忠实于原 Markdown。
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.sax.saxutils as saxutils
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

BASE_DIR = Path(__file__).resolve().parent.parent

# 同时覆盖中文与 IPA 音标的字体（与 export_unknown.py 保持一致）
PDF_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
_PDF_FONT_NAME = "Md2PdfUnicode"


# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #
def _register_pdf_font() -> str:
    """注册 PDF 用字体；重复调用安全。找不到时给出明确指引。"""
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
        "找不到支持中文与音标的字体。请在 scripts/md2pdf.py 的 "
        "PDF_FONT_CANDIDATES 中补充本机可用的字体路径。"
    )


# --------------------------------------------------------------------------- #
# Markdown 解析
# --------------------------------------------------------------------------- #
_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _split_row(line: str) -> list[str]:
    """按 `|` 切分一行表格，去掉首尾空列（兼容行首/行尾的竖线）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_row(line: str) -> bool:
    return "|" in line and not line.lstrip().startswith("#")


def parse_markdown(text: str) -> list[dict]:
    """把 Markdown 文本解析成块列表，块类型：heading / paragraph / table。"""
    lines = text.splitlines()
    blocks: list[dict] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            blocks.append(
                {"type": "heading", "level": len(m.group(1)), "text": m.group(2).strip()}
            )
            i += 1
            continue

        # 表格：当前行像表格行，且下一行是分隔行
        if _is_table_row(line) and i + 1 < n and _SEP_RE.match(lines[i + 1]):
            header = _split_row(line)
            i += 2  # 跳过表头与分隔行
            rows: list[list[str]] = []
            while i < n and _is_table_row(lines[i]):
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue

        # 段落：连续非空、非标题、非表格的行合并
        # 但「- 」/「* 」开头的列表项各自成段，避免与正文粘连
        para: list[str] = []
        while i < n and lines[i].strip() and not re.match(r"^#{1,6}\s", lines[i]) \
                and not _is_table_row(lines[i]):
            line = lines[i].strip()
            if re.match(r"^[-*]\s", line):
                if para:
                    blocks.append({"type": "paragraph", "text": " ".join(para)})
                    para = []
                blocks.append({"type": "paragraph", "text": line})
                i += 1
                continue
            para.append(line)
            i += 1
        if para:
            blocks.append({"type": "paragraph", "text": " ".join(para)})

    return blocks


# --------------------------------------------------------------------------- #
# 行内样式（**粗体** / *斜体* / `代码`）
# --------------------------------------------------------------------------- #
def inline(text: str, code_font: str = "Courier") -> str:
    """把行内 Markdown 标记转成 reportlab Paragraph 能识别的 HTML 片段。"""
    codes: list[str] = []

    def _stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    # 先抽出代码段，避免其中的 *、` 被二次解析
    text = re.sub(r"`([^`]+)`", _stash, text)
    text = _xml_escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)

    def _restore(m: re.Match) -> str:
        raw = codes[int(m.group(1))]
        return f'<font face="{code_font}">{_xml_escape(raw)}</font>'

    return re.sub(r"\x00(\d+)\x00", _restore, text)


# --------------------------------------------------------------------------- #
# PDF 渲染
# --------------------------------------------------------------------------- #
def _build_pdf(md_text: str, out_path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        HRFlowable,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    font = _register_pdf_font()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="柯林斯分级单词表",
        author="english-study",
    )

    avail = doc.width  # 可用宽度

    # 样式
    h_styles = {
        1: ParagraphStyle("h1", fontName=font, fontSize=18, leading=22,
                          textColor=colors.HexColor("#111827"), spaceBefore=4, spaceAfter=8),
        2: ParagraphStyle("h2", fontName=font, fontSize=13, leading=16,
                          textColor=colors.HexColor("#1f2937"), spaceBefore=12, spaceAfter=6),
        3: ParagraphStyle("h3", fontName=font, fontSize=11, leading=14,
                          textColor=colors.HexColor("#374151"), spaceBefore=8, spaceAfter=4),
    }
    p_style = ParagraphStyle("p", fontName=font, fontSize=9.5, leading=15,
                             textColor=colors.HexColor("#374151"), spaceAfter=6)
    thead = ParagraphStyle("th", fontName=font, fontSize=8.5, leading=11,
                           textColor=colors.HexColor("#111827"))
    tcell = ParagraphStyle("td", fontName=font, fontSize=8, leading=10.5,
                           textColor=colors.HexColor("#111827"))
    tcell_r = ParagraphStyle("tdr", parent=tcell, alignment=2)  # 右对齐（词频）

    blocks = parse_markdown(md_text)
    story = []

    for blk in blocks:
        if blk["type"] == "heading":
            level = blk["level"]
            style = h_styles.get(level, h_styles[3])
            story.append(Paragraph(inline(blk["text"]), style))
            if level <= 2:
                story.append(HRFlowable(width="100%", thickness=0.6,
                                        color=colors.HexColor("#e5e7eb"),
                                        spaceBefore=2, spaceAfter=6))

        elif blk["type"] == "paragraph":
            story.append(Paragraph(inline(blk["text"]), p_style))

        elif blk["type"] == "table":
            story.extend(_build_table(blk, avail, font, thead, tcell, tcell_r))

    # 「当前页/总页数」需两遍绘制：缓存每页状态，保存时回填总页数
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.saveState()
                self.setFont(font, 8)
                self.setFillColor(colors.HexColor("#6b7280"))
                self.drawCentredString(A4[0] / 2, 8 * mm, f"{self._pageNumber}/{total}")
                self.restoreState()
                super().showPage()
            super().save()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])
    doc.build(story, canvasmaker=NumberedCanvas)


def _build_table(blk: dict, avail: float, font: str,
                 thead, tcell, tcell_r) -> list:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    header = list(blk["header"])
    rows = [list(r) for r in blk["rows"]]

    # 词表首列是行号（"#"），打印无意义，丢弃
    if header and header[0].strip() == "#":
        header = header[1:]
        rows = [r[1:] for r in rows if len(r) > 1]

    # 列宽：词频列右对齐且固定，释义列吃掉剩余空间（单位均为点/points）
    ncol = len(header)
    last_idx = ncol - 1
    freq_col = bool(header) and "词频" in header[last_idx] and ncol == 4
    if freq_col:
        word_w, phon_w, freq_w = 26 * mm, 30 * mm, 12 * mm
        meaning_w = avail - (word_w + phon_w + freq_w)
        col_widths = [word_w, phon_w, meaning_w, freq_w]
    else:
        col_widths = [avail / ncol] * ncol

    def make_cell(text, col):
        style = tcell_r if (freq_col and col == last_idx) else tcell
        return Paragraph(inline(text), style)

    data = [[Paragraph(f"<b>{inline(h)}</b>", thead) for h in header]]
    for r in rows:
        # 补齐缺列，截断多列
        r = (r + [""] * ncol)[:ncol]
        data.append([make_cell(c, ci) for ci, c in enumerate(r)])

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f9fafb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    tbl.setStyle(TableStyle(style))
    return [tbl, Spacer(1, 8)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown 转 PDF")
    default_in = BASE_DIR / "books" / "collins.md"
    parser.add_argument("--in", dest="infile", default=str(default_in),
                        help="输入 Markdown 路径（默认 books/collins.md）")
    parser.add_argument("--out", dest="outfile",
                        help="输出 PDF 路径（默认与输入同目录、同名 .pdf）")
    args = parser.parse_args()

    in_path = Path(args.infile)
    if not in_path.is_file():
        sys.exit(f"找不到输入文件：{in_path}")
    out_path = Path(args.outfile) if args.outfile else in_path.with_suffix(".pdf")

    md_text = in_path.read_text(encoding="utf-8")
    _build_pdf(md_text, out_path)
    print(f"已写入：{out_path}")


if __name__ == "__main__":
    main()
