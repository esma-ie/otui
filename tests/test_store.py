"""Loading a TIMES.jl dump into DuckDB, on a small hand-made dump (no Julia needed)."""

import json
import math

import pytest

from otui.store import headers_for, load_dump


@pytest.mark.parametrize(
    "name, kind, domain, expected",
    [
        (
            "NCAP_COST",
            "parameter",
            ["REG", "ALLYEAR", "PRC", "CUR"],
            ("REG", "ALLYEAR", "PRC", "CUR"),
        ),
        ("PRC", "set", ["*"], ("PRC",)),
        ("COM", "set", ["COM_GRP"], ("COM",)),  # 1-D set: its own name, not its domain
        ("COM_TYPE", "set", ["COM_GRP"], ("COM_TYPE",)),
        ("COV_MAP", "set", ["*", "*"], ("UNI", "UNI_2")),
        (
            "TOP_IRE",
            "set",
            ["ALL_REG", "COM", "ALL_REG", "COM", "PRC"],
            ("ALL_REG", "COM", "ALL_REG_2", "COM_2", "PRC"),
        ),
        ("G_DYEAR", "parameter", [], ()),
        ("X", "parameter", ["*"], ("UNI",)),
    ],
)
def test_headers(name, kind, domain, expected):
    assert headers_for(name, kind, domain) == expected


def test_load(dump):
    m = load_dump(dump)
    assert m.source == "hand-made"
    assert set(m.symbols) == {"PRC", "NCAP_COST", "COM", "VAR_ACT"}
    assert m.tables == ["PRC", "NCAP_COST"]

    cost = m.df("NCAP_COST")
    assert list(cost.columns) == ["REG", "ALLYEAR", "PRC", "CUR", "value", "value__sv"]
    assert cost["value__sv"].tolist() == [0, 1, 0]  # EPS kept explicit
    assert math.isinf(cost["value"].iloc[2])

    prc = m.df("PRC")
    assert list(prc.columns) == ["PRC", "element_text"]
    assert prc["element_text"].tolist() == ["", "two"]

    cat = m.con.execute("SELECT name, headers, records FROM _symbols ORDER BY name").fetchall()
    assert ("VAR_ACT", ["REG", "ALLYEAR", "ALLYEAR_2", "PRC", "ALL_TS"], 0) in cat

    with pytest.raises(KeyError):
        m.table("COM")  # no records


def test_rejects_other_formats(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"format": "other", "format_version": 1}))
    with pytest.raises(ValueError, match="format version 1"):
        load_dump(tmp_path)
