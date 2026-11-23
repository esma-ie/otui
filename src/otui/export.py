"""Export a loaded model as spreadsheets: a CSV zip or an .xlsx workbook.

Both follow the otui table format (``docs/table-format.md``): one table per set
or parameter with records, named by its TIMES code, headed by its TIMES
domain names, then ``value`` (parameters) or ``element_text`` (sets, only
when some element has text). Special values are written as text: ``EPS``,
``NA``, ``UNDF``, ``INF``, ``-INF``. Plain values only: no formulas, no
formatting beyond a bold header, so files re-open cleanly in Excel and
LibreOffice and can be read back by otui later.

The workbook starts with a ``contents`` sheet listing every table, its
description and record count. Excel limits sheet names to 31 characters and
sheets to 1,048,576 rows; a longer name is shortened (the contents sheet
gives the full name) and a larger table is written to CSV only, with a note.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from collections.abc import Iterator
from pathlib import Path

from otui import __version__
from otui.store import SPECIAL_VALUES, Model, Symbol, _q

EXCEL_MAX_ROWS = 1_048_576
SHEET_NAME_MAX = 31
CONTENTS = "contents"


def _value(v, sv) -> object:
    if sv:
        return SPECIAL_VALUES.get(int(sv), "NA")
    if isinstance(v, float):
        if math.isinf(v):
            return "INF" if v > 0 else "-INF"
        if math.isnan(v):
            return "NA"
        if v == int(v) and abs(v) < 1e15:
            return int(v)  # 2005.0 -> 2005; -0.0 (not EPS) -> 0
    return v


def columns(m: Model, s: Symbol) -> list[str]:
    cols = list(s.headers)
    if s.kind == "parameter":
        cols.append("value")
    elif _has_text(m, s):
        cols.append("element_text")
    return cols


def _has_text(m: Model, s: Symbol) -> bool:
    r = m.con.cursor().execute(
        f"SELECT count(*) FROM {_q(s.name)} WHERE coalesce(element_text, '') <> ''"
    )
    return r.fetchone()[0] > 0


def rows(m: Model, s: Symbol) -> Iterator[list]:
    """A symbol's rows as plain values, in the reader's order."""
    idx = ", ".join(f'_t."{h}"' for h in s.headers)
    if s.kind == "parameter":
        sel = (idx + ", " if idx else "") + "_t.value, _t.value__sv"
    else:
        sel = (idx + ", " if idx else "") + "_t.element_text"
    text = s.kind != "parameter" and _has_text(m, s)
    cur = m.con.cursor().execute(f"SELECT {sel} FROM {_q(s.name)} AS _t")
    n = len(s.headers)
    while batch := cur.fetchmany(10_000):
        for r in batch:
            if s.kind == "parameter":
                yield [*r[:n], _value(r[n], r[n + 1])]
            else:
                yield [*r[:n], r[n]] if text else list(r[:n])


def write_csv_zip(m: Model, out: Path | io.BytesIO) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", _readme(m))
        for name in sorted(m.tables):
            s = m.symbols[name]
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(columns(m, s))
            w.writerows(rows(m, s))
            z.writestr(f"{name}.csv", buf.getvalue())


def sheet_names(names: list[str]) -> dict[str, str]:
    """Unique Excel sheet names (31 characters at most) for TIMES symbol names."""
    out, used = {}, {CONTENTS}
    for n in names:
        base = n[:SHEET_NAME_MAX]
        cand, i = base, 2
        while cand.lower() in used:
            suffix = f"~{i}"
            cand = base[: SHEET_NAME_MAX - len(suffix)] + suffix
            i += 1
        used.add(cand.lower())
        out[n] = cand
    return out


def write_xlsx(m: Model, out: Path | io.BytesIO) -> None:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    bold = Font(bold=True)
    wb = Workbook(write_only=True)
    names = sorted(m.tables)
    sheets = sheet_names(names)

    toc = wb.create_sheet(CONTENTS)
    toc.append([f"Model: {m.source}"])
    toc.append([f"Written by otui {__version__}; otui table format; special values as text"])
    toc.append([])
    hdr = []
    for h in ("Symbol", "Sheet", "Kind", "Headers", "Records", "Description", "Note"):
        c = WriteOnlyCell(toc, value=h)
        c.font = bold
        hdr.append(c)
    toc.append(hdr)
    for n in names:
        s = m.symbols[n]
        note = "too many rows for Excel: see the CSV export" if s.records >= EXCEL_MAX_ROWS else ""
        sheet = "" if note else sheets[n]
        toc.append([n, sheet, s.kind, ", ".join(s.headers), s.records, s.description, note])

    for n in names:
        s = m.symbols[n]
        if s.records >= EXCEL_MAX_ROWS:
            continue
        ws = wb.create_sheet(sheets[n])
        head = []
        for h in columns(m, s):
            c = WriteOnlyCell(ws, value=h)
            c.font = bold
            head.append(c)
        ws.append(head)
        for r in rows(m, s):
            ws.append(r)
    wb.save(out)


def _readme(m: Model) -> str:
    return (
        f"Model: {m.source}\n"
        f"Written by otui {__version__}.\n\n"
        "One CSV file per TIMES set or parameter with records, named by its TIMES code.\n"
        "Columns: the symbol's TIMES domain names, then `value` (parameters) or\n"
        "`element_text` (sets with element text). Special values are text: EPS, NA,\n"
        "UNDF, INF, -INF.\n"
    )
