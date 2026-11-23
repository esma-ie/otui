"""VEDA's dummy imports: which a model has, and how much the solution takes from them.

A dummy import (``IMPNRGZ``, ``IMPMATZ``, ``IMPDEMZ``, ..., see ``graph.DUMMY``) can supply
any commodity of its type at a high cost, so that a model stays feasible when it could not
otherwise meet a demand or balance. In a sound model the solution uses none. Any use means
the model is short of real supply somewhere.

The amounts come from the solve (``dummy_flows`` in TIMES.jl's run result, see
``julia/runchain.jl``): the annual flow of each dummy import into each commodity, region and
period, and the period's length ``D(T)``. Over the model horizon a flow counts
``annual * D(T)``, in the commodity's own unit from ``COM_UNIT(REG, COM, UNITS_COM)``.

Every TIMES model has dummy imports available, and a solver may leave tiny amounts in them. So
they matter only at a scale that compares with demand (within 3-5 orders of
magnitude). otui compares, unit by unit, the largest annual dummy amount in any period with the
model's annual demand in that unit (the demand commodities' ``COM_PROJ``, summed over regions,
averaged over the years given), and calls them significant at ``SIGNIFICANT`` (1e-4) or more.
With no demand in a unit to compare against, anything above ``NOISE`` counts.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from otui.graph import Graph, _rows
from otui.store import Model

ENERGY = "NRG"
SIGNIFICANT = 1e-4  # dummy / demand from which dummy imports are highlighted
NOISE = 1e-6  # absolute amount below which a dummy flow is solver noise


def _units(m: Model) -> dict[str, str]:
    """``COM_UNIT``: each commodity's unit (first region wins)."""
    out: dict[str, str] = {}
    for _reg, com, unit in _rows(m, "COM_UNIT", ["REG", "COM", "UNITS_COM"]):
        out.setdefault(com, unit)
    return out


def _types(m: Model) -> dict[str, str]:
    out: dict[str, str] = {}
    for _reg, typ, com in _rows(m, "COM_TMAP", ["REG", "COM_TYPE", "COM"]):
        out.setdefault(com, typ)
    return out


def _demand(m: Model, units: dict[str, str], types: dict[str, str]) -> dict[str, float]:
    """Annual demand per unit: ``COM_PROJ`` of the DEM commodities, by year, averaged."""
    by_year: dict[tuple[str, str], float] = defaultdict(float)
    for _reg, year, com, value in _rows(m, "COM_PROJ", ["REG", "ALLYEAR", "COM", "value"]):
        if types.get(com) == "DEM" and isinstance(value, (int, float)):
            by_year[(units.get(com, ""), year)] += float(value)
    per_unit: dict[str, list[float]] = defaultdict(list)
    for (unit, _year), v in by_year.items():
        per_unit[unit].append(v)
    return {u: sum(vs) / len(vs) for u, vs in per_unit.items() if vs}


def summary(m: Model, g: Graph, result: dict[str, Any] | None) -> dict[str, Any]:
    """The dummy imports a model has and, once solved, how much the solution takes from them.

    ``totals`` holds one entry per commodity type and unit (energy first), summed over the
    horizon; ``commodities`` the largest users; ``periods`` the annual amounts per period.
    """
    out: dict[str, Any] = {"processes": g.dummies, "solved": False}
    flows = (result or {}).get("dummy_flows")
    if not g.dummies or flows is None:
        return out
    units, types = _units(m), _types(m)
    by_com: dict[str, float] = defaultdict(float)
    by_period: dict[tuple[str, str, str], float] = defaultdict(float)
    periods = set()
    for f in flows:
        com = f["commodity"]
        by_com[com] += f["annual"] * f["years"]
        by_period[(f["period"], types.get(com, ""), units.get(com, ""))] += f["annual"]
        periods.add(f["period"])
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for com, amount in by_com.items():
        key = (types.get(com, ""), units.get(com, ""))
        t = totals.setdefault(key, {"type": key[0], "unit": key[1], "total": 0.0, "commodities": 0})
        t["total"] += amount
        t["commodities"] += 1
    ranked = sorted(by_com.items(), key=lambda kv: -abs(kv[1]))
    demand = _demand(m, units, types)
    worst: dict[str, float] = defaultdict(float)  # largest annual dummy amount in a period, by unit
    per_period_unit: dict[tuple[str, str], float] = defaultdict(float)
    for (p, _typ, u), v in by_period.items():
        per_period_unit[(p, u)] += v
    for (_p, u), v in per_period_unit.items():
        worst[u] = max(worst[u], abs(v))
    scale = []
    for u, v in sorted(worst.items(), key=lambda kv: -kv[1]):
        d = demand.get(u)
        ratio = v / d if d else None
        big = (ratio >= SIGNIFICANT) if ratio is not None else v >= NOISE
        scale.append({"unit": u, "annual": v, "demand": d, "ratio": ratio, "significant": big})
    out.update(
        scale=scale,
        significant=any(s["significant"] for s in scale),
        threshold=SIGNIFICANT,
        solved=True,
        used=bool(flows),
        suppliers=sorted({f["process"] for f in flows}),
        totals=sorted(totals.values(), key=lambda t: (t["type"] != ENERGY, -abs(t["total"]))),
        commodities=[
            {"commodity": c, "type": types.get(c, ""), "unit": units.get(c, ""), "total": v}
            for c, v in ranked[:20]
        ],
        periods=[
            {"period": p, "type": typ, "unit": u, "annual": v}
            for (p, typ, u), v in sorted(by_period.items())
        ],
        horizon=[min(periods), max(periods)] if periods else [],
    )
    return out


def issues(s: dict[str, Any]) -> list[dict[str, str]]:
    """QC issues: an ERROR per commodity supplied at a significant scale, else one INFO line."""
    if not s.get("used"):
        return []
    span = "-".join(dict.fromkeys(s["horizon"]))
    big = {x["unit"] for x in s["scale"] if x["significant"]}
    out = [
        {
            "severity": "ERROR",
            "check": "Dummy import used in the solution",
            "message": f"{c['commodity']}: {c['total']:,.6g} {c['unit']} over {span}".strip(),
            "item": f"c:{c['commodity']}",
        }
        for c in s["commodities"]
        if c["unit"] in big
    ]
    small = [x for x in s["scale"] if not x["significant"]]
    if small:
        worst = max(small, key=lambda x: x["ratio"] or 0)
        out.append(
            {
                "severity": "INFO",
                "check": "Dummy import used, negligibly",
                "message": f"at most {worst['annual']:.3g} {worst['unit']} a year, "
                f"{worst['ratio']:.1g} of demand in {worst['unit']}"
                if worst["ratio"] is not None
                else f"at most {worst['annual']:.3g} {worst['unit']} a year",
                "item": "",
            }
        )
    return out
