"""Dummy imports: the summary of what a solution takes from them, and the QC issues."""

import duckdb
import pytest

from otui import dummies
from otui.graph import build
from otui.store import Model, Symbol, headers_for

TABLES = {
    "TOP": ("set", ["REG", "PRC", "COM", "IN_OUT"], [("R1", "DMDLIT", "ELC", "IN")]),
    "TOP_IRE": (
        "set",
        ["ALL_REG", "COM", "ALL_REG", "COM", "PRC"],
        [("IMPEXP", "ELC", "R1", "ELC", "IMPNRGZ"), ("IMPEXP", "DLIT", "R1", "DLIT", "IMPDEMZ")],
    ),
    "COM_UNIT": (
        "set",
        ["REG", "COM", "UNITS_COM"],
        [("R1", "ELC", "PJ"), ("R1", "GAS", "PJ"), ("R1", "DLIT", "Bv-km"), ("R1", "DHEAT", "PJ")],
    ),
    "COM_PROJ": (
        "parameter",
        ["REG", "ALLYEAR", "COM"],
        [
            ("R1", "2020", "DLIT", 100.0),
            ("R1", "2025", "DLIT", 100.0),
            ("R1", "2020", "DHEAT", 1000.0),
        ],
    ),
    "COM_TMAP": (
        "set",
        ["REG", "COM_TYPE", "COM"],
        [("R1", "NRG", "ELC"), ("R1", "NRG", "GAS"), ("R1", "DEM", "DLIT"), ("R1", "DEM", "DHEAT")],
    ),
}

FLOWS = [  # annual flow, period length
    {
        "region": "R1",
        "period": "2020",
        "process": "IMPNRGZ",
        "commodity": "ELC",
        "annual": 2.0,
        "years": 5,
    },
    {
        "region": "R1",
        "period": "2025",
        "process": "IMPNRGZ",
        "commodity": "ELC",
        "annual": 1.0,
        "years": 5,
    },
    {
        "region": "R1",
        "period": "2025",
        "process": "IMPNRGZ",
        "commodity": "GAS",
        "annual": 4.0,
        "years": 5,
    },
    {
        "region": "R1",
        "period": "2025",
        "process": "IMPDEMZ",
        "commodity": "DLIT",
        "annual": 0.5,
        "years": 5,
    },
]


@pytest.fixture
def model():
    con = duckdb.connect()
    syms = {}
    for name, (kind, domain, rows) in TABLES.items():
        h = headers_for(name, kind, domain)
        value = len(rows[0]) > len(h)  # a parameter with values: the loader's "value" column
        cols = ", ".join([f'"{c}" VARCHAR' for c in h] + (['"value" DOUBLE'] if value else []))
        con.execute(f'CREATE TABLE "{name}" ({cols})')
        n = len(h) + value
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * n)})', rows)
        syms[name] = Symbol(name, kind, tuple(domain), h, "", len(rows), f"{name}.arrow")
    return Model(con=con, symbols=syms)


def test_before_a_solve(model):
    s = dummies.summary(model, build(model), None)
    assert s == {"processes": ["IMPDEMZ", "IMPNRGZ"], "solved": False}
    assert dummies.issues(s) == []


def test_amounts_over_the_horizon(model):
    s = dummies.summary(model, build(model), {"status": "optimal", "dummy_flows": FLOWS})
    assert s["solved"] and s["used"] and s["horizon"] == ["2020", "2025"]
    assert s["suppliers"] == ["IMPDEMZ", "IMPNRGZ"]
    energy, demand = s["totals"]  # energy first
    assert energy == {"type": "NRG", "unit": "PJ", "total": 35.0, "commodities": 2}  # (2+1)*5 + 4*5
    assert demand == {"type": "DEM", "unit": "Bv-km", "total": 2.5, "commodities": 1}
    assert [c["commodity"] for c in s["commodities"]] == ["GAS", "ELC", "DLIT"]
    assert {"period": "2025", "type": "NRG", "unit": "PJ", "annual": 5.0} in s["periods"]
    # largest annual amount (2025: 1 + 4 PJ) against 1,000 PJ of demand a year: significant
    assert s["significant"] and s["threshold"] == 1e-4
    assert {
        "unit": "PJ",
        "annual": 5.0,
        "demand": 1000.0,
        "ratio": 0.005,
        "significant": True,
    } in s["scale"]
    qc = dummies.issues(s)
    assert len(qc) == 3 and qc[0]["severity"] == "ERROR" and qc[0]["item"] == "c:GAS"
    assert qc[0]["message"] == "GAS: 20 PJ over 2020-2025"


def test_negligible_amounts_are_not_highlighted(model):
    tiny = [dict(f, annual=f["annual"] * 1e-6) for f in FLOWS]  # 5e-6 PJ a year: 5e-9 of demand
    s = dummies.summary(model, build(model), {"status": "optimal", "dummy_flows": tiny})
    assert s["used"] and not s["significant"]
    qc = dummies.issues(s)
    assert [i["severity"] for i in qc] == ["INFO"]
    assert qc[0]["check"] == "Dummy import used, negligibly"


def test_a_sound_solution_uses_none(model):
    s = dummies.summary(model, build(model), {"status": "optimal", "dummy_flows": []})
    assert s["solved"] and not s["used"] and not s["significant"] and s["totals"] == []
    assert dummies.issues(s) == []
