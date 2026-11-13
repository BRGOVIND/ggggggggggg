import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SOURCE = Path("writeup.md")  # Keep the PDF tied to the reviewed text.
OUTPUT = Path("writeup.pdf")  # Submit the paginated copy.
PAGE = landscape(A4)  # Wide required tables remain readable.
MARGIN = 25  # Reserve space for page numbers.
BODY_SIZE = 8.4  # Keep the two-page write-up legible.
TABLE_SIZE = 7.0  # Eight-column results table needs compact type.


def inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", escaped)
    return escaped


def paragraph_style(
    name: str, size: float, leading: float, before: float, after: float
):
    return ParagraphStyle(
        name,
        fontName="Helvetica",
        fontSize=size,
        leading=leading,
        spaceBefore=before,
        spaceAfter=after,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#182230"),
    )


def table_widths(rows: list[list[str]], available: float) -> list[float]:
    count = len(rows[0])
    if count == 4:
        weights = [0.18, 0.25, 0.39, 0.18]
    elif count == 7:
        weights = [0.17, 0.13, 0.06, 0.10, 0.09, 0.225, 0.225]
    else:
        weights = [0.20, 0.105, 0.10, 0.11, 0.105, 0.10, 0.14, 0.14]
    if count == 8 and rows[0][0].startswith("Setting"):
        weights = [0.25, 0.07, 0.10, 0.08, 0.11, 0.10, 0.145, 0.145]
    scale = sum(weights)
    return [available * weight / scale for weight in weights]


def make_table(lines: list[str], style: ParagraphStyle, available: float) -> Table:
    rows = [
        [part.strip() for part in line.strip().strip("|").split("|")] for line in lines
    ]
    rows = [rows[0], *rows[2:]]
    widths = table_widths(rows, available)
    cells = [[Paragraph(inline(part), style) for part in row] for row in rows]
    table = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DE")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ]
        )
    )
    return table


def blocks(markdown: str):
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("# "):
            yield "heading", line[2:]
            index += 1
            continue
        if line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            yield "table", table_lines
            continue
        paragraph = []
        while (
            index < len(lines)
            and lines[index].strip()
            and not lines[index].startswith(("# ", "|"))
        ):
            paragraph.append(lines[index].strip())
            index += 1
        yield "body", " ".join(paragraph)


def footer(canvas, document):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#607080"))
    canvas.drawRightString(PAGE[0] - MARGIN, 12, f"{document.page} / 2")
    canvas.restoreState()


def main() -> None:
    heading = paragraph_style("Heading", 10.2, 11.4, 4.2, 1.7)
    body = paragraph_style("Body", BODY_SIZE, 9.5, 0, 2.5)
    label = paragraph_style("Label", 8.0, 9.0, 2.4, 1.3)
    table_style = paragraph_style("Cell", TABLE_SIZE, 8.0, 0, 0)
    available = PAGE[0] - 2 * MARGIN
    story = []
    for kind, value in blocks(SOURCE.read_text(encoding="utf-8")):
        if kind == "heading":
            if value.startswith("4 Where the time goes"):
                story.append(PageBreak())
            story.append(Paragraph(inline(value), heading))
        elif kind == "table":
            story.append(make_table(value, table_style, available))
            story.append(Spacer(1, 2))
        elif value.startswith("**Table "):
            story.append(Paragraph(inline(value), label))
        else:
            story.append(Paragraph(inline(value), body))
    document = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=19,
        bottomMargin=20,
        title="The Relay",
        author="",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    main()
