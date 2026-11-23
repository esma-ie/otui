"""Spreadsheet export in the otui table format, on the hand-made dump."""

import csv
import io
import zipfile

from openpyxl import load_workbook

from otui.export import EXCEL_MAX_ROWS, SHEET_NAME_MAX, sheet_names, write_csv_zip, write_xlsx
from otui.store import load_dump


def test_csv_zip(dump):
    m = load_dump(dump)
    buf = io.BytesIO()
    write_csv_zip(m, buf)
    z = zipfile.ZipFile(buf)
    assert sorted(z.namelist()) == ["NCAP_COST.csv", "PRC.csv", "README.txt"]
    rows = list(csv.reader(io.StringIO(z.read("NCAP_COST.csv").decode())))
    assert rows[0] == ["REG", "ALLYEAR", "PRC", "CUR", "value"]
    assert [r[-1] for r in rows[1:]] == ["10", "EPS", "INF"]
    prc = list(csv.reader(io.StringIO(z.read("PRC.csv").decode())))
    assert prc == [["PRC", "element_text"], ["P1", ""], ["P2", "two"]]


def test_xlsx(dump, tmp_path):
    m = load_dump(dump)
    out = tmp_path / "m.xlsx"
    write_xlsx(m, out)
    wb = load_workbook(out)
    assert wb.sheetnames == ["contents", "NCAP_COST", "PRC"]
    ws = wb["NCAP_COST"]
    data = [[c.value for c in r] for r in ws.iter_rows()]
    assert data[0] == ["REG", "ALLYEAR", "PRC", "CUR", "value"]
    assert data[1] == ["R1", "2005", "P1", "EUR", 10]
    assert [r[-1] for r in data[1:]] == [10, "EPS", "INF"]
    # plain values only: no formulas
    assert not any(
        isinstance(c.value, str) and c.value.startswith("=") for r in ws.iter_rows() for c in r
    )
    toc = [[c.value for c in r] for r in wb["contents"].iter_rows()]
    assert ["NCAP_COST", "NCAP_COST", "parameter", "REG, ALLYEAR, PRC, CUR", 3] == toc[4][:5]


def test_sheet_names_are_unique_and_short():
    long = "X" * 40
    names = sheet_names([long, long[:31] + "Y", "contents", "Contents2"])
    assert all(len(v) <= SHEET_NAME_MAX for v in names.values())
    assert len({v.lower() for v in names.values()}) == 4
    assert names["contents"] != "contents"  # reserved for the contents sheet
    assert EXCEL_MAX_ROWS == 1_048_576
