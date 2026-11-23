"""The results of solved runs, for the Results tab: capacity and flows, filtered, grouped, compared
across runs and exported.

Four of the report attributes VEDA users know, with the same meaning in both codes:

    VAR_Cap   (region, period, process)                         capacity, new and existing
    VAR_Ncap  (region, period, process)                         new capacity
    VAR_FIn   (region, period, process, commodity, timeslice)   flows into processes
    VAR_FOut  (region, period, process, commodity, timeslice)   flows out of processes

GAMS TIMES computes them in its report (rptmisc.rpt, rptmain.rpt): VAR_Cap is PAR_CAPL + PAR_PASTI,
VAR_Ncap is PAR_NCAPL, VAR_FIn and VAR_FOut are F_IN and F_OUT summed over vintages. otui reads
them from the run's GDX file with GAMS's gdxdump (``from_gdx``). TIMES.jl does not translate the
report yet (its M1.6), so otui computes the same from TIMES.jl's solution (``runchain.jl``,
``otui_results``) and writes them as Arrow files.

Each run's results are kept as Parquet files, one folder per run (``save``), with the model's sets
for the filters: process sets (PRC_MAP), commodity sets (COM_TMAP), descriptions, units and the
process topology (TOP).

The words TIMES modellers use: the **model** is the DD files; a **scenario** is the run file, which
picks the DD files to include; a **run** is one scenario solved with one solver, named
``<scenario>.<solver>`` (``demo12.highs``). Each run is one such folder.
"""

from __future__ import annotations

import io
import json
import math
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq

RTP = ("region", "period", "process")
FLOW = (*RTP, "commodity", "timeslice")
ATTRIBUTES: dict[str, dict[str, Any]] = {
    "VAR_Cap": {"label": "Capacity", "dims": RTP, "about": "installed capacity, new and existing"},
    "VAR_Ncap": {"label": "New capacity", "dims": RTP, "about": "capacity built in the period"},
    "VAR_FIn": {"label": "Flows in", "dims": FLOW, "about": "commodity flows into processes"},
    "VAR_FOut": {"label": "Flows out", "dims": FLOW, "about": "commodity flows out of processes"},
}
# what a chart or table can be filtered and grouped by
DIMENSIONS = (
    "region",
    "period",
    "process",
    "process_set",
    "commodity",
    "commodity_set",
    "timeslice",
)
GROUPS = (*DIMENSIONS, "run")
# a process can be in more than one process set (PRC_MAP): grouped by set, it counts in the first
# of these that it is in, so nothing is counted twice
PROCESS_SETS = (
    "ELE", "CHP", "HPL", "STG", "STK", "STS", "NST", "PRE", "PRW", "REF", "XTRACT", "RENEW",
    "IRE", "DMD", "MISC",
)  # fmt: skip
OTHER = "Other"
DIFF_TOL = 1e-6  # a difference between runs within this (relative) is none: TIMES.jl's parity check
NONE = "(none)"
EXCEL_ROWS = 1_048_575  # a sheet's rows, less its header


class ResultsError(ValueError):
    """A request the results cannot answer (unknown attribute, run or grouping)."""


# --- reading a run's results -------------------------------------------------------------------


def from_gdx(gdx: Path, gdxdump: Path, dest: Path) -> dict[str, int]:
    """GAMS TIMES's report attributes from a run's GDX file, as Parquet files in ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    with tempfile.TemporaryDirectory() as tmp:

        def read(symbol: str) -> str | None:
            """The symbol as a DuckDB view (columns c0..cN, value), or None if the GDX lacks it."""
            out = Path(tmp) / f"{symbol}.csv"
            r = subprocess.run(
                [
                    str(gdxdump),
                    str(gdx),
                    f"symb={symbol}",
                    "format=csv",
                    "EpsOut=0",
                    f"output={out}",
                ],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
                return None
            rel = con.read_csv(str(out), header=True, all_varchar=True)
            cols = rel.columns
            sel = ", ".join(f'"{c}" AS c{i}' for i, c in enumerate(cols[:-1]))
            con.register(f"_{symbol}_raw", rel)
            con.execute(
                f'CREATE OR REPLACE VIEW "{symbol}" AS SELECT {sel}, '
                f'TRY_CAST("{cols[-1]}" AS DOUBLE) AS value FROM "_{symbol}_raw"'
            )
            return symbol

        queries: dict[str, str] = {}
        if read("PAR_NCAPL"):
            queries["VAR_Ncap"] = (
                "SELECT c0 AS region, c1 AS period, c2 AS process, sum(value) AS value "
                "FROM PAR_NCAPL GROUP BY ALL"
            )
        capl, pasti = read("PAR_CAPL"), read("PAR_PASTI")
        parts = []
        if capl:
            parts.append("SELECT c0, c1, c2, value FROM PAR_CAPL")
        if pasti:
            parts.append("SELECT c0, c1, c2, value FROM PAR_PASTI")
        if parts:
            queries["VAR_Cap"] = (
                "SELECT c0 AS region, c1 AS period, c2 AS process, sum(value) AS value FROM ("
                + " UNION ALL ".join(parts)
                + ") GROUP BY ALL"
            )
        for sym, name in (("F_IN", "VAR_FIn"), ("F_OUT", "VAR_FOut")):
            if read(sym):  # F_IN(R, ALLYEAR, T, P, C, S): summed over vintages
                queries[name] = (
                    "SELECT c0 AS region, c2 AS period, c3 AS process, c4 AS commodity, "
                    f"c5 AS timeslice, sum(value) AS value FROM {sym} GROUP BY ALL"
                )
        counts = {}
        for name, q in queries.items():
            sql = f"SELECT * FROM ({q}) WHERE abs(value) > 1e-9 ORDER BY ALL"
            t = con.sql(sql).to_arrow_table()
            pq.write_table(t, dest / f"{name}.parquet")
            counts[name] = t.num_rows
    con.close()
    return counts


def _read_attribute(src: Path, name: str) -> pa.Table | None:
    if (src / f"{name}.parquet").exists():
        return pq.read_table(src / f"{name}.parquet")
    if (src / f"{name}.arrow").exists():
        return feather.read_table(src / f"{name}.arrow")
    return None


def _model_tables(model: Any) -> dict[str, pa.Table]:
    """The model's sets the filters need, from its loaded dump (store.Model)."""

    def q(sql: str, *names: str) -> pa.Table | None:
        if any(n not in model.symbols or not model.symbols[n].has_table for n in names):
            return None
        return model.con.sql(sql).to_arrow_table()

    out = {
        "process_sets": q(
            'SELECT "REG" AS region, "PRC" AS process, "PRC_GRP" AS "set" FROM "PRC_MAP"',
            "PRC_MAP",
        ),
        "commodity_sets": q(
            'SELECT "REG" AS region, "COM" AS commodity, "COM_TYPE" AS "set" FROM "COM_TMAP"',
            "COM_TMAP",
        ),
        "process_names": q(
            'SELECT "REG" AS region, "PRC" AS id, element_text AS description FROM "PRC_DESC"',
            "PRC_DESC",
        ),
        "commodity_names": q(
            'SELECT "REG" AS region, "COM" AS id, element_text AS description FROM "COM_DESC"',
            "COM_DESC",
        ),
        "commodity_units": q(
            'SELECT "REG" AS region, "COM" AS commodity, "UNITS_COM" AS unit FROM "COM_UNIT"',
            "COM_UNIT",
        ),
        "topology": q(
            'SELECT "REG" AS region, "PRC" AS process, "COM" AS commodity, "IN_OUT" AS io '
            'FROM "TOP"',
            "TOP",
        ),
    }
    return {k: v for k, v in out.items() if v is not None}


def save(src: Path, dest: Path, model: Any, meta: dict[str, Any]) -> dict[str, int]:
    """Keep a run's results (Arrow or Parquet files in ``src``) and the model's sets as Parquet
    files in ``dest``, replacing what was there. Returns the records per attribute."""
    tmp = dest.with_name(dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    counts: dict[str, int] = {}
    for name, a in ATTRIBUTES.items():
        t = _read_attribute(src, name)
        if t is None:
            t = pa.table(
                {
                    **{d: pa.array([], pa.string()) for d in a["dims"]},
                    "value": pa.array([], pa.float64()),
                }
            )
        pq.write_table(t, tmp / f"{name}.parquet")
        counts[name] = t.num_rows
    if model is not None:
        for name, t in _model_tables(model).items():
            pq.write_table(t, tmp / f"{name}.parquet")
    (tmp / "meta.json").write_text(
        json.dumps({**meta, "records": counts, "saved": time.time()}, indent=1), encoding="utf-8"
    )
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return counts


def has_results(src: Path) -> bool:
    return any(_read_attribute(src, n) is not None for n in ATTRIBUTES)


# --- runs --------------------------------------------------------------------------------------


def read_meta(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _path(p: Path) -> str:
    return _sql_str(p.as_posix())


class Results:
    """Queries over a set of runs: {run id: its results folder}."""

    def __init__(self, runs: dict[str, Path]):
        if not runs:
            raise ResultsError("no results: solve a model first")
        self.runs = runs
        self.con = duckdb.connect()
        for name in (*ATTRIBUTES, "process_sets", "commodity_sets", "process_names",
                     "commodity_names", "commodity_units", "topology"):  # fmt: skip
            self._view(name)
        # each process's set for grouping: the first of PROCESS_SETS it is in, else any
        order = " ".join(f"WHEN {_sql_str(s)} THEN {i}" for i, s in enumerate(PROCESS_SETS))
        self.con.execute(
            "CREATE VIEW process_group AS SELECT run, region, process, "
            f'arg_min("set", CASE "set" {order} ELSE 999 END) AS grp '
            "FROM process_sets GROUP BY ALL"
        )
        self.con.execute(
            'CREATE VIEW commodity_group AS SELECT run, region, commodity, min("set") AS grp '
            "FROM commodity_sets GROUP BY ALL"
        )

    def _view(self, name: str):
        parts = [
            f"SELECT {_sql_str(sid)} AS run, * FROM read_parquet({_path(d / (name + '.parquet'))})"
            for sid, d in self.runs.items()
            if (d / f"{name}.parquet").exists()
        ]
        if not parts:  # an empty table with the expected columns
            cols = {
                "process_sets": 'region, process, "set"',
                "commodity_sets": 'region, commodity, "set"',
                "process_names": "region, id, description",
                "commodity_names": "region, id, description",
                "commodity_units": "region, commodity, unit",
                "topology": "region, process, commodity, io",
            }.get(name, ", ".join(ATTRIBUTES.get(name, {}).get("dims", ())) + ", value")
            sel = ", ".join(
                f"NULL::{'DOUBLE' if c.strip() == 'value' else 'VARCHAR'} AS {c.strip()}"
                for c in cols.split(",")
            )
            parts = [f"SELECT NULL::VARCHAR AS run, {sel} WHERE false"]
        self.con.execute(f'CREATE VIEW "{name}" AS ' + " UNION ALL BY NAME ".join(parts))

    def close(self):
        self.con.close()

    # --- filters

    def _where(self, attr: str, filters: dict[str, list[str]]) -> tuple[str, list[Any]]:
        flows = "commodity" in ATTRIBUTES[attr]["dims"]
        sql, args = [], []

        def isin(col: str, vals: list[str]):
            sql.append(f"{col} IN ({', '.join('?' for _ in vals)})")
            args.extend(vals)

        for dim, vals in filters.items():
            vals = [str(v) for v in vals or []]
            if not vals:
                continue
            if dim not in DIMENSIONS:
                raise ResultsError(f"no filter {dim}: one of {', '.join(DIMENSIONS)}")
            if dim in ("region", "period", "process"):
                isin(f"d.{dim}", vals)
            elif dim == "timeslice":
                if flows:
                    isin("d.timeslice", vals)
            elif dim == "process_set":
                sql.append(
                    "EXISTS (SELECT 1 FROM process_sets s WHERE s.run = d.run AND "
                    's.region = d.region AND s.process = d.process AND s."set" IN '
                    f"({', '.join('?' for _ in vals)}))"
                )
                args.extend(vals)
            elif dim == "commodity":
                if flows:
                    isin("d.commodity", vals)
                else:  # capacity of the processes that use or make these commodities
                    sql.append(
                        "EXISTS (SELECT 1 FROM topology t WHERE t.run = d.run AND "
                        "t.region = d.region AND t.process = d.process AND t.commodity IN "
                        f"({', '.join('?' for _ in vals)}))"
                    )
                    args.extend(vals)
            elif dim == "commodity_set":
                ph = ", ".join("?" for _ in vals)
                if flows:
                    sql.append(
                        "EXISTS (SELECT 1 FROM commodity_sets s WHERE s.run = d.run AND "
                        f's.region = d.region AND s.commodity = d.commodity AND s."set" IN ({ph}))'
                    )
                else:
                    sql.append(
                        "EXISTS (SELECT 1 FROM topology t JOIN commodity_sets s ON "
                        "s.run = t.run AND s.region = t.region "
                        "AND s.commodity = t.commodity "
                        "WHERE t.run = d.run AND t.region = d.region AND "
                        f't.process = d.process AND s."set" IN ({ph}))'
                    )
                args.extend(vals)
        return (" AND ".join(sql) or "true"), args

    def _group(self, attr: str, by: str) -> tuple[str, str]:
        """(the group expression, the joins it needs)."""
        flows = "commodity" in ATTRIBUTES[attr]["dims"]
        if by not in GROUPS:
            raise ResultsError(f"no grouping {by}: one of {', '.join(GROUPS)}")
        if by in ("commodity", "timeslice", "commodity_set") and not flows:
            by = "process_set"  # capacity has no commodity or timeslice
        if by == "process_set":
            return (
                f"coalesce(pg.grp, {_sql_str(NONE)})",
                "LEFT JOIN process_group pg ON pg.run = d.run AND pg.region = d.region "
                "AND pg.process = d.process",
            )
        if by == "commodity_set":
            return (
                f"coalesce(cg.grp, {_sql_str(NONE)})",
                "LEFT JOIN commodity_group cg ON cg.run = d.run AND "
                "cg.region = d.region AND cg.commodity = d.commodity",
            )
        return f"d.{by}", ""

    # --- queries

    def query(
        self,
        attr: str,
        filters: dict[str, list[str]] | None = None,
        by: str = "process_set",
        top: int = 12,
        base: str | None = None,
    ) -> dict[str, Any]:
        """``attr`` summed by run, period and group (``by``), after ``filters``. The ``top``
        groups are kept, the rest summed into "Other": the largest groups, or with ``base`` (a
        run) the groups that differ most from it, so a comparison shows what changed."""
        if attr not in ATTRIBUTES:
            raise ResultsError(f"no attribute {attr}: one of {', '.join(ATTRIBUTES)}")
        where, args = self._where(attr, filters or {})
        grp, join = self._group(attr, by)
        rows = self.con.execute(
            f'SELECT d.run, d.period, {grp} AS grp, sum(d.value) AS value FROM "{attr}" d '
            f"{join} WHERE {where} GROUP BY ALL",
            args,
        ).fetchall()
        size: dict[str, float] = {}
        for _, _, g, v in rows:
            size[g] = size.get(g, 0.0) + abs(v)
        change: dict[str, float] = {}
        if base in self.runs and len(self.runs) > 1:
            at = {(s, p, g): v for s, p, g, v in rows}
            cells = {(p, g) for _, p, g in at}
            for s in self.runs:
                if s == base:
                    continue
                for p, g in cells:
                    v, b = at.get((s, p, g), 0.0), at.get((base, p, g), 0.0)
                    if abs(v - b) > DIFF_TOL * max(1.0, abs(v), abs(b)):
                        change[g] = change.get(g, 0.0) + abs(v - b)
        ranked = sorted(size, key=lambda g: (-change.get(g, 0.0), -size[g], g))
        keep = set(ranked[: max(1, top)]) if top and len(ranked) > top else set(ranked)
        acc: dict[tuple[str, str, str], float] = {}
        for s, p, g, v in rows:
            k = (s, p, g if g in keep else OTHER)
            acc[k] = acc.get(k, 0.0) + v
        groups = [g for g in ranked if g in keep] + ([OTHER] if len(keep) < len(ranked) else [])
        periods = sorted({p for _, p, _ in acc}, key=_period_key)
        return {
            "attribute": attr,
            "by": by,
            "groups": groups,
            "periods": periods,
            "runs": [s for s in self.runs if any(k[0] == s for k in acc)],
            "rows": [[s, p, g, v] for (s, p, g), v in sorted(acc.items())],
            "other": len(ranked) - len(keep),
            "unit": self.unit(attr, filters or {}),
        }

    def unit(self, attr: str, filters: dict[str, list[str]]) -> str | None:
        """The commodities' unit of a flow attribute, when they share one."""
        if "commodity" not in ATTRIBUTES[attr]["dims"]:
            return None
        where, args = self._where(attr, filters)
        units = self.con.execute(
            f'SELECT DISTINCT u.unit FROM "{attr}" d JOIN commodity_units u ON '
            "u.run = d.run AND u.region = d.region AND u.commodity = d.commodity "
            f"WHERE {where} LIMIT 2",
            args,
        ).fetchall()
        return units[0][0] if len(units) == 1 else None

    def facets(self) -> dict[str, Any]:
        """Every value each filter can take, in the results of these runs."""

        def distinct(sql: str) -> list[str]:
            return [r[0] for r in self.con.execute(sql).fetchall() if r[0] is not None]

        rtp = " UNION ".join(f'SELECT region, period, process FROM "{a}"' for a in ATTRIBUTES)
        flows = " UNION ".join(
            f'SELECT region, commodity, timeslice FROM "{a}"' for a in ("VAR_FIn", "VAR_FOut")
        )
        self.con.execute(f"CREATE OR REPLACE TEMP VIEW _rtp AS {rtp}")
        self.con.execute(f"CREATE OR REPLACE TEMP VIEW _flows AS {flows}")
        names = {
            kind: dict(
                self.con.execute(
                    f"SELECT id, any_value(description) FROM {kind}_names GROUP BY id"
                ).fetchall()
            )
            for kind in ("process", "commodity")
        }
        processes = distinct("SELECT DISTINCT process FROM _rtp ORDER BY 1")
        commodities = distinct("SELECT DISTINCT commodity FROM _flows ORDER BY 1")
        return {
            "region": distinct("SELECT DISTINCT region FROM _rtp ORDER BY 1"),
            "period": sorted(distinct("SELECT DISTINCT period FROM _rtp"), key=_period_key),
            "process": [{"id": p, "description": names["process"].get(p, "")} for p in processes],
            "process_set": distinct(
                'SELECT DISTINCT "set" FROM process_sets WHERE process IN '
                "(SELECT process FROM _rtp) ORDER BY 1"
            ),
            "commodity": [
                {"id": c, "description": names["commodity"].get(c, "")} for c in commodities
            ],
            "commodity_set": distinct(
                'SELECT DISTINCT "set" FROM commodity_sets WHERE commodity IN '
                "(SELECT commodity FROM _flows) ORDER BY 1"
            ),
            "timeslice": distinct("SELECT DISTINCT timeslice FROM _flows ORDER BY 1"),
        }

    # --- export

    def table(self, attr: str) -> pa.Table:
        """One attribute's records across the runs, a run column first."""
        return self.con.sql(f'SELECT * FROM "{attr}" ORDER BY ALL').to_arrow_table()

    def sets(self) -> dict[str, pa.Table]:
        return {
            n: self.con.sql(f'SELECT * FROM "{n}" ORDER BY ALL').to_arrow_table()
            for n in (
                "process_sets",
                "commodity_sets",
                "process_names",
                "commodity_names",
                "commodity_units",
            )  # fmt: skip
        }


def _period_key(p: str) -> tuple[int, Any]:
    return (0, int(p)) if re.fullmatch(r"-?\d+", str(p)) else (1, str(p))


# --- export ------------------------------------------------------------------------------------


def _readme(runs: list[dict[str, Any]]) -> str:
    lines = [
        "Results exported by otui (Open TIMES User Interface).",
        "",
        "Attributes (VEDA's names):",
        *(f"  {n}: {a['about']} ({', '.join(a['dims'])})" for n, a in ATTRIBUTES.items()),
        "",
        "GAMS TIMES runs: from TIMES's own report (PAR_CAPL + PAR_PASTI, PAR_NCAPL, F_IN, F_OUT).",
        "TIMES.jl runs: computed by otui from TIMES.jl's solution as TIMES's report computes them,",
        "until TIMES.jl translates the report itself (capacity-related and auxiliary trade and",
        "storage flows are not in its flows yet).",
        "",
        "Runs: a scenario (the run file, which picks the DD files to include) solved with one",
        "solver, named <scenario>.<solver>. The run column of every table names the run.",
        *(
            f"  {s['label']}: scenario {s.get('scenario')} ({s.get('run_file')}), "
            f"{s.get('code')} + {s.get('solver_label', s.get('solver'))}, "
            f"objective {s.get('objective')}"
            for s in runs
        ),
    ]
    return "\n".join(lines) + "\n"


RUN_COLUMNS = ("run", "scenario", "run_file", "solver", "code", "version", "objective")


def export(res: Results, runs: list[dict[str, Any]], fmt: str) -> tuple[bytes, str, str]:
    """The runs' whole results as ``fmt``: parquet (a zip of Parquet files), duckdb (a
    database file), csv (a zip) or xlsx. ``res`` names each run by its label, as ``runs`` do.
    Returns (bytes, media type, file extension)."""
    tables = {n: res.table(n) for n in ATTRIBUTES}
    tables.update(res.sets())
    tables["runs"] = pa.Table.from_pylist(
        [{k: s.get("label") if k == "run" else s.get(k) for k in RUN_COLUMNS} for s in runs]
    )
    readme = _readme(runs)
    if fmt in ("parquet", "csv"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", readme)
            for n, t in tables.items():
                b = io.BytesIO()
                if fmt == "parquet":
                    pq.write_table(t, b)
                else:
                    import pyarrow.csv as pacsv

                    pacsv.write_csv(t, b)
                z.writestr(f"{n}.{fmt}", b.getvalue())
        return buf.getvalue(), "application/zip", f"{fmt}.zip"
    if fmt == "duckdb":
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.duckdb"
            con = duckdb.connect(str(path))
            for n, t in tables.items():
                con.register("_t", t)
                con.execute(f'CREATE TABLE "{n}" AS SELECT * FROM _t')
                con.unregister("_t")
            con.execute("CREATE TABLE readme (text VARCHAR)")
            con.execute("INSERT INTO readme VALUES (?)", [readme])
            con.close()
            return path.read_bytes(), "application/octet-stream", "duckdb"
    if fmt == "xlsx":
        return _xlsx(tables, readme), XLSX, "xlsx"
    raise ResultsError(f"no export format {fmt}: parquet, duckdb, csv or xlsx")


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(tables: dict[str, pa.Table], readme: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("README")
    for line in readme.splitlines():
        ws.append([line])
    bold = Font(bold=True)
    for n, t in tables.items():
        cols = [t.column(i).to_pylist() for i in range(t.num_columns)]
        for part in range(max(1, math.ceil(t.num_rows / EXCEL_ROWS))):  # a sheet holds ~1M rows
            ws = wb.create_sheet(n if part == 0 else f"{n} ({part + 1})"[:31])
            head = []
            for c in t.column_names:
                cell = WriteOnlyCell(ws, value=c)
                cell.font = bold
                head.append(cell)
            ws.append(head)
            for i in range(part * EXCEL_ROWS, min(t.num_rows, (part + 1) * EXCEL_ROWS)):
                ws.append([col[i] for col in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def charts_xlsx(charts: list[dict[str, Any]]) -> bytes:
    """Charts as an Excel workbook to edit further: one sheet per chart, with its data as a table
    and a native Excel chart drawn from it.

    Each chart: {title, unit?, categories: [period, ...], series: [{name, values}], stacked?: bool,
    kind?: "bar" | "line"}."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.styles import Font

    if not charts:
        raise ResultsError("no charts to export")
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for i, ch in enumerate(charts):
        name = re.sub(r"[\[\]:*?/\\]", " ", str(ch.get("title") or f"Chart {i + 1}"))[:28].strip()
        base, n = name or f"Chart {i + 1}", 2
        while name in used:
            name = f"{base[:25]} ({n})"
            n += 1
        used.add(name)
        ws = wb.create_sheet(name)
        cats = [str(c) for c in ch.get("categories", [])]
        series = ch.get("series", [])
        ws.append([ch.get("title", ""), *([ch["unit"]] if ch.get("unit") else [])])
        ws["A1"].font = Font(bold=True, size=12)
        ws.append([ch.get("category_label", "Period"), *[str(s["name"]) for s in series]])
        for c in ws[2]:
            c.font = Font(bold=True)
        for j, cat in enumerate(cats):
            ws.append(
                [
                    _num(cat),
                    *[_cell(s["values"][j] if j < len(s["values"]) else None) for s in series],
                ]
            )
        if not series or not cats:
            continue
        kind = ch.get("kind", "bar")
        chart = LineChart() if kind == "line" else BarChart()
        if kind != "line":
            chart.type = "col"
            if ch.get("stacked", True):
                chart.grouping = "stacked"
                chart.overlap = 100
        chart.title = ch.get("title", "")
        chart.y_axis.title = ch.get("unit") or ""
        chart.x_axis.title = ch.get("category_label", "Period")
        last = 2 + len(cats)
        data = Reference(ws, min_col=2, max_col=1 + len(series), min_row=2, max_row=last)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=3, max_row=last))
        chart.width, chart.height = 24, 13
        ws.add_chart(chart, f"{_col(len(series) + 3)}2")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _num(s: str) -> Any:
    return int(s) if re.fullmatch(r"-?\d+", s) else s


def _cell(v: Any) -> Any:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
