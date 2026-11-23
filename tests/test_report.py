"""Parity across runs, reference objectives, "not translated" explained, and the report bundle."""

import io
import json
import time
import urllib.parse
import zipfile

from fastapi.testclient import TestClient
from test_server import RUN, STEP_NAMES, _upload, _wait, _wait_run, fake_compiler, fake_runner

from otui import references, report
from otui.server import create_app, run_switches


def _runner(objective, version=""):
    def run(run, out, until="results", on_event=None):
        r = fake_runner(run, out, until, on_event)
        if r.get("status") == "optimal":
            r["objective"] = objective
            r["rows"], r["columns"] = 10, 12
        if version:
            r["version"] = version
        out.mkdir(parents=True, exist_ok=True)
        (out / "solver.log").write_text("solver says hello\n")
        return r

    return run


def untranslated_runner(run, out, until="results", on_event=None):
    for s in STEP_NAMES[:2]:
        if on_event:
            on_event({"step": s, "state": "running"})
            on_event({"step": s, "state": "done", "seconds": 0.01, "log": [f"{s} ok"]})
    return {
        "status": "not_translated",
        "segment": "ppmain -> ppm_ext",
        "message": "not yet translated: pp_qack.mod L53: nested user constraints (UC_UCN)",
    }


def test_every_solver_kept_with_its_code_and_version(tmp_path):
    runners = {
        "highs": _runner(42.0),
        "cuopt": _runner(42.0000001),
        "cplex": _runner(42.0, "TIMES 4.9.7, GAMS 54.6.1"),
        "gams-highs": _runner(42.0, "TIMES 4.9.7, GAMS 54.6.1"),
    }
    runners["highs"].about = lambda: {"version": "latest @ e6bb7ca"}
    app = create_app(compiler=fake_compiler, runner=runners, root=tmp_path)
    with TestClient(app) as c:
        mid = _wait(c, _upload(c, [("demo.run", RUN)]).json()["id"])["id"]
        for s in ("highs", "cuopt", "cplex", "gams-highs"):
            c.post(f"/api/models/{mid}/solve", data={"solver": s})
            last = _wait_run(c, mid)
        # four solvers: the latest and every earlier one (no cap: the parity panel compares all)
        assert last["solver"] == "gams-highs" and last["code"] == "GAMS TIMES"
        assert last["version"] == "TIMES 4.9.7, GAMS 54.6.1"
        h = {x["solver"]: x for x in last["history"]}
        assert list(h) == ["cplex", "cuopt", "highs"]
        assert h["highs"]["code"] == "TIMES.jl" and h["highs"]["version"] == "latest @ e6bb7ca"
        assert h["cuopt"]["result"]["objective"] == 42.0000001
        assert h["cplex"]["result"]["rows"] == 10


def test_report_zip_and_issue(tmp_path):
    app = create_app(compiler=fake_compiler, runner={"highs": _runner(42.0)}, root=tmp_path)
    with TestClient(app) as c:
        mid = _wait(c, _upload(c, [("demo.run", RUN), ("base.dd", b"x")]).json()["id"])["id"]
        assert c.get(f"/api/models/{mid}/report").status_code == 409  # nothing run yet
        c.post(f"/api/models/{mid}/solve")
        _wait_run(c, mid)
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/models/{mid}/report.zip").content))
        names = set(z.namelist())
        assert {"README.txt", "report.md", "report.json", "logs/solver.log"} <= names
        assert {"model/demo.run", "model/base.dd"} <= names
        data = json.loads(z.read("report.json"))
        assert data["run"]["code"] == "TIMES.jl" and data["run"]["result"]["objective"] == 42.0
        assert [s["step"] for s in data["run"]["steps"]] == list(STEP_NAMES)
        assert data["files"][0]["sha256"]
        assert "| Solve | done |" in z.read("report.md").decode()
        # without the model's files
        r = c.get(f"/api/models/{mid}/report.zip", params={"model": "false"})
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert not any(n.startswith("model/") for n in names)
        assert "demo-otui-report.zip" in r.headers["content-disposition"]
        issue = c.get(f"/api/models/{mid}/report").json()["issue"]
        assert issue["where"] == "TIMES.jl"
        assert issue["url"].startswith("https://github.com/esma-ie/timesjl/issues/new?")


def test_not_translated_says_what_in_the_model(tmp_path):
    app = create_app(compiler=fake_compiler, runner={"highs": untranslated_runner}, root=tmp_path)
    with TestClient(app) as c:
        mid = _wait(c, _upload(c, [("demo.run", RUN)]).json()["id"])["id"]
        app.state.registry.get(mid).symbol_counts["UC_UCN"] = 3
        c.post(f"/api/models/{mid}/solve")
        r = _wait_run(c, mid)["result"]
        assert r["status"] == "not_translated"
        u = r["untranslated"]
        assert u["what"] == "pp_qack.mod L53: nested user constraints (UC_UCN)"
        assert u["gams_files"] == ["pp_qack.mod"]
        assert u["in_model"] == [{"name": "UC_UCN", "records": 3}]
        assert u["coverage"].endswith("docs/gams-julia-map.md")
        issue = c.get(f"/api/models/{mid}/report").json()["issue"]
        title = urllib.parse.parse_qs(urllib.parse.urlparse(issue["url"]).query)["title"][0]
        assert title.startswith("Not yet translated: pp_qack.mod L53")


def test_untranslated_switches_and_unknown_names():
    u = report.untranslated(
        "not yet translated: cal_ire.mod L30 under %IREAUXBAL% without %POWERFLO%",
        {"IRE_FLOSUM": 0},
        {"IREAUXBAL": "YES"},
    )
    assert u["switches"] == [
        {"name": "IREAUXBAL", "value": "YES"},
        {"name": "POWERFLO", "value": ""},
    ]
    assert u["in_model"] == []  # only names the model has records of


def test_run_switches(tmp_path):
    run = tmp_path / "x.run"
    run.write_text(
        "$SET POWERFLO YES\n  $set reduce 'no'\n$   SET VAR_UC  YES \n$BATINCLUDE base.dd\n"
    )
    assert run_switches(run) == {"POWERFLO": "YES", "REDUCE": "no", "VAR_UC": "YES"}


def test_fingerprint_reads_named_dd_files_only(tmp_path):
    (tmp_path / "m.run").write_bytes(b"$BATINCLUDE base.dd\r\n$BATINCLUDE maindrv.mod mod\r\n")
    (tmp_path / "base.dd").write_bytes(b"SET REG / R1 /;\r\n")
    a = references.fingerprint(tmp_path / "m.run")
    (tmp_path / "other.dd").write_text("not named by the run file")
    assert references.fingerprint(tmp_path / "m.run") == a
    # line endings do not matter
    (tmp_path / "m.run").write_bytes(b"$BATINCLUDE base.dd\n$BATINCLUDE maindrv.mod mod\n")
    (tmp_path / "base.dd").write_bytes(b"SET REG / R1 /;\n")
    assert references.fingerprint(tmp_path / "m.run") == a
    (tmp_path / "base.dd").write_text("SET REG / R2 /;\n")
    assert references.fingerprint(tmp_path / "m.run") != a


def test_reference_shown_with_the_model(tmp_path, monkeypatch):
    app = create_app(compiler=fake_compiler, runner=fake_runner, root=tmp_path)
    ref = {"model": "a test model", "objective": 42.0, "source": "a test"}
    monkeypatch.setattr("otui.server.reference_for", lambda run: ref)
    with TestClient(app) as c:
        m = _wait(c, _upload(c, [("demo.run", RUN)]).json()["id"])
        assert m["reference"] == ref


def test_report_markdown_lists_runs():
    entry = {
        "written": time.strftime("%Y"),
        "run_file": "demo.run",
        "environment": {"otui": "0.0.1", "julia": "1.13.2"},
        "run": {"code": "TIMES.jl", "solver_label": "HiGHS", "status": "done", "steps": []},
        "parity": [
            {
                "code": "TIMES.jl",
                "solver_label": "HiGHS",
                "status": "done",
                "objective": 1.5,
                "rows": 9,
                "total": 2.0,
            },
        ],
        "reference": {"model": "demo", "objective": 1.5, "source": "x"},
        "files": [],
    }
    md = report.markdown(entry)
    assert "| TIMES.jl | HiGHS | done | 1.5 | 9 | 2.0 |" in md
    assert "Reference objective: demo, objective 1.5" in md


def test_reader_stops_on_an_untranslated_feature(tmp_path):
    def compiler(run, out):
        raise RuntimeError(
            "not yet translated: $GDXIN (readbprice.mod L18)\nStacktrace:\n  [1] gdollar!(...)"
        )

    app = create_app(compiler=compiler, runner=fake_runner, root=tmp_path)
    with TestClient(app) as c:
        run = b"$   SET TIMESED YES\n" + RUN
        m = _wait(c, _upload(c, [("demo.run", run)]).json()["id"])
        assert m["status"] == "error"
        assert m["error"] == "not yet translated: $GDXIN (readbprice.mod L18)"  # no stack trace
        u = m["untranslated"]
        assert u["what"] == "$GDXIN (readbprice.mod L18)" and u["gams_files"] == ["readbprice.mod"]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(u["ask"]).query)
        assert q["title"] == ["Not yet translated: $GDXIN (readbprice.mod L18)"]
        assert u["ask"].startswith("https://github.com/esma-ie/timesjl/issues/new?")


def test_environment_says_where_otui_runs():
    assert report.environment({"hosting": {"kind": "codespaces"}})["runs_in"] == "codespaces"
    assert report.environment({})["runs_in"] == ""
