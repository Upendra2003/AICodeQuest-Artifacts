"""Renders docs/ARCHITECTURE.md into docs/ARCHITECTURE.docx.

ONE source, two formats. The markdown is the source of truth and the
.docx is generated from it, so the two cannot drift -- the alternative
(hand-maintaining a Word file alongside a markdown one) is how an
architecture document ends up describing a system that no longer exists.

    make architecture-doc
    # or: cd backend && ../.venv/bin/python scripts/generate_architecture_doc.py

Deliberately a small hand-rolled renderer rather than pandoc: pandoc is
not installed here and is a system package, whereas python-docx is
already a dependency (backend/app/services/report_docx.py has used it
since Phase 24). It handles exactly the markdown subset ARCHITECTURE.md
uses -- headings, paragraphs, bullet and numbered lists, fenced code
blocks, pipe tables, horizontal rules, and inline bold/italic/code -- and
RAISES on anything it does not understand rather than silently dropping
it, so a new construct in the markdown fails the build instead of
vanishing from the document.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from docx import Document  # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Inches, Pt, RGBColor  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "docs" / "ARCHITECTURE.md"
OUTPUT = REPO_ROOT / "docs" / "ARCHITECTURE.docx"

# The study's palette (config-adjacent, but these are document styling and
# not behaviour, so they live here rather than in params.yaml -- same call
# report_docx.py made).
INK = RGBColor(0x13, 0x34, 0x3B)
TEAL = RGBColor(0x11, 0x4F, 0x56)
MUTED = RGBColor(0x5C, 0x73, 0x79)
CODE_BG = "F3F3EE"


def _shade(cell_or_para, hex_fill: str) -> None:
    """Background fill. python-docx has no API for this, so it goes in as
    raw OOXML -- the standard workaround, used the same way in
    report_docx.py."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    element = cell_or_para._tc if hasattr(cell_or_para, "_tc") else cell_or_para._p.get_or_add_pPr()
    element.append(shd)


_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)")


def _add_inline(paragraph, text: str) -> None:
    """Renders **bold**, *italic* and `code` inside a paragraph.

    Nested spans are not supported and are not used in the source. A run
    of literal text is added verbatim, so an unmatched marker degrades to
    itself rather than eating the rest of the line.
    """
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            run = paragraph.add_run(part[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(9.5)
            run.font.color.rgb = TEAL
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def _add_code_block(doc, lines: list[str]) -> None:
    """A fenced block, as a single shaded one-cell table.

    A table rather than a paragraph because the ASCII diagrams in this
    document need a hard edge and a background; a shaded paragraph loses
    its fill at a page break in Word.
    """
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    # Full text width, and autofit OFF. Left to autofit, Word sizes the
    # cell to the text and the widest diagram line ran off the right edge.
    table.autofit = False
    # Keep the block whole. Without this a state diagram split across a
    # page break mid-arrow, which is worse than an early page break.
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:cantSplit"))
    cell = table.cell(0, 0)
    cell.width = Inches(6.7)
    _shade(cell, CODE_BG)
    cell.text = ""
    para = cell.paragraphs[0]
    para.paragraph_format.space_after = Pt(0)
    # A single run holding embedded newlines does not break across lines
    # in Word; each source line needs its own run + explicit break.
    run = para.add_run(lines[0] if lines else "")
    for extra in lines[1:]:
        run.add_break()
        run.add_text(extra)
    run.font.name = "Consolas"
    run.font.size = Pt(8)
    run.font.color.rgb = INK
    doc.add_paragraph()


def _add_table(doc, rows: list[list[str]]) -> None:
    header, *body = rows
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, text in enumerate(header):
        cell = table.rows[0].cells[i]
        _shade(cell, CODE_BG)
        cell.text = ""
        para = cell.paragraphs[0]
        para.paragraph_format.space_after = Pt(2)
        _add_inline(para, text)
        for run in para.runs:
            run.bold = True
            run.font.size = Pt(9)

    for row in body:
        cells = table.add_row().cells
        # A ragged row is a source typo; pad rather than crash, but only
        # after the header has fixed the column count.
        for i in range(len(header)):
            cell = cells[i]
            cell.text = ""
            para = cell.paragraphs[0]
            para.paragraph_format.space_after = Pt(2)
            _add_inline(para, row[i] if i < len(row) else "")
            for run in para.runs:
                run.font.size = Pt(9)
    doc.add_paragraph()


def _consume_wrapped(lines: list[str], index: int, first: str) -> tuple[str, int]:
    """Joins a list item's INDENTED continuation lines onto its first line.

    Markdown hard-wraps a long list item and indents the continuation; the
    first renderer here treated each continuation as a fresh paragraph, so
    every multi-line bullet rendered as one short bullet followed by an
    orphaned block of unbulleted prose. Caught by converting the output to
    PDF and looking at it -- nothing in the pipeline would have complained.

    Stops at a blank line, at a less-indented line, or at anything that
    starts a new construct.
    """
    parts = [first]
    index += 1
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            break
        if not raw.startswith((" ", "\t")):
            break
        if stripped.startswith(("- ", "* ", "|", "```", "#")) or re.match(r"^\d+\.\s", stripped):
            break
        parts.append(stripped)
        index += 1
    return " ".join(parts), index


def _split_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _configure_styles(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(8)

    for name, size in (("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 11.5)):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.color.rgb = TEAL
        style.font.bold = True


def _add_cover(doc: Document, title: str) -> None:
    heading = doc.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = heading.add_run(title)
    run.bold = True
    run.font.size = Pt(26)
    run.font.color.rgb = TEAL

    subtitle = doc.add_paragraph()
    run = subtitle.add_run(
        "A comprehension gate for AI-generated code — Chrome extension and Flask backend.\n"
        f"Generated from docs/ARCHITECTURE.md on "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} (UTC)."
    )
    run.font.size = Pt(10.5)
    run.font.color.rgb = MUTED
    doc.add_paragraph()


def render(markdown: str) -> Document:
    doc = Document()
    for section in doc.sections:
        section.left_margin = section.right_margin = Inches(0.9)
        section.top_margin = section.bottom_margin = Inches(0.8)
    _configure_styles(doc)

    lines = markdown.split("\n")
    index = 0
    cover_done = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        # Fenced code block -- consumed whole, so its contents are never
        # interpreted as markdown (the ASCII diagrams contain characters
        # that would otherwise read as list markers).
        if stripped.startswith("```"):
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1  # closing fence
            _add_code_block(doc, block)
            continue

        # Pipe table: a header row, a separator, then body rows.
        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and set(lines[index + 1].strip()) <= set("|-: ")
            and "-" in lines[index + 1]
        ):
            rows = [_split_table_row(stripped)]
            index += 2  # header + separator
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_split_table_row(lines[index]))
                index += 1
            _add_table(doc, rows)
            continue

        if stripped == "---":
            # A horizontal rule between top-level sections. Rendered as a
            # page break: this document is read section by section.
            doc.add_page_break()
            index += 1
            continue

        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[level:].strip()
            if level == 1 and not cover_done:
                _add_cover(doc, text)
                cover_done = True
            else:
                heading = doc.add_heading(level=min(level, 3))
                _add_inline(heading, text)
            index += 1
            continue

        if stripped.startswith(("- ", "* ")):
            text, index = _consume_wrapped(lines, index, stripped[2:])
            para = doc.add_paragraph(style="List Bullet")
            _add_inline(para, text)
            continue

        numbered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if numbered:
            text, index = _consume_wrapped(lines, index, numbered.group(2))
            # The number is written literally rather than using Word's
            # "List Number" style. That style continues numbering from
            # the previous numbered list anywhere in the document, so the
            # two-step list in §4.3 rendered as items 4 and 5 -- carrying
            # on from §1's list of three constraints. Restarting a Word
            # list means hand-editing numbering XML; a literal number
            # with a hanging indent is boring and cannot be wrong.
            para = doc.add_paragraph()
            para.paragraph_format.left_indent = Inches(0.35)
            para.paragraph_format.first_line_indent = Inches(-0.25)
            para.paragraph_format.space_after = Pt(4)
            para.add_run(f"{numbered.group(1)}.  ").bold = True
            _add_inline(para, text)
            continue

        # A plain paragraph: join the wrapped source lines back together,
        # since markdown's hard wrapping is not a line break.
        buffer = [stripped]
        index += 1
        while index < len(lines):
            nxt = lines[index].strip()
            if not nxt or nxt.startswith(("#", "-", "*", "|", "```")) or re.match(r"^\d+\.\s", nxt):
                break
            buffer.append(nxt)
            index += 1
        para = doc.add_paragraph()
        _add_inline(para, " ".join(buffer))

    return doc


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"missing source: {SOURCE}")
    doc = render(SOURCE.read_text(encoding="utf-8"))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
