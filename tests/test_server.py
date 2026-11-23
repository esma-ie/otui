"""The local API, with a fake compiler that writes a hand-made dump (no Julia needed)."""

import io
import shutil
import time
from pathlib import Path

import pytest
from conftest import make_dump
from fastapi.testclient import TestClient

from otui.server import create_app

RUN = b"$BATINCLUDE base.dd\n$BATINCLUDE maindrv.mod mod\n"


def fake_compiler(run, out):
    out.mkdir(parents=True, exist_ok=True)
    if run.name == "broken.run":
        raise RuntimeError("DDSyntaxError: base.dd:3: expected a label")
    (out.parent / "src").mkdir(exist_ok=True)
    src = make_dump(out.parent / "src")
    for p in src.iterdir():
        shutil.copy(p, out / p.name)
    return {"symbols": 4}


QA = """ *** COM_FR does not sum to unity
 *01 WARNING       - Normalized to 1, R=R1 C=D
"""


STEP_NAMES = ("read", "preprocess", "build", "pass", "solve", "results")


def fake_runner(run, out, until="results", on_event=None):
    for s in STEP_NAMES[: STEP_NAMES.index(until) + 1]:
        if on_event:
            on_event({"step": s, "state": "running"})
            on_event({"step": s, "state": "done", "seconds": 0.01, "log": [f"{s} ok"]})
    if run.name == "infeasible.run":
        return {"status": "not_optimal", "termination": "INFEASIBLE", "qa_log": QA}
    if until != "results":
        return {"status": "generated" if until in ("build", "pass") else "stopped", "qa_log": QA}
    return {"status": "optimal", "objective": 42.0, "qa_log": QA}


@pytest.fixture
def client(tmp_path):
    app = create_app(compiler=fake_compiler, runner=fake_runner, root=tmp_path)
    with TestClient(app) as c:
        yield c


def _wait_run(client, mid, timeout=10):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        r = client.get(f"/api/models/{mid}").json()
        if r.get("solve", {}).get("status") not in ("running", None):
            return r["solve"]
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def _wait(client, mid, timeout=10):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        r = client.get(f"/api/models/{mid}").json()
        if r["status"] not in ("compiling", "loading"):
            return r
        time.sleep(0.05)
    raise AssertionError("model did not finish")


def _upload(client, files, **data):
    return client.post(
        "/api/models",
        files=[("files", (n, io.BytesIO(b), "application/octet-stream")) for n, b in files],
        data=data,
    )


def test_health(client):
    assert client.get("/api/health").json()["engine"] == "external"


def test_health_says_local(client, monkeypatch):
    monkeypatch.delenv("CODESPACES", raising=False)
    assert client.get("/api/health").json()["hosting"] == {"kind": "local"}


def test_health_says_codespace_without_its_name(client, monkeypatch):
    # GitHub sets these in every codespace; the name starts with the user's GitHub name
    monkeypatch.setenv("CODESPACES", "true")
    monkeypatch.setenv("CODESPACE_NAME", "octocat-literate-space-parakeet-mld5")
    h = client.get("/api/health").json()
    assert h["hosting"] == {"kind": "codespaces"}
    assert "octocat" not in str(h)


def test_forwarded_url(monkeypatch):
    from otui.server import forwarded_url

    monkeypatch.setenv("CODESPACE_NAME", "octocat-literate-space-parakeet-mld5")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    monkeypatch.delenv("CODESPACES", raising=False)
    assert forwarded_url(8642) == ""  # not in a codespace
    monkeypatch.setenv("CODESPACES", "true")
    assert forwarded_url(8642) == "https://octocat-literate-space-parakeet-mld5-8642.app.github.dev"


def test_upload_compile_browse(client):
    r = _upload(client, [("demo.run", RUN), ("base.dd", b"x")])
    assert r.status_code == 200, r.text
    m = _wait(client, r.json()["id"])
    assert m["status"] == "ready", m
    assert m["run"] == "demo.run"
    assert m["summary"]["tables"] == 2
    assert m["summary"]["counts"]["PRC"] == 2

    syms = {s["name"]: s for s in client.get(f"/api/models/{m['id']}/symbols").json()}
    assert syms["NCAP_COST"]["headers"] == ["REG", "ALLYEAR", "PRC", "CUR"]
    assert syms["COM"]["has_table"] is False

    t = client.get(f"/api/models/{m['id']}/symbols/NCAP_COST").json()
    assert t["columns"] == ["REG", "ALLYEAR", "PRC", "CUR", "value"]
    assert t["total"] == 3
    assert [row[-1] for row in t["rows"]] == [10.0, "EPS", "INF"]

    f = client.get(f"/api/models/{m['id']}/symbols/NCAP_COST", params={"where": "PRC:P2"}).json()
    assert f["total"] == 1 and f["rows"][0][2] == "P2"
    q = client.get(f"/api/models/{m['id']}/symbols/NCAP_COST", params={"q": "2010"}).json()
    assert q["total"] == 1
    page = client.get(
        f"/api/models/{m['id']}/symbols/NCAP_COST", params={"limit": 1, "offset": 1}
    ).json()
    assert page["total"] == 3 and len(page["rows"]) == 1


def test_where_rejects_unknown_columns(client):
    m = _wait(client, _upload(client, [("demo.run", RUN)]).json()["id"])
    r = client.get(f"/api/models/{m['id']}/symbols/NCAP_COST", params={"where": 'x" OR 1=1 --:a'})
    assert r.status_code == 400


def test_no_run_file(client):
    m = _upload(client, [("base.dd", b"x")]).json()
    assert m["status"] == "error" and "no TIMES run file" in m["error"]


def test_two_run_files_then_choose(client):
    m = _upload(client, [("a.run", RUN), ("b.run", RUN), ("base.dd", b"x")]).json()
    assert m["status"] == "needs_run"
    assert m["run_candidates"] == ["a.run", "b.run"]
    r = client.post(f"/api/models/{m['id']}/run", data={"run": "b.run"})
    assert r.status_code == 200
    assert _wait(client, m["id"])["run"] == "b.run"


def test_compile_error_is_reported(client):
    m = _wait(client, _upload(client, [("broken.run", RUN)]).json()["id"])
    assert m["status"] == "error" and "DDSyntaxError" in m["error"]


def test_bad_upload_is_400(client):
    r = _upload(client, [("m.zip", b"not a zip")])
    assert r.status_code == 400


def test_graph_endpoints(client):
    m = _wait(client, _upload(client, [("demo.run", RUN)]).json()["id"])
    g = client.get(f"/api/models/{m['id']}/graph").json()
    assert g["focus"] is None and "nodes" in g and "links" in g
    assert client.get(f"/api/models/{m['id']}/graph", params={"focus": "p:NOPE"}).status_code == 404
    hits = client.get(f"/api/models/{m['id']}/search", params={"q": "P"}).json()
    assert isinstance(hits, list)


def test_export_endpoints(client):
    m = _wait(client, _upload(client, [("demo.run", RUN)]).json()["id"])
    x = client.get(f"/api/models/{m['id']}/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert 'filename="demo.xlsx"' in x.headers["content-disposition"]
    z = client.get(f"/api/models/{m['id']}/export.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"


def test_solve_and_qc(client):
    m = _wait(client, _upload(client, [("demo.run", RUN)]).json()["id"])
    before = client.get(f"/api/models/{m['id']}/qc").json()
    assert before["times_log"] is False
    r = client.post(f"/api/models/{m['id']}/solve")
    assert r.status_code == 200
    run = _wait_run(client, m["id"])
    assert run["status"] == "done" and run["result"]["objective"] == 42.0
    assert "qa_log" not in run["result"]
    qc = client.get(f"/api/models/{m['id']}/qc").json()
    assert qc["times_log"] is True and qc["counts"]["WARNING"] >= 1
    times = [i for i in qc["issues"] if i["source"] == "TIMES"]
    assert times[0]["check"] == "COM_FR does not sum to unity"


def test_steps_up_to_a_button(client):
    m = _wait(client, _upload(client, [("demo.run", RUN)]).json()["id"])
    assert client.post(f"/api/models/{m['id']}/solve", data={"until": "build"}).status_code == 200
    run = _wait_run(client, m["id"])
    assert [s["step"] for s in run["steps"]] == ["read", "preprocess", "build"]
    assert all(s["state"] == "done" and s["log"] for s in run["steps"])
    assert run["result"]["status"] == "generated" and run["solver"] == "highs"
    # all six by default
    client.post(f"/api/models/{m['id']}/solve")
    assert len(_wait_run(client, m["id"])["steps"]) == 6


def test_earlier_runs_kept_one_per_solver(tmp_path):
    runners = {s: fake_runner for s in ("highs", "cuopt", "cplex")}
    app = create_app(compiler=fake_compiler, runner=runners, root=tmp_path)
    with TestClient(app) as c:
        mid = _wait(c, _upload(c, [("demo.run", RUN)]).json()["id"])["id"]

        def run(solver, until="results"):
            c.post(f"/api/models/{mid}/solve", data={"solver": solver, "until": until})
            return _wait_run(c, mid)

        assert run("highs")["history"] == []
        h = run("cuopt")["history"]
        assert [x["solver"] for x in h] == ["highs"]
        assert h[0]["result"]["objective"] == 42.0 and len(h[0]["steps"]) == 6
        assert "log" not in h[0]["steps"][0]
        assert [x["solver"] for x in run("cplex", "build")["history"]] == ["cuopt", "highs"]
        # a solver run again takes its old column's place: at most three columns in all
        r = run("highs")
        assert r["solver"] == "highs"
        assert [x["solver"] for x in r["history"]] == ["cplex", "cuopt"]
        assert r["history"][0]["result"]["status"] == "generated"
        assert len(r["history"][0]["steps"]) == 3
        assert [x["solver"] for x in run("highs")["history"]] == ["cplex", "cuopt"]


def test_unknown_step_or_solver(client):
    m = _wait(client, _upload(client, [("demo.run", RUN)]).json()["id"])
    assert client.post(f"/api/models/{m['id']}/solve", data={"until": "nope"}).status_code == 400
    r = client.post(f"/api/models/{m['id']}/solve", data={"solver": "cplex"})
    assert r.status_code == 503  # not configured in tests


def test_health_lists_solvers(client):
    h = client.get("/api/health").json()
    assert [s["id"] for s in h["solvers"]] == ["highs", "cuopt", "cplex"]
    assert [s["available"] for s in h["solvers"]] == [True, False, False]
    assert [s["step"] for s in h["steps"]][-1] == "results"


def test_solve_needs_a_ready_model(client):
    m = _upload(client, [("base.dd", b"x")]).json()
    assert client.post(f"/api/models/{m['id']}/solve").status_code == 409


def test_demo_needs_the_timesjl_reader(client):
    # with an injected compiler there is no TIMES.jl reader to fetch TIMES_Demo from
    assert client.post("/api/models/demo").status_code == 503


def test_root_serves_app_or_says_how_to_build(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "otui" in r.text


class _FakeWorker:
    """A TIMES.jl worker stand-in: the latest TIMES.jl solves demo12 wrongly."""

    def __init__(self, channel=None, **kw):
        self.channel = channel

    def start(self):
        return self

    def info(self):
        return {"times_jl": "0.1.0", "id": 0, "ok": True}

    def demo_dir(self):
        return Path("demo")

    def solve(self, run, out, **kw):
        obj = 1.0 if self.channel == "latest" else 23960685.737007916
        return {"status": "optimal", "objective": obj}

    def compile(self, run, out, **kw):
        return {}

    def close(self):
        pass


def test_engine_falls_back_to_the_pinned_timesjl(monkeypatch):
    from otui import server

    monkeypatch.setattr(server, "JuliaWorker", _FakeWorker)
    e = server.Engine(warm="solve", channels=["latest", "pinned"])
    e._run("solve")
    assert e.state == "ready" and e.channel == "pinned"
    assert "TIMES.jl latest did not pass: demo12 gave optimal 1.0" in e.fallback
    h = e.health()
    assert h["channel"] == "pinned" and h["commit"] == server_bridge_commit()
    # a latest TIMES.jl that passes is used
    ok = server.Engine(warm="compile", channels=["latest", "pinned"])
    ok._run("compile")
    assert ok.channel == "latest" and not ok.fallback


def server_bridge_commit():
    from otui import bridge

    return bridge.pinned_commit()


def _slow_runner(gate=None):
    """A runner that solves until killed: its kill() ends the solve, as a killed worker does."""
    import threading

    stop = threading.Event()

    def run(run, out, until="results", on_event=None):
        if gate is not None:
            gate.wait(10)  # e.g. TIMES.jl still warming up
        on_event({"step": "read", "state": "running"})
        on_event({"step": "read", "state": "done", "seconds": 0.1, "log": ["read"]})
        on_event({"step": "preprocess", "state": "running"})
        if stop.wait(10):
            raise RuntimeError("the Julia worker exited")
        return {"status": "optimal", "objective": 1.0}

    run.kill = stop.set
    return run


def _wait_step(c, mid, state="running", timeout=10):
    t = time.monotonic() + timeout
    while time.monotonic() < t:
        steps = c.get(f"/api/models/{mid}").json()["solve"]["steps"]
        if any(s["state"] == state for s in steps):
            return steps
        time.sleep(0.02)
    raise AssertionError(f"no step {state}")


def test_kill_the_job(tmp_path):
    app = create_app(compiler=fake_compiler, runner={"highs": _slow_runner()}, root=tmp_path)
    with TestClient(app) as c:
        mid = _wait(c, _upload(c, [("demo.run", RUN)]).json()["id"])["id"]
        assert c.post(f"/api/models/{mid}/kill").status_code == 409  # nothing running
        c.post(f"/api/models/{mid}/solve")
        _wait_step(c, mid)
        t0 = time.monotonic()
        assert c.post(f"/api/models/{mid}/kill").status_code == 200
        r = _wait_run(c, mid)
        assert time.monotonic() - t0 < 5
        assert r["status"] == "killed" and r["error"] == ""
        states = {s["step"]: s["state"] for s in r["steps"]}
        assert states["read"] == "done" and states["preprocess"] == "killed"
        assert states["results"] == "pending"
        assert "killed by you" in r["steps"][1]["log"]
        # the killed run stays as a column beside the next one
        app.state.registry.runners["cuopt"] = fake_runner
        c.post(f"/api/models/{mid}/solve", data={"solver": "cuopt"})
        h = _wait_run(c, mid)["history"]
        assert h[0]["solver"] == "highs" and h[0]["status"] == "killed"


def test_kill_before_the_first_step(tmp_path):
    import threading

    gate = threading.Event()
    app = create_app(compiler=fake_compiler, runner={"highs": _slow_runner(gate)}, root=tmp_path)
    with TestClient(app) as c:
        mid = _wait(c, _upload(c, [("demo.run", RUN)]).json()["id"])["id"]
        c.post(f"/api/models/{mid}/solve")
        assert c.post(f"/api/models/{mid}/kill").status_code == 200  # still waiting
        gate.set()  # it would start now: stopped at its first step
        assert _wait_run(c, mid)["status"] == "killed"
