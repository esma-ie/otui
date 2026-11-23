"""The Results tab: results kept per run, filtered, grouped, compared and exported."""

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_server import STEP_NAMES, _upload, _wait, _wait_run, fake_compiler

from otui import results
from otui.server import create_app
from otui.store import Symbol


def _arrow(folder: Path, name: str, rows: list[tuple], dims: tuple[str, ...]):
    folder.mkdir(parents=True, exist_ok=True)
    cols = {d: [r[i] for r in rows] for i, d in enumerate(dims)}
    cols["value"] = [float(r[-1]) for r in rows]
    feather.write_feather(pa.table(cols), folder / f"{name}.arrow")


def write_run(folder: Path, scale: float = 1.0):
    """A small run's results: two power plants and a car, two periods."""
    _arrow(folder, "VAR_Cap", [
        ("R1", "2020", "ELCCOA", 10 * scale), ("R1", "2030", "ELCCOA", 5 * scale),
        ("R1", "2020", "ELCWIN", 2 * scale), ("R1", "2030", "ELCWIN", 9 * scale),
        ("R1", "2030", "TCARELC", 1 * scale),
    ], results.RTP)  # fmt: skip
    _arrow(folder, "VAR_Ncap", [("R1", "2030", "ELCWIN", 7 * scale)], results.RTP)
    _arrow(folder, "VAR_FIn", [
        ("R1", "2020", "ELCCOA", "COA", "ANNUAL", 30 * scale),
        ("R1", "2030", "TCARELC", "ELC", "ANNUAL", 3 * scale),
    ], results.FLOW)  # fmt: skip
    _arrow(folder, "VAR_FOut", [
        ("R1", "2020", "ELCCOA", "ELC", "SD", 6 * scale),
        ("R1", "2020", "ELCCOA", "ELC", "SN", 4 * scale),
        ("R1", "2020", "ELCCOA", "CO2", "ANNUAL", 25 * scale),
        ("R1", "2030", "ELCWIN", "ELC", "SD", 8 * scale),
        ("R1", "2030", "TCARELC", "TCAR", "ANNUAL", 3 * scale),
    ], results.FLOW)  # fmt: skip


def fake_model():
    """The sets the filters use, as a loaded model (store.Model) has them."""
    con = duckdb.connect()
    tables = {
        "PRC_MAP": ("REG, PRC_GRP, PRC", [("R1", "ELE", "ELCCOA"), ("R1", "ELE", "ELCWIN"),
                                         ("R1", "RENEW", "ELCWIN"), ("R1", "DMD", "TCARELC")]),
        "COM_TMAP": ("REG, COM_TYPE, COM", [("R1", "NRG", "COA"), ("R1", "NRG", "ELC"),
                                           ("R1", "ENV", "CO2"), ("R1", "DEM", "TCAR")]),
        "PRC_DESC": ("REG, PRC, element_text", [("R1", "ELCCOA", "Coal power plant")]),
        "COM_DESC": ("REG, COM, element_text", [("R1", "ELC", "Electricity")]),
        "COM_UNIT": ("REG, COM, UNITS_COM", [("R1", "ELC", "PJ"), ("R1", "COA", "PJ"),
                                            ("R1", "CO2", "kt"), ("R1", "TCAR", "Bvkm")]),
        "TOP": ("REG, PRC, COM, IN_OUT", [
            ("R1", "ELCCOA", "COA", "IN"), ("R1", "ELCCOA", "ELC", "OUT"),
            ("R1", "ELCWIN", "ELC", "OUT"), ("R1", "TCARELC", "ELC", "IN"),
        ]),
    }  # fmt: skip
    symbols = {}
    for name, (cols, rows) in tables.items():
        names = [c.strip() for c in cols.split(",")]
        con.execute(f'CREATE TABLE "{name}" ({", ".join(f"{c} VARCHAR" for c in names)})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" for _ in names)})', rows)
        symbols[name] = Symbol(name, "set", (), tuple(names), "", len(rows), f"{name}.arrow")
    return SimpleNamespace(con=con, symbols=symbols)


@pytest.fixture
def two(tmp_path):
    """Two runs: a base and one with everything doubled."""
    for sid, scale in (("base", 1.0), ("high", 2.0)):
        write_run(tmp_path / "src" / sid, scale)
        results.save(tmp_path / "src" / sid, tmp_path / sid, fake_model(), {"id": sid})
    res = results.Results({"base": tmp_path / "base", "high": tmp_path / "high"})
    yield res
    res.close()


def _sum(q, **match):
    keys = ("run", "period", "group")
    return sum(r[3] for r in q["rows"] if all(r[keys.index(k)] == v for k, v in match.items()))


def test_saved_as_parquet_with_the_sets(two, tmp_path):
    assert {p.name for p in (tmp_path / "base").iterdir()} >= {
        "VAR_Cap.parquet", "VAR_FOut.parquet", "process_sets.parquet", "topology.parquet",
        "meta.json",
    }  # fmt: skip
    assert pq.read_table(tmp_path / "base" / "VAR_Cap.parquet").num_rows == 5
    assert results.read_meta(tmp_path / "base")["records"]["VAR_FOut"] == 5


def test_grouped_by_process_set_counts_each_process_once(two):
    q = two.query("VAR_Cap", by="process_set")
    assert q["groups"] == ["ELE", "DMD"]  # ELCWIN is in ELE and RENEW: counted in ELE only
    assert _sum(q, run="base", period="2030", group="ELE") == 14
    assert _sum(q, run="high", period="2030", group="ELE") == 28
    assert q["periods"] == ["2020", "2030"] and q["runs"] == ["base", "high"]


def test_filters(two):
    q = two.query("VAR_FOut", {"commodity_set": ["NRG"], "timeslice": ["SD"]}, by="process")
    assert q["groups"] == ["ELCWIN", "ELCCOA"] and q["unit"] == "PJ"
    assert _sum(q, run="base") == 14
    # capacity, filtered by a commodity: the processes that use or make it
    q = two.query("VAR_Cap", {"commodity": ["COA"]}, by="process")
    assert q["groups"] == ["ELCCOA"]
    q = two.query("VAR_Cap", {"process_set": ["RENEW"], "period": ["2030"]}, by="process")
    assert _sum(q, run="base") == 9
    assert two.query("VAR_FOut", by="commodity")["unit"] is None  # PJ, kt and Bvkm


def test_top_groups_and_other(two):
    q = two.query("VAR_FOut", by="commodity", top=1)
    assert q["groups"] == ["CO2", results.OTHER] and q["other"] == 2
    assert _sum(q, run="base", group=results.OTHER) == 21


def test_unknown_requests(two):
    with pytest.raises(results.ResultsError):
        two.query("VAR_Act")
    with pytest.raises(results.ResultsError):
        two.query("VAR_Cap", by="colour")
    with pytest.raises(results.ResultsError):
        two.query("VAR_Cap", {"colour": ["red"]})


def test_facets(two):
    f = two.facets()
    assert f["period"] == ["2020", "2030"]
    assert f["process_set"] == ["DMD", "ELE", "RENEW"]
    assert f["commodity_set"] == ["DEM", "ENV", "NRG"]
    assert {"id": "ELCCOA", "description": "Coal power plant"} in f["process"]
    assert f["timeslice"] == ["ANNUAL", "SD", "SN"]


@pytest.mark.parametrize("fmt", ["parquet", "csv", "xlsx", "duckdb"])
def test_export(two, fmt, tmp_path):
    runs = [{"id": "base", "label": "base"}, {"id": "high", "label": "high"}]
    data, _, ext = results.export(two, runs, fmt)
    if fmt in ("parquet", "csv"):
        z = zipfile.ZipFile(io.BytesIO(data))
        assert f"VAR_FOut.{fmt}" in z.namelist() and "README.txt" in z.namelist()
        if fmt == "parquet":
            t = pq.read_table(io.BytesIO(z.read("VAR_FOut.parquet")))
            assert t.num_rows == 10 and t.column_names[0] == "run"
    elif fmt == "xlsx":
        wb = load_workbook(io.BytesIO(data))
        assert wb["VAR_Cap"].max_row == 11 and "README" in wb.sheetnames
    else:
        (tmp_path / f"r.{ext}").write_bytes(data)
        con = duckdb.connect(str(tmp_path / f"r.{ext}"))
        assert con.execute('SELECT count(*) FROM "VAR_Ncap"').fetchone()[0] == 2


def test_charts_to_excel():
    data = results.charts_xlsx([
        {"title": "Capacity by process set", "unit": "GW", "categories": ["2020", "2030"],
         "series": [{"name": "ELE", "values": [12, 14]}, {"name": "DMD", "values": [0, 1]}]},
        {"title": "Capacity by process set", "categories": ["2020"], "series": [], "kind": "line"},
    ])  # fmt: skip
    wb = load_workbook(io.BytesIO(data))
    ws = wb[wb.sheetnames[0]]
    assert wb.sheetnames[1] != wb.sheetnames[0]  # same title, two sheets
    assert [c.value for c in ws[2]] == ["Period", "ELE", "DMD"]
    assert [c.value for c in ws[3]] == [2020, 12, 0]
    assert len(ws._charts) == 1  # a native Excel chart, to edit there


def test_from_gdx(tmp_path, monkeypatch):
    """GAMS TIMES's report attributes, through gdxdump's CSV (faked here)."""
    csv = {
        "PAR_NCAPL": '"R","ALLYEAR","P","Val"\n"R1","2030","ELCWIN",7\n',
        "PAR_CAPL": '"R","YEAR","P","Val"\n"R1","2030","ELCWIN",7\n',
        "PAR_PASTI": '"R","T","P","ITEM","Val"\n"R1","2030","ELCWIN","0",2\n',
        "F_OUT": '"R","ALLYEAR","T","P","C","S","Val"\n'
        '"R1","2020","2030","ELCWIN","ELC","SD",5\n"R1","2030","2030","ELCWIN","ELC","SD",3\n',
    }

    def fake_run(cmd, **kw):
        sym = next(a[5:] for a in cmd if a.startswith("symb="))
        out = Path(next(a[7:] for a in cmd if a.startswith("output=")))
        if sym in csv:
            out.write_text(csv[sym], encoding="utf-8")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(results.subprocess, "run", fake_run)
    n = results.from_gdx(tmp_path / "m.gdx", tmp_path / "gdxdump", tmp_path / "out")
    assert n == {"VAR_Ncap": 1, "VAR_Cap": 1, "VAR_FOut": 1}
    t = pq.read_table(tmp_path / "out" / "VAR_Cap.parquet").to_pylist()
    assert t == [{"region": "R1", "period": "2030", "process": "ELCWIN", "value": 9.0}]
    t = pq.read_table(tmp_path / "out" / "VAR_FOut.parquet").to_pylist()
    assert t[0]["value"] == 8.0 and t[0]["timeslice"] == "SD"  # summed over vintages


def runner_with_results(scale):
    def run(run, out, until="results", on_event=None):
        for s in STEP_NAMES:
            if on_event:
                on_event({"step": s, "state": "done", "seconds": 0.01, "log": []})
        write_run(out / "results", scale)
        return {"status": "optimal", "objective": 42.0 * scale}

    return run


def test_endpoints(tmp_path):
    runners = {"highs": runner_with_results(1.0), "cplex": runner_with_results(2.0)}
    app = create_app(compiler=fake_compiler, runner=runners, root=tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/results/runs").json()["runs"] == []
        assert c.get("/api/results/facets").status_code == 404
        mid = _upload(c, [("m.run", b"$BATINCLUDE maindrv.mod mod\n")]).json()["id"]
        _wait(c, mid)
        for solver in ("highs", "cplex"):
            c.post(f"/api/models/{mid}/solve", data={"solver": solver})
            assert _wait_run(c, mid)["status"] == "done"
        runs = c.get("/api/results/runs").json()["runs"]
        assert [r["id"] for r in runs] == [f"{mid}.m.highs", f"{mid}.m.cplex"]
        assert [r["label"] for r in runs] == ["m.highs", "m.cplex"]
        assert runs[0]["scenario"] == "m" and runs[0]["run_file"] == "m.run"
        assert runs[1]["objective"] == 84.0
        q = c.post(
            "/api/results/query",
            json={"attribute": "VAR_Cap", "by": "run", "filters": {"period": ["2030"]}},
        ).json()
        assert q["groups"] == [f"{mid}.m.cplex", f"{mid}.m.highs"]
        facets = c.get("/api/results/facets", params={"r": [f"{mid}.m.highs"]}).json()
        assert facets["period"] == ["2020", "2030"]
        r = c.get("/api/results/export", params={"r": [f"{mid}.m.highs"], "fmt": "csv"})
        assert r.status_code == 200 and "m-results.csv.zip" in r.headers["content-disposition"]
        z = zipfile.ZipFile(io.BytesIO(r.content))
        assert z.read("VAR_Cap.csv").decode().splitlines()[1].startswith('"m.highs"')
        assert '"m.highs","m","m.run","highs"' in z.read("runs.csv").decode()
        assert c.get("/api/results/export", params={"fmt": "pdf"}).status_code == 400
        assert c.post("/api/results/query", json={"attribute": "X"}).status_code == 400
        assert c.get("/api/results/facets", params={"r": ["nope"]}).status_code == 404
        r = c.post("/api/results/charts.xlsx", json={"charts": [{"title": "t", "categories": [],
                                                                  "series": []}]})  # fmt: skip
        assert r.status_code == 200


def test_a_run_is_a_scenario_with_a_solver(tmp_path):
    """A scenario is the run file: two run files over the same DD files are two scenarios, each
    kept per solver, and the parity history starts again with the other scenario."""
    runners = {"highs": runner_with_results(1.0), "cplex": runner_with_results(2.0)}
    app = create_app(compiler=fake_compiler, runner=runners, root=tmp_path)
    run = b"$BATINCLUDE maindrv.mod mod\n"
    with TestClient(app) as c:
        mid = _upload(c, [("base.run", run), ("policy.run", run)]).json()["id"]
        c.post(f"/api/models/{mid}/run", data={"run": "base.run"})
        _wait(c, mid)
        for solver in ("highs", "cplex"):
            c.post(f"/api/models/{mid}/solve", data={"solver": solver})
            assert _wait_run(c, mid)["status"] == "done"
        assert len(c.get(f"/api/models/{mid}").json()["solve"]["history"]) == 1
        c.post(f"/api/models/{mid}/run", data={"run": "policy.run"})
        assert "solve" not in _wait(c, mid)  # no runs of this scenario yet
        c.post(f"/api/models/{mid}/solve", data={"solver": "highs"})
        assert _wait_run(c, mid)["history"] == []  # not base's runs
        labels = [r["label"] for r in c.get("/api/results/runs").json()["runs"]]
        assert labels == ["base.highs", "base.cplex", "policy.highs"]  # base.highs is kept

        # the same scenario name in a second model: each label says which model
        mid2 = _upload(c, [("base.run", run)]).json()["id"]
        _wait(c, mid2)
        c.post(f"/api/models/{mid2}/solve", data={"solver": "highs"})
        assert _wait_run(c, mid2)["status"] == "done"
        labels = [r["label"] for r in c.get("/api/results/runs").json()["runs"]]
        assert labels == [
            "base.highs (model 1)", "base.cplex", "policy.highs", "base.highs (model 2)",
        ]  # fmt: skip


def test_ranked_by_difference_from_a_base(tmp_path):
    """Compared with a base, the groups that changed come first, whatever their size."""
    write_run(tmp_path / "src" / "a")
    write_run(tmp_path / "src" / "b")
    t = feather.read_table(tmp_path / "src" / "b" / "VAR_Cap.arrow").to_pylist()
    for r in t:
        if r["process"] == "TCARELC":
            r["value"] = 4.0  # the smallest process: 1 -> 4
    feather.write_feather(pa.Table.from_pylist(t), tmp_path / "src" / "b" / "VAR_Cap.arrow")
    for s in ("a", "b"):
        results.save(tmp_path / "src" / s, tmp_path / s, fake_model(), {"id": s})
    res = results.Results({"a": tmp_path / "a", "b": tmp_path / "b"})
    try:
        assert res.query("VAR_Cap", by="process", top=1)["groups"][0] == "ELCCOA"  # by size
        q = res.query("VAR_Cap", by="process", top=1, base="a")
        assert q["groups"] == ["TCARELC", results.OTHER]
    finally:
        res.close()
