"""TIMES.jl's DD reader on TIMES_Demo demo12, through the worker.

Needs Julia and otui's Julia environment (`otui julia-setup`). Run with
`pytest -m julia`. TIMES_Demo (CC BY 4.0, IEA-ETSAP) is fetched by TIMES.jl at
its pinned commit as a content-hashed artifact; the SHA-256 values below check
the demo12 files otui tests against.
"""

import hashlib
import json
import shutil

import pytest

from otui.bridge import JuliaError, JuliaWorker

pytestmark = pytest.mark.julia

if shutil.which("julia") is None:
    pytest.skip("Julia is not installed", allow_module_level=True)

DEMO_COMMIT = "962db8603aa00850b8581e6374b4f0559c47dd52"


@pytest.fixture(scope="module")
def worker():
    with JuliaWorker() as w:
        yield w


def test_info(worker):
    info = worker.info()
    assert info["times_demo_commit"] == DEMO_COMMIT
    assert 1 <= info["threads"] <= 12


def test_demo12_compiles(worker, tmp_path):
    run = worker.demo_dir() / "demo12.run"
    assert run.exists()
    r = worker.compile(run, tmp_path)
    assert r["symbols"] > 100
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["format_version"] == 1
    names = {s["name"] for s in manifest["symbols"]}
    # core TIMES sets and parameters a demo model must have
    for name in ("REG", "PRC", "COM", "TOP", "NCAP_COST"):
        assert name in names, name
    top = next(s for s in manifest["symbols"] if s["name"] == "TOP")
    assert top["records"] > 0 and top["dimension"] == 4


def test_bad_run_file_reports_error(worker, tmp_path):
    bad = tmp_path / "bad.run"
    bad.write_text("$BATINCLUDE does_not_exist.dd\n")
    with pytest.raises(JuliaError):
        worker.compile(bad, tmp_path / "out")
    assert worker.alive


def _sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_demo12_files_unchanged(worker):
    d = worker.demo_dir()
    for name, sha in FIXTURES.items():
        assert _sha256(d / name) == sha, name


# SHA-256 of demo12's run file and the 12 DD files it includes, TIMES_Demo @ 962db86.
FIXTURES: dict[str, str] = {
    "demo12.run": "31bf7323e30ab85ef9cc927f47e5c4dea2b81046f9e6527181b45fd4fe7b6795",
    "base.dd": "e488f886ee5d517cab0fb14b6ef8ecddd35b83bba3a9720e0202223f1157a0d0",
    "bounds-uc_wsets.dd": "53d679b6752a3f3f40038301d243ab4d9b7ca8b465bf770f7aabb91f1ef26f5e",
    "dem_ref.dd": "42466bdc4c3e2eaaa17f30b92da1cd6b11778685dd736ff4f2a70da34b1ce6a2",
    "demo12_ts.dd": "88865dabecc777fea1972dcdfbd3bb1eee2c970a2e942d60d77c9129f7ee6d87",
    "demproj_dtcar.dd": "598d259ee8747baf8032270bc74537ddc62261c09fb4252cf73507b39497e9ab",
    "newtechs.dd": "9c1d4e921260e724475e839548973fd36ef2092eea0e8935eaa88bee90796b91",
    "peak_rsv.dd": "552bcfdefa6ffdbe4dfa974a3fef95119abdcac75fb0e5607648362625bcf9ec",
    "refinery.dd": "9f47ccd14d0fbf971f6eb1eb3084afbdfc78f92232e311f95f658853498e6c1e",
    "syssettings.dd": "0e43c1494803517b89350c49162759eeed2870b18cd52b980634d5ecee37d539",
    "trade_param.dd": "7fbbf12506e9b9c5935d6ea074132b55d2a58fc983fff9f3f0b1065240ff12c0",
    "uc_nuc_maxcap.dd": "e1f155d9e1d2f7a7e25710bd2de1c34c82830f880d5f0e43501060cb8d2e8455",
}


def test_demo12_loads_with_d4_headers(worker, tmp_path):
    from otui.store import load_dump

    worker.compile(worker.demo_dir() / "demo12.run", tmp_path)
    m = load_dump(tmp_path)
    assert len(m.tables) > 100
    cost = m.df("NCAP_COST")
    assert list(cost.columns) == ["REG", "ALLYEAR", "PRC", "CUR", "value", "value__sv"]
    top = m.df("TOP")
    assert list(top.columns)[:4] == ["REG", "PRC", "COM", "IN_OUT"]
    assert len(top) == m.symbols["TOP"].records
    assert list(m.df("PRC").columns)[0] == "PRC"


def test_api_end_to_end_demo12(tmp_path):
    """Upload demo12's run and DD files to the API; TIMES.jl compiles; browse a table."""
    import time

    from fastapi.testclient import TestClient

    from otui.server import create_app

    with TestClient(create_app(root=tmp_path / "ws", warm=False)) as c:
        t = time.monotonic()
        while c.get("/api/health").json()["engine"] not in ("ready", "error"):
            assert time.monotonic() - t < 600
            time.sleep(0.5)
        assert c.get("/api/health").json()["engine"] == "ready"
        demo = c.app.state.engine.worker.demo_dir()  # the reader worker
        names = ["demo12.run", *FIXTURES]
        files = [
            ("files", (n, (demo / n).read_bytes(), "text/plain")) for n in dict.fromkeys(names)
        ]
        mid = c.post("/api/models", files=files).json()["id"]
        while (m := c.get(f"/api/models/{mid}").json())["status"] in ("compiling", "loading"):
            assert time.monotonic() - t < 600
            time.sleep(0.2)
        assert m["status"] == "ready", m["error"]
        assert m["summary"]["counts"]["PRC"] == 168
        r = c.get(f"/api/models/{mid}/symbols/TOP", params={"where": "PRC:ELCNENUC00"}).json()
        assert r["total"] == 4
        g = c.get(f"/api/models/{mid}/graph").json()
        # TIMES's ACT is not drawn, nor VEDA's dummy imports and their 62 links
        assert len(g["nodes"]) == 234 and len(g["links"]) == 371
        assert g["dummies"] == ["IMPDEMZ", "IMPMATZ", "IMPNRGZ"]
        assert g["flags"] == {}
        assert g["centre"] == "c:ELC" and g["reach"][-1] == 234  # all within reach of ELC
        dm = c.get(f"/api/models/{mid}/dummies").json()
        assert dm == {"processes": ["IMPDEMZ", "IMPMATZ", "IMPNRGZ"], "solved": False}
        ego = c.get(f"/api/models/{mid}/graph", params={"focus": "p:ELCNENUC00"}).json()
        assert 10 < len(ego["nodes"]) < 234
        x = c.get(f"/api/models/{mid}/export.xlsx")
        assert x.status_code == 200
        assert 'filename="demo12.xlsx"' in x.headers["content-disposition"]
        z = c.get(f"/api/models/{mid}/export.zip")
        assert z.status_code == 200 and z.content[:2] == b"PK"

        # run on TIMES.jl + HiGHS; GAMS + CPLEX gives 23960685.737007827 for demo12
        # (TIMES.jl reference, TIMES_Demo-demo12/as_run, GAMS 54.6.1)
        assert c.post(f"/api/models/{mid}/solve").status_code == 200
        while (m := c.get(f"/api/models/{mid}").json())["solve"]["status"] == "running":
            assert time.monotonic() - t < 1800
            time.sleep(1)
        run = m["solve"]
        assert run["status"] == "done", run["error"]
        res = run["result"]
        assert res["status"] == "optimal"
        assert abs(res["objective"] - 23960685.737007827) <= 1e-6 * 23960685.737007827
        assert (res["rows"], res["columns"]) == (3233, 4485)
        qc = c.get(f"/api/models/{mid}/qc").json()
        times = [i for i in qc["issues"] if i["source"] == "TIMES"]
        assert qc["times_log"] and len(times) == 2  # COM_FR normalised in REG1 and REG2
        assert qc["counts"] == {"WARNING": 2}  # and no otui structure issue
        dm = c.get(f"/api/models/{mid}/dummies").json()
        assert dm["solved"] and not dm["used"]  # a sound model takes nothing from them
