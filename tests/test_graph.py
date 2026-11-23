"""The RES network and its dead-end flags, on a small hand-made model."""

import duckdb
import pytest

from otui.graph import build
from otui.store import Model, Symbol, headers_for

TABLES = {
    "TOP": (
        "set",
        ["REG", "PRC", "COM", "IN_OUT"],
        [
            ("R1", "MINCOA", "COA", "OUT"),
            ("R1", "ELCCOA", "COA", "IN"),
            ("R1", "ELCCOA", "ELC", "OUT"),
            ("R1", "ELCCOA", "CO2", "OUT"),
            ("R1", "DMDLIT", "ELC", "IN"),
            ("R1", "DMDLIT", "DLIT", "OUT"),
            ("R1", "BADPRC", "HYD", "IN"),  # HYD is used but nothing makes it
            ("R1", "BADPRC", "GEO", "IN"),  # so is GEO, but it has no balance (COM_LIM N)
            ("R1", "BADPRC", "WASTE", "OUT"),  # WASTE is made but nothing uses it
            ("R2", "DMDLIT", "ELC", "IN"),
            ("R2", "DMDLIT", "DLIT", "OUT"),
            ("R1", "IMPNRGZ", "HYD", "OUT"),  # VEDA dummy import: not real supply
            ("R1", "IMPNRGZ", "SPARE", "OUT"),
        ],
    ),
    "TOP_IRE": (
        "set",
        ["ALL_REG", "COM", "ALL_REG", "COM", "PRC"],
        [
            ("R1", "ELC", "R2", "ELC", "TRDELC"),
            ("IMPEXP", "GAS", "R1", "GAS", "IMPGAS"),
        ],
    ),
    "PRC": (
        "set",
        ["*"],
        [
            ("MINCOA",),
            ("ELCCOA",),
            ("DMDLIT",),
            ("BADPRC",),
            ("TRDELC",),
            ("IMPGAS",),
            ("ORPHAN",),
            ("IMPNRGZ",),
            ("IMPMATZ",),  # a dummy import with no TOP: not "disconnected"
        ],
    ),
    "COM": (
        "set",
        ["*"],
        [("COA",), ("ELC",), ("CO2",), ("DLIT",), ("HYD",), ("WASTE",), ("GAS",), ("SPARE",)],
    ),
    "VDA_EMCB": (
        "parameter",
        ["REG", "ALLYEAR", "COM", "COM"],
        [("R1", "2020", "COA", "CO2X")],
    ),
    "COM_AGG": (
        "parameter",
        ["REG", "ALLYEAR", "COM", "COM"],
        [("R1", "2020", "CO2X", "TOTCO2")],
    ),
    "FLO_EMIS": (
        "parameter",
        ["REG", "ALLYEAR", "PRC", "COM_GRP", "COM", "ALL_TS"],
        [("R1", "2020", "ELCCOA", "COA", "NOX", "ANNUAL")],
    ),
    "COM_LIM": ("set", ["REG", "COM", "LIM"], [("R1", "GEO", "N"), ("R1", "ELC", "UP")]),
    "COM_TMAP": (
        "set",
        ["REG", "COM_TYPE", "COM"],
        [
            ("R1", "DEM", "DLIT"),
            ("R2", "DEM", "DLIT"),
            ("R1", "ENV", "CO2"),
            ("R1", "ENV", "TOTCO2"),
            ("R1", "ENV", "NOX"),
            ("R1", "NRG", "ELC"),
        ],
    ),
}


@pytest.fixture
def model():
    con = duckdb.connect()
    syms = {}
    for name, (kind, domain, rows) in TABLES.items():
        h = headers_for(name, kind, domain)
        cols = ", ".join(f'"{c}" VARCHAR' for c in h)
        con.execute(f'CREATE TABLE "{name}" ({cols})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" * len(h))})', rows)
        syms[name] = Symbol(name, kind, tuple(domain), h, "", len(rows), f"{name}.arrow")
    return Model(con=con, symbols=syms)


def flags(g, kind, name):
    return set(g.nodes[f"{kind[0]}:{name}"].flags)


def test_links_and_flags(model):
    g = build(model)
    assert ("c:COA", "p:ELCCOA") in g.links and ("p:ELCCOA", "c:ELC") in g.links
    assert g.links[("p:DMDLIT", "c:DLIT")].regions == {"R1", "R2"}
    # trade: ELC in R1 -> TRDELC -> ELC in R2; IMPEXP adds no node
    assert g.links[("c:ELC", "p:TRDELC")].kind == "trade"
    assert ("p:IMPGAS", "c:GAS") in g.links
    assert "c:IMPEXP" not in g.nodes

    assert flags(g, "commodity", "HYD") == {"no_supply"}
    assert flags(g, "commodity", "WASTE") == {"no_use"}
    assert flags(g, "commodity", "DLIT") == set()  # a demand is an expected end point
    assert flags(g, "commodity", "CO2") == set()  # so is an emission
    assert flags(g, "commodity", "SPARE") == {"unused"}
    assert flags(g, "process", "ORPHAN") == {"disconnected"}
    assert g.nodes["c:DLIT"].types == {"DEM"}
    assert flags(g, "commodity", "GAS") == {"no_use"}  # imported, never used
    assert g.public()["flags"] == {"no_supply": 1, "no_use": 2, "unused": 1, "disconnected": 1}


def test_emission_and_aggregation_links(model):
    g = build(model)
    assert g.links[("c:COA", "c:CO2X")].kind == "emission"
    assert g.links[("c:CO2X", "c:TOTCO2")].kind == "aggregation"
    assert flags(g, "commodity", "TOTCO2") == set()  # an ENV total: expected end point


def test_process_emissions(model):
    g = build(model)
    assert g.links[("p:ELCCOA", "c:NOX")].kind == "emission"  # FLO_EMIS
    assert flags(g, "commodity", "NOX") == set()


def test_dummy_imports_left_out(model):
    g = build(model)
    assert g.dummies == ["IMPMATZ", "IMPNRGZ"]
    assert "p:IMPNRGZ" not in g.nodes and "p:IMPMATZ" not in g.nodes
    assert flags(g, "commodity", "HYD") == {"no_supply"}  # a dummy import is no supply
    assert flags(g, "commodity", "SPARE") == {"unused"}
    assert flags(g, "commodity", "GEO") == set()  # COM_LIM N: TIMES writes no balance
    assert g.public()["dummies"] == ["IMPMATZ", "IMPNRGZ"]


def test_ego(model):
    g = build(model)
    assert g.ego("p:ELCCOA", 1) == {"p:ELCCOA", "c:COA", "c:ELC", "c:CO2", "c:NOX"}
    two = g.ego("p:ELCCOA", 2)
    assert {"p:MINCOA", "p:DMDLIT", "p:TRDELC"} <= two
    sub = g.public(keep=g.ego("p:ELCCOA", 1))
    assert {n["id"] for n in sub["nodes"]} == {"p:ELCCOA", "c:COA", "c:ELC", "c:CO2", "c:NOX"}
    assert all(lk["source"] in sub_ids(sub) and lk["target"] in sub_ids(sub) for lk in sub["links"])


def sub_ids(sub):
    return {n["id"] for n in sub["nodes"]}


def test_region_filter(model):
    g = build(model, region="R2")
    assert "p:MINCOA" not in g.nodes
    assert not any("disconnected" in n.flags for n in g.nodes.values())
    assert ("p:DMDLIT", "c:DLIT") in g.links
    assert ("p:TRDELC", "c:ELC") in g.links  # imported into R2


def test_find(model):
    g = build(model)
    names = [h["name"] for h in g.find("elc")]
    assert names[0] == "ELC"  # exact prefix and shortest first
    assert "ELCCOA" in names and "TRDELC" in names


def test_categories_rank_sectors(model):
    c = build(model).categories()
    assert dict(c["sectors"]) == {"M": 1, "E": 1, "D": 1, "B": 1, "T": 1, "I": 1, "O": 1}
    assert c["sectors"][0] == ("B", 1)  # ties alphabetical


def test_reach_counts_nodes_by_links(model):
    g = build(model)
    r = g.reach("p:ELCCOA")
    assert r[:2] == [1, 5]  # itself; COA, ELC, CO2, NOX
    assert r == sorted(r) and len(set(r)) == len(r)  # grows each step, ends when complete
    ego = g.ego("p:ELCCOA", len(r) - 1)
    assert len(ego) == r[-1]  # the whole connected part
    assert g.reach("p:ORPHAN") == [1]


def test_centre_is_most_central(model):
    g = build(model)
    c = g.centre()
    assert c in g.nodes and "ENV" not in g.nodes[c].types  # an emission hub is no place to start
    ecc = len(g.reach(c)) - 1
    part = [n for n in g.adjacency() if g.reach(n)[-1] == g.reach(c)[-1]]
    assert all(len(g.reach(n)) - 1 >= ecc for n in part if "ENV" not in g.nodes[n].types)
