"""Load a TIMES.jl dump (Arrow format v1) into DuckDB, with otui table-format headers.

The dump is what TIMES.jl's reader writes for a compiled model
(``bridge.JuliaWorker.compile``): ``manifest.json``, ``uels.arrow`` and one
``<symbol>.arrow`` per symbol with records. Each symbol file has positional
domain columns ``d1 .. dN`` and value columns.

Table format: one table per TIMES symbol, named by its TIMES code
name; index columns headed by the symbol's domain short names as declared in
the TIMES code. Rules for the cases the declarations leave open:

- a one-dimensional set names its column after itself, whatever its declared
  domain: ``PRC(*)`` gives ``PRC`` and ``COM(COM_GRP)`` gives ``COM``;
- any other universe index is ``UNI``;
- a name repeated in one symbol takes a suffix: ``REG``, ``REG_2``.

Parameters keep ``value`` and ``value__sv``; sets keep ``element_text``.
``value__sv`` is the format's special-value code: 0 ordinary, 1 EPS, 2 NA,
3 UNDF (+INF and -INF are ordinary IEEE values).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow.feather as feather

FORMAT = "timesjl-dump"
SPECIAL_VALUES = {1: "EPS", 2: "NA", 3: "UNDF"}
UNIVERSE = "UNI"
CATALOGUE = "_symbols"


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # set, parameter, alias, variable, equation
    domain: tuple[str, ...]
    headers: tuple[str, ...]  # index column names (docs/table-format.md)
    description: str
    records: int
    file: str | None

    @property
    def dimension(self) -> int:
        return len(self.domain)

    @property
    def has_table(self) -> bool:
        return self.records > 0 and self.file is not None and self.kind in ("set", "parameter")


def headers_for(name: str, kind: str, domain: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Index column headers for a symbol's domain."""
    if kind == "set" and len(domain) == 1:
        return (name,)
    base = [UNIVERSE if d == "*" else d for d in domain]
    seen: dict[str, int] = {}
    out = []
    for h in base:
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    return tuple(out)


def read_manifest(dump: Path) -> dict:
    m = json.loads((Path(dump) / "manifest.json").read_text(encoding="utf-8"))
    if m.get("format") != FORMAT or m.get("format_version") != 1:
        raise ValueError(f"{dump}: not a TIMES.jl dump in format version 1")
    return m


def symbols_from_manifest(m: dict) -> list[Symbol]:
    out = []
    for s in m["symbols"]:
        domain = tuple(s.get("domain") or ())
        kind = s["kind"]
        out.append(
            Symbol(
                name=s["name"],
                kind=kind,
                domain=domain,
                headers=headers_for(s["name"], kind, domain),
                description=s.get("description") or "",
                records=int(s.get("records") or 0),
                file=s.get("file"),
            )
        )
    return out


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


@dataclass
class Model:
    """A TIMES model loaded into DuckDB: one table per set or parameter with records."""

    con: duckdb.DuckDBPyConnection
    symbols: dict[str, Symbol] = field(default_factory=dict)
    source: str = ""

    def table(self, name: str):
        """A symbol's records as a DuckDB relation, with table-format headers."""
        s = self.symbols[name]
        if not s.has_table:
            raise KeyError(f"{name} has no records")
        return self.con.table(_q(name))

    def df(self, name: str):
        return self.table(name).df()

    @property
    def tables(self) -> list[str]:
        return [n for n, s in self.symbols.items() if s.has_table]


def load_dump(dump: str | Path, database: str = ":memory:") -> Model:
    """Load a TIMES.jl dump into DuckDB (in memory by default, or a database file)."""
    dump = Path(dump)
    m = read_manifest(dump)
    syms = symbols_from_manifest(m)
    con = duckdb.connect(database)
    con.execute(
        f"CREATE OR REPLACE TABLE {CATALOGUE} (name VARCHAR PRIMARY KEY, kind VARCHAR, "
        "dimension INTEGER, domain VARCHAR[], headers VARCHAR[], description VARCHAR, "
        "records BIGINT)"
    )
    con.executemany(
        f"INSERT INTO {CATALOGUE} VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (s.name, s.kind, s.dimension, list(s.domain), list(s.headers), s.description, s.records)
            for s in syms
        ],
    )
    for s in syms:
        if not s.has_table:
            continue
        t = feather.read_table(dump / s.file)
        rename = {f"d{i + 1}": h for i, h in enumerate(s.headers)}
        t = t.rename_columns([rename.get(c, c) for c in t.column_names])
        con.register("_incoming", t)
        con.execute(f"CREATE OR REPLACE TABLE {_q(s.name)} AS SELECT * FROM _incoming")
        con.unregister("_incoming")
    return Model(con=con, symbols={s.name: s for s in syms}, source=m.get("source", ""))
