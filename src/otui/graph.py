"""The RES (reference energy system) network of a TIMES model.

Built from TIMES's own sets and parameters, as loaded by ``store.load_dump``.
Links have a kind:

- ``flow``: ``TOP(REG, PRC, COM, IN_OUT)``: commodity -> process for IN,
  process -> commodity for OUT, in each region.
- ``trade``: ``TOP_IRE(ALL_REG, COM, ALL_REG_2, COM_2, PRC)``: a trade
  process takes ``COM`` in ``ALL_REG`` and delivers ``COM_2`` in
  ``ALL_REG_2``. ``IMPEXP`` is TIMES's pseudo-region for the world outside
  the model, so it adds no node.
- ``emission``: ``VDA_EMCB(REG, ALLYEAR, COM, COM_2)``: using fuel ``COM``
  emits ``COM_2`` (commodity -> commodity); ``FLO_EMIS`` and ``FLO_EFF``
  ``(REG, ALLYEAR, PRC, COM_GRP, COM, ALL_TS)``: process ``PRC`` emits ``COM``
  (process -> commodity). TIMES adds these to ``TOP`` itself when it
  preprocesses, so the model's own ``TOP`` does not have them.
- ``aggregation``: ``COM_AGG(REG, ALLYEAR, COM, COM_2)``: ``COM`` counts
  into ``COM_2`` (e.g. sector CO2 into total CO2).

``COM_TMAP(REG, COM_TYPE, COM)`` gives each commodity's type (DEM, NRG, MAT,
ENV) and ``PRC_MAP(REG, PRC_GRP, PRC)`` each process's groups;
``COM_DESC``/``PRC_DESC`` their descriptions, where the model has them.

VEDA's dummy import processes (``IMPNRGZ``, ``IMPMATZ``, ``IMPDEMZ``,
``IMPDUCZ``..., named ``IMP???Z``) can supply every commodity of their type at
a high cost, to keep a model feasible. They are not part of the energy system:
they are left out of the network (so they neither count as supply nor join
everything to everything) and listed in ``Graph.dummies``.

Node ids carry their kind (``p:NAME``, ``c:NAME``). By default regions are
merged: a link exists if it exists in any region, and lists those regions.

Flags (visual validation, alongside QC):
- ``no_supply`` (dead end): a commodity that is used but nothing produces;
- ``no_use`` (dead end): a commodity produced but never used, unless it is a
  demand (DEM) or emission (ENV) commodity, where that is expected;
- ``unused``: a commodity declared in ``COM`` with no link at all (common in
  VEDA models, where only a dummy import touches it);
- ``disconnected``: a process declared in ``PRC`` with no link at all.
TIMES's own reserved commodity ``ACT`` is never flagged, nor is a commodity with
``COM_LIM(REG, COM, 'N')``: TIMES writes no balance for it, so it needs no
supply and no use.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from otui.store import Model

OUTSIDE = "IMPEXP"
SINK_TYPES = {"DEM", "ENV"}
RESERVED = {"ACT"}  # commodity group TIMES itself defines
DUMMY = re.compile(r"^IMP[A-Z]{3}Z$")  # VEDA's dummy imports: IMPNRGZ, IMPMATZ, ...
FLAGS = ("no_supply", "no_use", "unused", "disconnected")
MAX_NODES = 5000


def nid(kind: str, name: str) -> str:
    return f"{'p' if kind == 'process' else 'c'}:{name}"


@dataclass
class Node:
    id: str
    name: str
    kind: str  # process, commodity
    description: str = ""
    regions: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)  # COM_TMAP types or PRC_MAP groups
    flags: set[str] = field(default_factory=set)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "regions": sorted(self.regions),
            "types": sorted(self.types),
            "flags": sorted(self.flags),
        }


@dataclass
class Link:
    source: str
    target: str
    kind: str = "flow"  # flow, trade, emission, aggregation
    regions: set[str] = field(default_factory=set)

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "regions": sorted(self.regions),
        }


@dataclass
class Graph:
    nodes: dict[str, Node]
    links: dict[tuple[str, str], Link]
    dummies: list[str] = field(default_factory=list)  # VEDA dummy imports, left out
    _adj: dict[str, set[str]] | None = field(default=None, init=False, repr=False)
    _centre: tuple[str | None] | None = field(default=None, init=False, repr=False)

    def adjacency(self) -> dict[str, set[str]]:
        if self._adj is None:
            adj: dict[str, set[str]] = defaultdict(set)
            for s, t in self.links:
                adj[s].add(t)
                adj[t].add(s)
            self._adj = adj
        return self._adj

    def categories(self) -> dict[str, list[tuple[str, int]]]:
        """Processes by sector (the first letter of the code) and by first type, most first.

        The front end gives each a shape and colour by rank; ranking over the whole network
        (not the part on screen) keeps them the same in every view of a model.
        """
        sectors: dict[str, int] = defaultdict(int)
        groups: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            if n.kind == "process":
                sectors[n.name[:1].upper() or "?"] += 1
                groups[min(n.types) if n.types else "none"] += 1

        def rank(d: dict[str, int]) -> list[tuple[str, int]]:
            return sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))

        return {"sectors": rank(sectors), "groups": rank(groups)}

    def reach(self, focus: str) -> list[int]:
        """Nodes within 0, 1, 2, ... links of ``focus`` (either direction), cumulative.

        The list ends where the focus's connected part of the network is complete, so
        ``len(reach) - 1`` is the most links any connected node is away from it.
        """
        adj = self.adjacency()
        seen, frontier, out = {focus}, [focus], [1]
        while frontier:
            nxt = [m for n in frontier for m in adj.get(n, ()) if m not in seen and not seen.add(m)]
            if not nxt:
                break
            out.append(len(seen))
            frontier = nxt
        return out

    def centre(self) -> str | None:
        """The most central node of the largest connected part of the network.

        Exactly, the node with the smallest eccentricity (most links to its farthest node);
        approximated by trying the 50 best-connected nodes of that part, which is quick for
        large models. Ties go to the better-connected node. Emission (ENV) commodities are not
        candidates: every emitting process links to them, which makes them hubs (in otcgen,
        residential methane) but poor places to start reading a model.
        """
        if self._centre is None:
            adj = self.adjacency()
            seen: set[str] = set()
            best: list[str] = []
            for n in sorted(adj, key=lambda n: (-len(adj[n]), n)):
                if n in seen:
                    continue
                part, q = {n}, [n]
                while q:
                    q = [m for x in q for m in adj[x] if m not in part and not part.add(m)]
                seen |= part
                if len(part) > len(best):
                    best = sorted(part)
            pool = [n for n in best if "ENV" not in self.nodes[n].types] or best
            top = sorted(pool, key=lambda n: (-len(adj[n]), n))[:50]
            pick = min(top, key=lambda n: (len(self.reach(n)), -len(adj[n]), n)) if top else None
            self._centre = (pick,)
        return self._centre[0]

    def public(self, keep: set[str] | None = None) -> dict[str, Any]:
        nodes = [n for n in self.nodes.values() if keep is None or n.id in keep]
        links = [
            lk for (s, t), lk in self.links.items() if keep is None or (s in keep and t in keep)
        ]
        return {
            "nodes": [n.public() for n in sorted(nodes, key=lambda n: n.id)],
            "links": [lk.public() for lk in links],
            "flags": flag_counts(nodes),
            "dummies": self.dummies,
        }

    def ego(self, focus: str, depth: int = 2) -> set[str]:
        """Node ids within ``depth`` links of ``focus`` (either direction)."""
        if focus not in self.nodes:
            raise KeyError(focus)
        adj = self.adjacency()
        seen = {focus}
        q = deque([(focus, 0)])
        while q:
            n, d = q.popleft()
            if d == depth:
                continue
            for m in adj.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    q.append((m, d + 1))
        return seen

    def find(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        """Nodes whose name or description contains ``text``; name-prefix and short names first."""
        t = text.upper()
        hits = [n for n in self.nodes.values() if t in n.name.upper() or t in n.description.upper()]
        hits.sort(
            key=lambda n: (
                t not in n.name.upper(),
                not n.name.upper().startswith(t),
                len(n.name),
                n.name,
            )
        )
        return [
            {"id": n.id, "name": n.name, "kind": n.kind, "description": n.description}
            for n in hits[:limit]
        ]


def flag_counts(nodes) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for n in nodes:
        for f in n.flags:
            out[f] += 1
    return dict(out)


def _rows(m: Model, name: str, cols: list[str] | None = None) -> list[tuple]:
    """Distinct rows of a symbol's columns (``cols`` by header, default: its index columns).

    Columns are always qualified by the table: in DuckDB a bare name equal to the
    table's own name (``COM`` in table ``COM``) would mean the whole row.
    """
    if name not in m.symbols or not m.symbols[name].has_table:
        return []
    cols = list(cols or m.symbols[name].headers)
    sel = ", ".join(f'_t."{c}"' for c in cols)
    return m.con.cursor().execute(f'SELECT DISTINCT {sel} FROM "{name}" AS _t').fetchall()


def _descriptions(m: Model, name: str) -> dict[str, str]:
    """``COM_DESC(REG, COM)`` / ``PRC_DESC(REG, PRC)``: element text, first region wins."""
    if name not in m.symbols or not m.symbols[name].has_table:
        return {}
    h = m.symbols[name].headers
    out: dict[str, str] = {}
    for key, text in _rows(m, name, [h[-1], "element_text"]):
        if text and key not in out:
            out[key] = text
    return out


def build(m: Model, region: str | None = None) -> Graph:
    """The RES network; with ``region``, only that region's links."""
    nodes: dict[str, Node] = {}
    links: dict[tuple[str, str], Link] = {}

    def node(kind: str, name: str) -> Node:
        i = nid(kind, name)
        if i not in nodes:
            nodes[i] = Node(id=i, name=name, kind=kind)
        return nodes[i]

    def link(a: Node, b: Node, reg: str, kind: str = "flow"):
        lk = links.setdefault((a.id, b.id), Link(a.id, b.id, kind=kind))
        lk.regions.add(reg)
        a.regions.add(reg)
        b.regions.add(reg)

    def mine(reg: str) -> bool:
        return reg != OUTSIDE and (not region or reg == region)

    dummies: set[str] = set()

    def dummy(prc: str) -> bool:
        if DUMMY.match(prc):
            dummies.add(prc)
            return True
        return False

    for reg, prc, com, io in _rows(m, "TOP", ["REG", "PRC", "COM", "IN_OUT"]):
        if mine(reg) and not dummy(prc):
            p, c = node("process", prc), node("commodity", com)
            link(c, p, reg) if io == "IN" else link(p, c, reg)

    for r1, c1, r2, c2, prc in _rows(m, "TOP_IRE", ["ALL_REG", "COM", "ALL_REG_2", "COM_2", "PRC"]):
        if dummy(prc):
            continue
        p = node("process", prc)
        if mine(r1):
            link(node("commodity", c1), p, r1, "trade")
        if mine(r2):
            link(p, node("commodity", c2), r2, "trade")

    for symbol, kind in (("VDA_EMCB", "emission"), ("COM_AGG", "aggregation")):
        for reg, c1, c2 in _rows(m, symbol, ["REG", "COM", "COM_2"]):
            if mine(reg):
                link(node("commodity", c1), node("commodity", c2), reg, kind)
    for symbol in ("FLO_EMIS", "FLO_EFF"):
        for reg, prc, com in _rows(m, symbol, ["REG", "PRC", "COM"]):
            if mine(reg) and not dummy(prc):
                link(node("process", prc), node("commodity", com), reg, "emission")

    # declared but linked in no region (a one-region view cannot tell, so it skips this)
    if not region:
        for (name,) in _rows(m, "PRC"):  # declared PRC(*); its one column
            if dummy(name):
                continue
            n = node("process", name)
            if not n.regions:
                n.flags.add("disconnected")
        for (name,) in _rows(m, "COM"):  # declared COM(COM_GRP); its one column
            if name in RESERVED and nid("commodity", name) not in nodes:
                continue  # TIMES's own ACT: not a model commodity unless something links it
            n = node("commodity", name)
            if not n.regions:
                n.flags.add("unused")
    else:
        nodes = {i: n for i, n in nodes.items() if n.regions}

    for reg, typ, com in _rows(m, "COM_TMAP", ["REG", "COM_TYPE", "COM"]):
        i = nid("commodity", com)
        if i in nodes and (not region or reg == region):
            nodes[i].types.add(typ)
    for reg, grp, prc in _rows(m, "PRC_MAP", ["REG", "PRC_GRP", "PRC"]):
        i = nid("process", prc)
        if i in nodes and (not region or reg == region):
            nodes[i].types.add(grp)
    for symbol, kind in (("COM_DESC", "commodity"), ("PRC_DESC", "process")):
        for name, text in _descriptions(m, symbol).items():
            i = nid(kind, name)
            if i in nodes:
                nodes[i].description = text

    free = set()  # COM_LIM 'N': no commodity balance
    if "COM_LIM" in m.symbols:
        h = m.symbols["COM_LIM"].headers
        for reg, com, lim in _rows(m, "COM_LIM", list(h[:3])):
            if lim == "N" and (not region or reg == region):
                free.add(nid("commodity", com))

    produced = {t for (_, t) in links}
    used = {s for (s, _) in links}
    for n in nodes.values():
        if n.kind != "commodity" or n.flags or n.name in RESERVED or n.id in free:
            continue
        if n.id in used and n.id not in produced:
            n.flags.add("no_supply")
        if n.id in produced and n.id not in used and not (n.types & SINK_TYPES):
            n.flags.add("no_use")

    return Graph(nodes=nodes, links=links, dummies=sorted(dummies))
