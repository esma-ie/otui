"""Shared test helpers: a small hand-made TIMES.jl dump (Arrow format v1)."""

import json
import math

import pyarrow as pa
import pyarrow.feather as feather
import pytest


def _write(d, name, cols):
    feather.write_feather(pa.table(cols), str(d / f"{name}.arrow"))
    return f"{name}.arrow"


def make_dump(tmp_path):
    syms = [
        {
            "name": "PRC",
            "kind": "set",
            "dimension": 1,
            "domain": ["*"],
            "description": "Processes",
            "records": 2,
            "file": _write(tmp_path, "PRC", {"d1": ["P1", "P2"], "element_text": ["", "two"]}),
        },
        {
            "name": "NCAP_COST",
            "kind": "parameter",
            "dimension": 4,
            "domain": ["REG", "ALLYEAR", "PRC", "CUR"],
            "description": "Investment cost for new capacity",
            "records": 3,
            "file": _write(
                tmp_path,
                "NCAP_COST",
                {
                    "d1": ["R1", "R1", "R1"],
                    "d2": ["2005", "2010", "2020"],
                    "d3": ["P1", "P1", "P2"],
                    "d4": ["EUR", "EUR", "EUR"],
                    "value": [10.0, -0.0, math.inf],
                    "value__sv": pa.array([0, 1, 0], type=pa.int8()),
                },
            ),
        },
        {
            "name": "COM",
            "kind": "set",
            "dimension": 1,
            "domain": ["*"],
            "description": "Commodities",
            "records": 0,
            "file": None,
        },
        {
            "name": "VAR_ACT",
            "kind": "variable",
            "dimension": 5,
            "domain": ["REG", "ALLYEAR", "ALLYEAR", "PRC", "ALL_TS"],
            "description": "Activity",
            "records": 0,
            "file": None,
        },
    ]
    _write(tmp_path, "uels", {"uel": ["R1", "P1", "P2"]})
    manifest = {
        "format": "timesjl-dump",
        "format_version": 1,
        "source": "hand-made",
        "uels": "uels.arrow",
        "symbols": syms,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


@pytest.fixture
def dump(tmp_path):
    return make_dump(tmp_path)
